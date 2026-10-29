"""
codebud/crypto.py

Encryption at rest with age (phase 10, D14b: age with a passphrase).

A passphrase protects one age X25519 key, kept in the config directory
($XDG_CONFIG_HOME/codebud/keys), never beside the data it encrypts:

    keys/identity.age   the secret key, encrypted with the passphrase
    keys/recipient.txt  the public key (age1...)

Writing needs only the public key, so codebud can save data without
asking for the passphrase; reading needs the key unlocked, which costs
one passphrase (scrypt, about a second) per run rather than one per file.
The unlocked key lives only in this process's memory.

Whole files (a session) are ordinary age files. Append-only files (the
execution history, the evidence log) cannot be appended to as one age
file, so each line is encrypted on its own and stored as
``age:<base64>`` -- still one line, still appendable.

If the passphrase is lost, the data cannot be recovered: that is the point.

The optional dependency pyrage provides age (pip install 'codebud[crypto]').
The age command-line tool is not used: it reads passphrases only from a
terminal.
"""

import base64
import binascii
import os
from pathlib import Path

from codebud import paths
from codebud.fsutil import atomic_write_bytes, private_dir

IDENTITY_FILE = "identity.age"
RECIPIENT_FILE = "recipient.txt"
LINE_PREFIX = "age:"
_MAGIC = b"age-encryption.org/v1\n"

_identity = None  # the unlocked secret key, for this process only


class CryptoError(Exception):
    """Encryption that is unavailable, locked, or fails."""


def _pyrage():
    try:
        import pyrage
    except ImportError as exc:
        raise CryptoError(
            "encryption needs the pyrage module: pip install 'codebud[crypto]'"
        ) from exc
    return pyrage


def available() -> bool:
    """Whether age encryption (pyrage) is installed."""
    try:
        _pyrage()
    except CryptoError:
        return False
    return True


def key_dir() -> Path:
    return paths.config_dir() / "keys"


def has_key() -> bool:
    return (key_dir() / IDENTITY_FILE).is_file() and (key_dir() / RECIPIENT_FILE).is_file()


def _require_key() -> None:
    if not has_key():
        raise CryptoError(f"no encryption key yet in {key_dir()}")


def create_key(passphrase: str) -> str:
    """Generate the key pair, seal it with ``passphrase``; return the public key.

    Refuses to replace an existing key: data encrypted to it would be lost.
    The new key is left unlocked.
    """
    global _identity
    pyrage = _pyrage()
    if not passphrase:
        raise CryptoError("the passphrase must not be empty")
    if (key_dir() / IDENTITY_FILE).exists() or (key_dir() / RECIPIENT_FILE).exists():
        raise CryptoError(f"an encryption key already exists in {key_dir()}")
    identity = pyrage.x25519.Identity.generate()
    recipient = str(identity.to_public())
    private_dir(key_dir())
    atomic_write_bytes(
        key_dir() / IDENTITY_FILE, pyrage.passphrase.encrypt(str(identity).encode(), passphrase)
    )
    atomic_write_bytes(key_dir() / RECIPIENT_FILE, (recipient + "\n").encode())
    _identity = identity
    return recipient


def recipient() -> str:
    """The public key data is encrypted to."""
    _require_key()
    return (key_dir() / RECIPIENT_FILE).read_text().strip()


def _open_identity(passphrase: str):
    pyrage = _pyrage()
    _require_key()
    sealed = (key_dir() / IDENTITY_FILE).read_bytes()
    try:
        secret = pyrage.passphrase.decrypt(sealed, passphrase)
    except pyrage.DecryptError as exc:
        raise CryptoError("wrong passphrase (or a damaged key file)") from exc
    return pyrage.x25519.Identity.from_str(secret.decode())


def unlock(passphrase: str) -> None:
    """Unlock the secret key for this process."""
    global _identity
    _identity = _open_identity(passphrase)


def lock() -> None:
    """Forget the unlocked key."""
    global _identity
    _identity = None


def is_unlocked() -> bool:
    return _identity is not None


def change_passphrase(old: str, new: str) -> None:
    """Re-seal the same key with a new passphrase; the data stays readable."""
    if not new:
        raise CryptoError("the passphrase must not be empty")
    identity = _open_identity(old)
    sealed = _pyrage().passphrase.encrypt(str(identity).encode(), new)
    atomic_write_bytes(key_dir() / IDENTITY_FILE, sealed)


def is_encrypted(data: bytes) -> bool:
    return data.startswith(_MAGIC)


def encrypt(data: bytes) -> bytes:
    """Encrypt ``data`` to the public key; no passphrase needed."""
    pyrage = _pyrage()
    public = pyrage.x25519.Recipient.from_str(recipient())
    return pyrage.encrypt(data, [public])


def decrypt(data: bytes) -> bytes:
    """Decrypt ``data`` with the unlocked key."""
    pyrage = _pyrage()
    if _identity is None:
        raise CryptoError("the encryption key is locked; unlock it with the passphrase first")
    try:
        return pyrage.decrypt(data, [_identity])
    except pyrage.DecryptError as exc:
        raise CryptoError(f"cannot decrypt: {exc}") from exc


def is_encrypted_line(line: str) -> bool:
    return line.startswith(LINE_PREFIX)


def encrypt_line(text: str) -> str:
    """Encrypt one line of an append-only file; the result is one line."""
    return LINE_PREFIX + base64.b64encode(encrypt(text.encode())).decode("ascii")


def decrypt_line(line: str) -> str:
    if not is_encrypted_line(line):
        raise CryptoError("not an encrypted line")
    try:
        sealed = base64.b64decode(line[len(LINE_PREFIX) :].strip(), validate=True)
    except binascii.Error as exc:
        raise CryptoError(f"damaged encrypted line: {exc}") from exc
    return decrypt(sealed).decode()


def write_file(path, data: bytes) -> None:
    """Encrypt ``data`` and replace ``path`` with it, atomically, 0600."""
    atomic_write_bytes(path, encrypt(data))


def read_file(path) -> bytes:
    return decrypt(Path(os.fspath(path)).read_bytes())
