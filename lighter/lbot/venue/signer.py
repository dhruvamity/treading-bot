"""Transaction signing through Lighter's official signer library (Go, built for each platform and shipped inside the
`lighter-sdk` wheel). Lighter's transactions are signed with a Schnorr scheme over a Goldilocks field with Poseidon2
hashing; this module only calls that library, it does not re-implement it.

The library is found at LIGHTER_SIGNER_LIB, else in the installed lighter-sdk package (located without importing it).
Every call passes an explicit nonce with SkipNonce on (lbot/venue/nonce.py), so the library never goes to the network.
"""

from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lbot.venue import consts as C

_LIBS = {
    ("Darwin", "arm64"): "lighter-signer-darwin-arm64.dylib",
    ("Darwin", "x86_64"): "lighter-signer-darwin-amd64.dylib",
    ("Linux", "x86_64"): "lighter-signer-linux-amd64.so",
    ("Linux", "aarch64"): "lighter-signer-linux-arm64.so",
    ("Linux", "arm64"): "lighter-signer-linux-arm64.so",
    ("Windows", "AMD64"): "lighter-signer-windows-amd64.dll",
}


class SignerError(RuntimeError):
    pass


class _Signed(ctypes.Structure):
    _fields_ = [("txType", ctypes.c_uint8), ("txInfo", ctypes.c_void_p), ("txHash", ctypes.c_void_p),
                ("messageToSign", ctypes.c_void_p), ("err", ctypes.c_void_p)]


class _StrOrErr(ctypes.Structure):
    _fields_ = [("str", ctypes.c_void_p), ("err", ctypes.c_void_p)]


class _KeyPair(ctypes.Structure):
    _fields_ = [("privateKey", ctypes.c_void_p), ("publicKey", ctypes.c_void_p), ("err", ctypes.c_void_p)]


def library_path() -> Path:
    env = os.environ.get("LIGHTER_SIGNER_LIB")
    if env:
        return Path(env)
    name = _LIBS.get((platform.system(), platform.machine()))
    if name is None:
        raise SignerError(f"no Lighter signer for {platform.system()}/{platform.machine()}")
    spec = importlib.util.find_spec("lighter")
    if spec is None or not spec.submodule_search_locations:
        raise SignerError("the lighter-sdk package is not installed (it ships the signer): make install")
    return Path(next(iter(spec.submodule_search_locations))) / "signers" / name


@dataclass(frozen=True)
class SignedTx:
    tx_type: int
    tx_info: str        # JSON, sent as is
    tx_hash: str


_lib: Any = None


def _load() -> Any:
    global _lib
    if _lib is not None:
        return _lib
    p = library_path()
    if not p.exists():
        raise SignerError(f"signer library missing: {p}")
    lib = ctypes.CDLL(str(p))
    L, N, U8, P = ctypes.c_longlong, ctypes.c_int, ctypes.c_uint8, ctypes.c_char_p
    lib.CreateClient.argtypes = [P, P, N, N, L]
    lib.CreateClient.restype = ctypes.c_void_p
    lib.CheckClient.argtypes = [N, L]
    lib.CheckClient.restype = ctypes.c_void_p
    lib.SignCreateOrder.argtypes = [N, L, L, N, N, N, N, N, N, L, L, N, N, U8, U8, U8, L, N, L]
    lib.SignCreateOrder.restype = _Signed
    lib.SignModifyOrder.argtypes = [N, L, L, L, L, L, N, N, U8, U8, U8, L, L, N, L]
    lib.SignModifyOrder.restype = _Signed
    lib.SignCancelOrder.argtypes = [N, L, U8, L, N, L]
    lib.SignCancelOrder.restype = _Signed
    lib.SignCancelAllOrders.argtypes = [N, L, N, U8, L, N, L]
    lib.SignCancelAllOrders.restype = _Signed
    lib.SignUpdateLeverage.argtypes = [N, N, N, U8, L, N, L]
    lib.SignUpdateLeverage.restype = _Signed
    lib.CreateAuthToken.argtypes = [L, N, L]
    lib.CreateAuthToken.restype = _StrOrErr
    lib.GenerateAPIKey.argtypes = []
    lib.GenerateAPIKey.restype = _KeyPair
    lib.Free.argtypes = [ctypes.c_void_p]
    lib.Free.restype = None
    _lib = lib
    return lib


