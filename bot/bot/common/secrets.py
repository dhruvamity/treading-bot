"""Encrypted secrets store (B3.9).

`config/secrets.enc` holds a JSON map of secret name -> value, encrypted with Fernet under a key derived by
scrypt from the BOT_SECRETS_PASSWORD environment variable. The file is written 0600 and never committed.

The normal place for credentials is `.env` (gitignored, 0600), loaded at CLI start. The encrypted store is optional
(for hosts where a plain-text file is not acceptable): resolution order is encrypted store, then the environment.
Every loaded value is registered with the log redactor.

The wallet (L1) private keys are NOT stored here: owner-machine scripts prompt for them (A2.7).
"""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path

import orjson
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from bot.common.errors import SecretsError
from bot.common.logging import register_secret

PASSWORD_ENV = "BOT_SECRETS_PASSWORD"
DEFAULT_PATH = Path("config/secrets.enc")
_SCRYPT_N = 2**15

# Names the code reads. Kept here so `secrets status` can report what is missing without printing values.
# Everything else (which subaccount a key trades, when it expires, the Lighter account index) is looked up from
# the venues at start-up, so the owner only supplies what cannot be discovered.
KNOWN_SECRETS: dict[str, str] = {
    "ARCUS_ADDRESS": "Arcus wallet address (0x...) that owns the API key",
    "ARCUS_API_PRIVATE_KEY": "Arcus API private key (64 hex); its subaccount and expiry are read from the venue",
    "LIGHTER_ADDRESS": "Lighter RH wallet address (0x...)",
    "LIGHTER_API_PRIVATE_KEY": "Lighter RH API private key (hex)",
    "LIGHTER_API_KEY_INDEX": "Lighter RH API key slot (optional, default 4; 0-3 and 157 are reserved)",
    "LIGHTER_ACCOUNT_INDEX": "Lighter RH account index (optional, found from LIGHTER_ADDRESS)",
    "TELEGRAM_BOT_TOKEN": "Telegram bot token for alerts (optional)",
    "TELEGRAM_CHAT_ID": "Telegram chat id for alerts (optional)",
    "ARCUS_TESTNET_ADDRESS": "Arcus testnet wallet address (testnet runs only)",
    "ARCUS_TESTNET_API_PRIVATE_KEY": "Arcus testnet API private key (testnet runs only)",
    "LIGHTER_TESTNET_ADDRESS": "Lighter RH testnet wallet address (testnet runs only)",
    "LIGHTER_TESTNET_API_PRIVATE_KEY": "Lighter RH testnet API private key (testnet runs only)",
}


# Other names owners have used for the same value (the Arcus web app calls it the wallet address).
ALIASES: dict[str, str] = {"ARCUS_ADDRESS": "ARCUS_WALLET_ADDRESS"}


def _kdf(password: str, salt: bytes) -> bytes:
    raw = Scrypt(salt=salt, length=32, n=_SCRYPT_N, r=8, p=1).derive(password.encode())
    return base64.urlsafe_b64encode(raw)


class SecretStore:
    def __init__(self, path: Path | str = DEFAULT_PATH, password: str | None = None) -> None:
        self.path = Path(path)
        self._password = password if password is not None else os.environ.get(PASSWORD_ENV)
        self._data: dict[str, str] | None = None

    # ---- file format ------------------------------------------------------------------------------
    def _load(self) -> dict[str, str]:
        if self._data is not None:
            return self._data
        if not self.path.exists():
            self._data = {}
            return self._data
        if not self._password:
            raise SecretsError(f"{self.path} exists but {PASSWORD_ENV} is not set")
        blob = orjson.loads(self.path.read_bytes())
        salt = base64.b64decode(blob["salt"])
        try:
            plain = Fernet(_kdf(self._password, salt)).decrypt(blob["token"].encode())
        except InvalidToken as e:
            raise SecretsError("wrong secrets password or corrupted secrets file") from e
        data = orjson.loads(plain)
        for v in data.values():
            register_secret(str(v))
        self._data = data
        return data

    def _save(self, data: dict[str, str]) -> None:
        if not self._password:
            raise SecretsError(f"set {PASSWORD_ENV} before writing secrets")
        salt = os.urandom(16)
        token = Fernet(_kdf(self._password, salt)).encrypt(orjson.dumps(data)).decode()
        blob = orjson.dumps({"v": 1, "kdf": "scrypt", "n": _SCRYPT_N, "salt": base64.b64encode(salt).decode(),
                             "token": token})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(blob)
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        tmp.replace(self.path)
        self._data = data

    # ---- API --------------------------------------------------------------------------------------
    def init(self) -> None:
        if self.path.exists():
            raise SecretsError(f"{self.path} already exists")
        self._save({})

    def set(self, name: str, value: str) -> None:
        data = dict(self._load())
        data[name] = value
        register_secret(value)
        self._save(data)

    def delete(self, name: str) -> None:
        data = dict(self._load())
        data.pop(name, None)
        self._save(data)

    def get(self, name: str, default: str | None = None) -> str | None:
        v = self._load().get(name)
        if v is None:
            v = os.environ.get(name)
            if v:
                register_secret(v)
        if not v and name in ALIASES:
            return self.get(ALIASES[name], default)
        return v if v is not None else default

    def require(self, name: str) -> str:
        v = self.get(name)
        if not v:
            raise SecretsError(f"missing secret {name}: {KNOWN_SECRETS.get(name, '')}".rstrip(": "))
        return v

    def source(self, name: str) -> str:
        if name in self._load():
            return "store"
        return "env" if os.environ.get(name) else "missing"

    def names(self) -> list[str]:
        return sorted(self._load())

    def names_with_prefix(self, prefix: str) -> list[str]:
        """Every secret name (store or environment) starting with `prefix` that has a value."""
        return sorted({n for n in self._load() if n.startswith(prefix)}
                      | {n for n, v in os.environ.items() if n.startswith(prefix) and v})

    def import_env(self, names: list[str] | None = None) -> list[str]:
        data = dict(self._load())
        moved = []
        for n in names or list(KNOWN_SECRETS):
            v = os.environ.get(n)
            if v:
                data[n] = v
                moved.append(n)
        if moved:
            self._save(data)
        return moved

    def status(self) -> dict[str, str]:
        return {n: self.source(n) for n in KNOWN_SECRETS}


def load_dotenv(path: Path | str = ".env") -> list[str]:
    """Minimal .env loader (KEY=VALUE lines, # comments). Never overrides variables already in the environment.
    Returns the names it set. Empty values are skipped so a copied template does not shadow the secrets store."""
    p = Path(path)
    if not p.exists():
        return []
    set_names = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v
            set_names.append(k)
    return set_names


def upsert_dotenv(name: str, value: str, path: Path | str = ".env") -> Path:
    """Set NAME=value in a .env file (created 0600 if missing, other lines kept). Used by the key-registration
    scripts so the owner never copies a key by hand."""
    p = Path(path)
    lines = p.read_text().splitlines() if p.exists() else []
    out, done = [], False
    for ln in lines:
        if ln.split("=", 1)[0].strip() == name and not ln.lstrip().startswith("#"):
            out.append(f"{name}={value}")
            done = True
        else:
            out.append(ln)
    if not done:
        out.append(f"{name}={value}")
    if not p.exists():
        os.close(os.open(p, os.O_WRONLY | os.O_CREAT, 0o600))
    p.write_text("\n".join(out) + "\n")
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    register_secret(value)
    return p


def mask(value: str | None) -> str:
    """Never reveals any character of the value, only whether it is set and its length."""
    return "∅" if not value else f"set ({len(value)} chars)"
