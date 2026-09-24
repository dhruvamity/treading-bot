"""Lighter RH transaction signer: a thin wrapper over the OFFICIAL lighter-sdk signer binary.

Choice (P1 task 5): lighter-sdk 1.1.4 ships the Go signer as a shared library for linux/darwin (amd64/arm64)
and natively supports the RH chain IDs (466324 mainnet, 300 testnet; see lighter/signer_client.py). We call the
library directly instead of using `SignerClient`, so that nonces, transport and rate budgets stay under our
control (the SDK's client would manage nonces and send on its own).

Nonces: per API key. Default mode uses the documented SkipNonce attribute with millisecond time-based nonces
(max(now_ms, last+1), persisted), which survives restarts without a /nextNonce read; `2^47-1 > nonce > last`
must hold (docs: signing-transactions).
"""

from __future__ import annotations

import ctypes
import json
from dataclasses import dataclass
from typing import Any

from bot.common.errors import AuthError

NIL_TRIGGER_PRICE = 0
NIL_MARKET_INDEX = 255
DEFAULT_28_DAY_ORDER_EXPIRY = -1
IOC_EXPIRY = 0
ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET = 0, 1
TIF_IOC, TIF_GTT, TIF_POST_ONLY = 0, 1, 2
CANCEL_ALL_IMMEDIATE, CANCEL_ALL_SCHEDULED, CANCEL_ALL_ABORT = 0, 1, 2
CROSS_MARGIN, ISOLATED_MARGIN = 0, 1
# Self-trade prevention: expire the maker; compare by MASTER account so our own subaccounts never match.
STP_EXPIRE_MAKER = 0
STP_EQUALITY_MASTER = 1
SKIP_NONCE_ON = 1


@dataclass(frozen=True, slots=True)
class SignedTx:
    tx_type: int
    tx_info: str
    tx_hash: str

    def info(self) -> dict[str, Any]:
        return dict(json.loads(self.tx_info))


def _lib() -> Any:
    from lighter import signer_client as sc  # heavy import: only when signing is actually used

    return sc.get_signer(), sc.decode_and_free  # type: ignore[no-untyped-call]


def generate_api_key() -> tuple[str, str]:
    """(private_key_hex, public_key_hex) from the official signer. Owner machine only."""
    lib, dfree = _lib()
    r = lib.GenerateAPIKey()
    priv, pub, err = dfree(r.privateKey), dfree(r.publicKey), dfree(r.err)
    if err:
        raise AuthError("lighter_rh", f"GenerateAPIKey failed: {err}")
    return priv, pub