def _take(ptr: int | None) -> str | None:
    """Read a C string the library allocated, then free it with the library's own allocator."""
    if not ptr:
        return None
    try:
        v = ctypes.cast(ptr, ctypes.c_char_p).value
        return v.decode() if v is not None else None
    finally:
        _lib.Free(ptr)


def _signed(r: _Signed) -> SignedTx:
    err = _take(r.err)
    info = _take(r.txInfo)
    h = _take(r.txHash)
    _take(r.messageToSign)
    if err:
        raise SignerError(err)
    if not info:
        raise SignerError("the signer returned no transaction")
    return SignedTx(int(r.txType), info, h or "")


def generate_key() -> tuple[str, str]:
    """A new API key pair (private, public), made locally. Registering the public key with an account is a separate,
    wallet-signed step (the owner's)."""
    lib = _load()
    r = lib.GenerateAPIKey()
    priv, pub, err = _take(r.privateKey), _take(r.publicKey), _take(r.err)
    if err or not priv or not pub:
        raise SignerError(err or "no key")
    return priv, pub


class Signer:
    """Signs for one account with one API key. Nonces come from the caller."""

    def __init__(self, url: str, private_key: str, chain_id: int, api_key_index: int, account_index: int) -> None:
        if api_key_index in C.RESERVED_KEY_SLOTS:
            raise SignerError(f"API key slot {api_key_index} belongs to the Lighter apps; use 4-254")
        self.lib = _load()
        self.key_index = api_key_index
        self.account = account_index
        err = _take(self.lib.CreateClient(url.encode(), private_key.removeprefix("0x").encode(), chain_id,
                                          api_key_index, account_index))
        if err:
            raise SignerError(err)

    def check(self) -> str | None:
        """None if Lighter holds this key's public key for the account (a read over the network), else why not."""
        return _take(self.lib.CheckClient(self.key_index, self.account))

    def create_order(self, *, market: int, client_index: int, size: int, price: int, is_ask: bool, order_type: int,
                     tif: int, reduce_only: bool, expiry: int, nonce: int) -> SignedTx:
        return _signed(self.lib.SignCreateOrder(market, client_index, size, price, int(is_ask), order_type, tif,
                                                int(reduce_only), 0, expiry, 0, 0, 0, 0, 0, 1, nonce, self.key_index,
                                                self.account))

    def modify_order(self, *, market: int, index: int, size: int, price: int, nonce: int) -> SignedTx:
        """`index`: the order's client index or Lighter's order index (both are accepted)."""
        return _signed(self.lib.SignModifyOrder(market, index, size, price, 0, 0, 0, 0, 0, 0, 1, nonce, 0,
                                                self.key_index, self.account))

    def cancel_order(self, *, market: int, index: int, nonce: int) -> SignedTx:
        return _signed(self.lib.SignCancelOrder(market, index, 1, nonce, self.key_index, self.account))

    def cancel_all(self, *, tif: int, time_ms: int, nonce: int, market: int = C.NIL_MARKET) -> SignedTx:
        return _signed(self.lib.SignCancelAllOrders(tif, time_ms, market, 1, nonce, self.key_index, self.account))

    def update_leverage(self, *, market: int, fraction: int, nonce: int, margin_mode: int = C.CROSS_MARGIN) -> SignedTx:
        return _signed(self.lib.SignUpdateLeverage(market, fraction, margin_mode, 1, nonce, self.key_index,
                                                   self.account))

    def auth_token(self, deadline_s: int) -> str:
        """An auth token valid until the unix time `deadline_s` (Lighter allows at most 8 hours ahead)."""
        r = self.lib.CreateAuthToken(deadline_s, self.key_index, self.account)
        tok, err = _take(r.str), _take(r.err)
        if err or not tok:
            raise SignerError(err or "no auth token")
        return tok


def tx_fields(tx: SignedTx) -> dict[str, Any]:
    """The signed transaction's fields (for logs and tests)."""
    return json.loads(tx.tx_info)
