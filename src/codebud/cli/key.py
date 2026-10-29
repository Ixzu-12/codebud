"""
codebud/cli/key.py

codebud key: the encryption key for data at rest (crypto.py).

  key init     create the key, sealed with a new passphrase
  key passwd   change the passphrase (the key, and so the data, stay)
  key status   whether encryption is available and the key exists
"""

import getpass
import sys

from codebud import crypto
from codebud.cli.display import _OUT_BOLD, _OUT_DIM, _OUT_RESET


def _fail(message: str) -> None:
    print(f"codebud: {message}", file=sys.stderr)
    sys.exit(1)


def _new_passphrase() -> str:
    """CODEBUD_PASSPHRASE when set, otherwise typed twice on the terminal."""
    from_env = crypto.passphrase_from_env()
    if from_env is not None:
        print(f"Using the passphrase in {crypto.PASSPHRASE_ENV}.")
        return from_env
    first = getpass.getpass("New passphrase: ")
    if not first:
        _fail("the passphrase must not be empty")
    if getpass.getpass("Repeat it: ") != first:
        _fail("the passphrases do not match; nothing was changed")
    return first


def _init() -> None:
    if crypto.has_key():
        _fail(f"an encryption key already exists in {crypto.key_dir()}")
    passphrase = _new_passphrase()
    try:
        recipient = crypto.create_key(passphrase)
    except crypto.CryptoError as exc:
        _fail(str(exc))
    print(f"Created the encryption key in {crypto.key_dir()}")
    print(f"  {_OUT_DIM}public key{_OUT_RESET} {recipient}")
    print(
        "Keep the passphrase safe: without it, data encrypted to this key "
        "cannot be recovered by anyone, codebud included."
    )


def _passwd() -> None:
    if not crypto.has_key():
        _fail("no encryption key yet; create one with: codebud key init")
    old = getpass.getpass("Current passphrase: ")
    try:
        crypto.unlock(old)  # check it before asking for the new one
    except crypto.CryptoError as exc:
        _fail(str(exc))
    first = getpass.getpass("New passphrase: ")
    if getpass.getpass("Repeat it: ") != first:
        _fail("the passphrases do not match; nothing was changed")
    try:
        crypto.change_passphrase(old, first)
    except crypto.CryptoError as exc:
        _fail(str(exc))
    print("Changed the passphrase; the key and your encrypted data are unchanged.")


def _status() -> None:
    print(f"{_OUT_BOLD}Encryption at rest{_OUT_RESET}")
    if not crypto.available():
        print("  not available: install pyrage (pip install 'codebud[crypto]')")
        return
    if not crypto.has_key():
        print(f"  no key yet ({crypto.key_dir()}); create one with: codebud key init")
        return
    print(f"  {_OUT_DIM}key        {_OUT_RESET} {crypto.key_dir()}")
    print(f"  {_OUT_DIM}public key {_OUT_RESET} {crypto.recipient()}")
    if crypto.passphrase_from_env() is not None:
        print(f"  {_OUT_DIM}passphrase {_OUT_RESET} from {crypto.PASSPHRASE_ENV} (unattended)")
    else:
        print(f"  {_OUT_DIM}passphrase {_OUT_RESET} asked on the terminal when needed")


def cmd_key(args):
    if args.key_action != "status" and not crypto.available():
        _fail("encryption needs the pyrage module: pip install 'codebud[crypto]'")
    {"init": _init, "passwd": _passwd, "status": _status}[args.key_action]()