class LighterSigner:
    def __init__(self, *, url: str, chain_id: int, account_index: int, api_key_index: int, private_key_hex: str,
                 reserved_indices: tuple[int, ...] = (0, 1, 2, 3, 157)) -> None:
        if api_key_index in reserved_indices:
            raise AuthError("lighter_rh", f"api_key_index {api_key_index} is reserved by Lighter's own interfaces")
        if not 0 <= api_key_index <= 254:
            raise AuthError("lighter_rh", "api_key_index must be 0..254")
        self.url = url
        self.chain_id = chain_id
        self.account_index = account_index
        self.api_key_index = api_key_index
        self._lib, self._free = _lib()
        err = self._free(self._lib.CreateClient(url.encode(), private_key_hex.removeprefix("0x").encode(), chain_id,
                                                api_key_index, account_index))
        if err:
            raise AuthError("lighter_rh", f"CreateClient failed: {err}")

    def __repr__(self) -> str:
        return f"LighterSigner(account={self.account_index}, key_index={self.api_key_index}, chain={self.chain_id})"

    def _decode(self, r: Any) -> SignedTx:
        err = self._free(r.err)
        info = self._free(r.txInfo)
        tx_hash = self._free(r.txHash)
        self._free(r.messageToSign)
        if err:
            raise AuthError("lighter_rh", f"sign failed: {err}")
        return SignedTx(int(r.txType), info or "", tx_hash or "")

    def check_client(self) -> str | None:
        """None when the registered public key for (account, key index) matches ours (network call in signer)."""
        r: str | None = self._free(self._lib.CheckClient(self.api_key_index, self.account_index))
        return r

    def create_auth_token(self, deadline_unix_s: int) -> str:
        r = self._lib.CreateAuthToken(deadline_unix_s, self.api_key_index, self.account_index)
        tok, err = self._free(r.str), self._free(r.err)
        if err or not tok:
            raise AuthError("lighter_rh", f"auth token failed: {err}")
        return str(tok)

    def create_order(self, *, market_index: int, client_order_index: int, base_amount: int, price: int, is_ask: bool,
                     order_type: int, time_in_force: int, reduce_only: bool, order_expiry: int, nonce: int,
                     trigger_price: int = NIL_TRIGGER_PRICE, skip_nonce: int = SKIP_NONCE_ON) -> SignedTx:
        return self._decode(self._lib.SignCreateOrder(
            market_index, client_order_index, base_amount, price, int(is_ask), order_type, time_in_force,
            int(reduce_only), trigger_price, order_expiry,
            0, 0, 0,  # integrator account / taker fee / maker fee: none (standard accounts may not set them)
            STP_EXPIRE_MAKER, STP_EQUALITY_MASTER,
            skip_nonce, nonce, self.api_key_index, self.account_index))

    def cancel_order(self, *, market_index: int, order_index: int, nonce: int,
                     skip_nonce: int = SKIP_NONCE_ON) -> SignedTx:
        return self._decode(self._lib.SignCancelOrder(market_index, order_index, skip_nonce, nonce,
                                                      self.api_key_index, self.account_index))

    def cancel_all(self, *, time_in_force: int, time_ms: int, nonce: int, market_index: int = NIL_MARKET_INDEX,
                   skip_nonce: int = SKIP_NONCE_ON) -> SignedTx:
        """IMMEDIATE cancels now; SCHEDULED arms a cancel-all at `time_ms` (Lighter dead man's switch);
        ABORT disarms a scheduled one."""
        return self._decode(self._lib.SignCancelAllOrders(time_in_force, time_ms, market_index, skip_nonce, nonce,
                                                          self.api_key_index, self.account_index))

    def modify_order(self, *, market_index: int, order_index: int, base_amount: int, price: int, nonce: int,
                     trigger_price: int = NIL_TRIGGER_PRICE, order_version: int = 0,
                     skip_nonce: int = SKIP_NONCE_ON) -> SignedTx:
        return self._decode(self._lib.SignModifyOrder(
            market_index, order_index, base_amount, price, trigger_price, 0, 0, 0,
            STP_EXPIRE_MAKER, STP_EQUALITY_MASTER, skip_nonce, nonce, order_version,
            self.api_key_index, self.account_index))

    def update_leverage(self, *, market_index: int, imf_fraction: int, margin_mode: int, nonce: int,
                        skip_nonce: int = SKIP_NONCE_ON) -> SignedTx:
        """imf_fraction in 1/10,000 (e.g. 3x -> 3334)."""
        return self._decode(self._lib.SignUpdateLeverage(market_index, imf_fraction, margin_mode, skip_nonce, nonce,
                                                         self.api_key_index, self.account_index))

    def change_pub_key_unsigned(self, *, new_pubkey_hex: str, nonce: int) -> tuple[SignedTx, str]:
        """Returns the L2-signed tx and the message the WALLET must sign (EIP-191). Owner machine only."""
        r = self._lib.SignChangePubKey(ctypes.c_char_p(new_pubkey_hex.encode()), 0, nonce, self.api_key_index,
                                       self.account_index)
        err = self._free(r.err)
        info = self._free(r.txInfo)
        tx_hash = self._free(r.txHash)
        msg = self._free(r.messageToSign)
        if err:
            raise AuthError("lighter_rh", f"change pub key sign failed: {err}")
        return SignedTx(int(r.txType), info or "", tx_hash or ""), msg or ""


def leverage_to_imf_fraction(leverage: float) -> int:
    """Lighter margin fractions are in 1/10,000; 3x -> ceil(10000/3) = 3334."""
    import math

    return math.ceil(10_000 / leverage)
