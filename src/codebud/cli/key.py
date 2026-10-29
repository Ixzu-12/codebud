"""
codebud/cli/key.py

codebud key: the encryption key for data at rest (crypto.py).

  key init     create the key, sealed with a new passphrase
  key passwd   change the passphrase (the key, and so the data, stay)
  key status   whether encryption is available and the key exists
  key encrypt-existing
               encrypt sessions and history saved in plain text; the
               plaintext is copied aside first, never deleted
"""

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
    first = crypto.ask_passphrase("New passphrase: ")
    if not first:
        _fail("the passphrase must not be empty")
    if crypto.ask_passphrase("Repeat it: ") != first:
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
    if not crypto.enabled():
        print(
            "To encrypt sessions and step history, add  encrypt = true  to "
            "~/.config/codebud/config.toml (or set CODEBUD_ENCRYPT=1)."
        )


def _passwd() -> None:
    if not crypto.has_key():
        _fail("no encryption key yet; create one with: codebud key init")
    old = crypto.ask_passphrase("Current passphrase: ")
    try:
        crypto.unlock(old)  # check it before asking for the new one
    except crypto.CryptoError as exc:
        _fail(str(exc))
    first = crypto.ask_passphrase("New passphrase: ")
    if crypto.ask_passphrase("Repeat it: ") != first:
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
    state = "on" if crypto.enabled() else "off (turn it on with encrypt = true)"
    print(f"  {_OUT_DIM}encryption {_OUT_RESET} {state}")
    print(f"  {_OUT_DIM}key        {_OUT_RESET} {crypto.key_dir()}")
    print(f"  {_OUT_DIM}public key {_OUT_RESET} {crypto.recipient()}")
    if crypto.passphrase_from_env() is not None:
        print(f"  {_OUT_DIM}passphrase {_OUT_RESET} from {crypto.PASSPHRASE_ENV} (unattended)")
    else:
        print(f"  {_OUT_DIM}passphrase {_OUT_RESET} asked on the terminal when needed")


def _encrypt_existing(yes: bool) -> None:
    from codebud import encrypt_existing
    from codebud.cli.common import plural

    if not crypto.has_key():
        _fail("no encryption key yet; create one with: codebud key init")
    todo = encrypt_existing.plan()
    for session_id in todo.in_use:
        print(f"Skipping session {session_id}: it is open in a codebud chat.")
    files = todo.files()
    if not files:
        print("Nothing to encrypt: no plaintext sessions or step history.")
    else:
        backup = encrypt_existing.backup_dir()
        print(f"Plaintext to encrypt ({plural(len(files), 'file')}):")
        for path in files:
            print(f"  {path}")
        print(f"Each is first copied, unchanged, to {backup}/")
        print("and left there for you to delete; codebud never deletes it.")
        if not yes:
            if not sys.stdin.isatty():
                _fail("not encrypting without confirmation; re-run with --yes")
            if input("Encrypt them? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Nothing changed.")
                sys.exit(1)
        copies = encrypt_existing.apply(todo, backup)
        print(f"Encrypted {plural(len(copies), 'file')}. The plaintext copies are in {backup}:")
        for copy in copies:
            print(f"  {copy}")
        print(f"Delete them when you are satisfied:  rm -r {backup}")
    for path in todo.other_plaintext:
        print(f"Not converted (older plaintext; delete it yourself if unneeded): {path}")
    if not crypto.enabled():
        print("Note: encryption is off, so new data is still saved in plain text (encrypt = true).")


def cmd_key(args):
    if args.key_action != "status" and not crypto.available():
        _fail("encryption needs the pyrage module: pip install 'codebud[crypto]'")
    if args.key_action == "encrypt-existing":
        _encrypt_existing(args.yes)
        return
    {"init": _init, "passwd": _passwd, "status": _status}[args.key_action]()
