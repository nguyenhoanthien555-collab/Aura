"""
Provider API keys, set from the phone instead of from a shell.

Aura runs on Render. When a provider stops answering - a quota that ran
out, a key that was rotated - the only fix used to be editing the
service's environment variables in a dashboard and waiting for a redeploy.
That is a bad position to be in from a phone, and it is the whole reason
this module exists.

WHY `os.environ` AND NOT A NEW RESOLUTION PATH
----------------------------------------------
Every provider already reads its own key in `__init__`:

    self.api_key = os.getenv("GROQ_API_KEY")

and `BrainRouter._skip_reason` probes the same variables to explain a
provider it could not build. Five providers, one convention, already
tested. Threading a key parameter through all of them would mean editing
every provider, every call in `_instantiate_provider`, and would leave
`_skip_reason` lying about a provider whose key is stored but not in the
environment.

So a stored key is applied to `os.environ` and nothing downstream changes.
The scope is right too: process-global is exactly what those providers
already assume. `apply()` is called at startup and after every write.

WHAT "SECURE" MEANS HERE, PRECISELY
----------------------------------
The file is encrypted with Fernet (AES-128-CBC + HMAC) under a key
derived by scrypt from `AURA_SECRET_KEY`, falling back to
`AURA_SERVER_AUTH_TOKEN` - which the server already refuses to start
without. The salt is stored beside the ciphertext; it is not a secret,
it exists so two deployments sharing a token do not share a key stream.

That protects a stolen disk, a leaked snapshot, an accidental `git add`.
It does NOT protect against someone who can already read the process's
environment - nothing can, because the process must decrypt unattended.
Claiming otherwise would be the kind of security theatre this codebase
avoids.

There is deliberately NO plaintext fallback. If no secret is configured,
`persistent` is False and writes are refused with a reason the API
returns verbatim. A store that silently degrades to plaintext is worse
than one that says it cannot help.

Nothing here logs, returns or formats a key. `mask()` is the only way a
stored value leaves this module, and it never emits more than the last
four characters.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from threading import RLock

from core.logger import logger
from core.paths import DATA_DIR


# The environment variable each provider reads. Deliberately imported
# from the router rather than restated: two lists of provider key names
# would drift, and the one that drifts is the one that stops working.
from brain.router import PROVIDER_KEYS


# Where the encrypted blob lives. Under `data/` because that is the
# directory Render mounts a persistent disk on (docs/DEPLOYMENT.md §2);
# anywhere else and a redeploy silently forgets every key.
#
# Read as a module global at construction time, so a test can redirect it
# (see `tests/conftest.py`) without every caller having to pass a path.
CREDENTIAL_PATH = DATA_DIR / "credentials.enc"

# Read in this order. `AURA_SECRET_KEY` is the dedicated one and is what
# an operator should set. The auth token is the fallback because it is
# already mandatory, already secret, and already stored in the same place
# a dedicated variable would be - so the feature works out of the box
# without inventing a second thing to configure.
SECRET_ENV_VARS = ("AURA_SECRET_KEY", "AURA_SERVER_AUTH_TOKEN")

# The primary secret variable, named for tests that must unset or set it.
SECRET_ENV_VAR = SECRET_ENV_VARS[0]

# scrypt parameters. n=2**14 is ~100ms on server hardware, which is
# irrelevant for a value derived once per process and worth a lot against
# an offline attack on a short token.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16


def mask(value: str | None) -> str:
    """
    A key, rendered so it can be shown on a screen.

    `sk-or-v1-abc...wxyz` becomes `••••••••wxyz`. Short values lose
    everything: a four-character key would otherwise be displayed whole,
    and a masked value that is sometimes the real value is not a mask.

    Returns "" for nothing stored, which is how the UI tells "no key" from
    "a key it may not see".
    """

    text = (value or "").strip()

    if not text:
        return ""

    if len(text) <= 8:
        return "•" * 8

    return "•" * 8 + text[-4:]


class CredentialError(RuntimeError):
    """A write that could not be made durable. The message is user-facing."""


class CredentialNotPersisted(CredentialError):
    """
    The key is valid and now in effect, but could not be written to disk.

    Distinct from its parent because the two mean opposite things to a
    caller: a rejected key is a 422 the user must correct, while this is a
    success with a caveat. Inferring the difference from `persistent` at
    the call site was wrong - a store with no secret raises the *parent*
    for a bad key too, so a masked value came back as "saved".
    """


class CredentialStore:
    """
    Provider keys, held in memory and optionally encrypted on disk.

    Thread-safe: FastAPI serves settings writes on request threads while
    a chat turn may be reading the environment on another.
    """

    def __init__(self, path: Path | str | None = None, secret: str | None = None):

        self.path = Path(path) if path is not None else CREDENTIAL_PATH
        self._explicit_secret = secret

        self._lock = RLock()
        self._keys: dict[str, str] = {}
        self._salt: bytes | None = None

        self._load()

    # ------------------------------------------------------------------
    # Capability
    # ------------------------------------------------------------------

    def _secret(self) -> str:
        """The passphrase, or "" when none is configured."""

        if self._explicit_secret is not None:
            return self._explicit_secret

        for name in SECRET_ENV_VARS:
            value = os.environ.get(name, "").strip()
            if value:
                return value

        return ""

    @property
    def persistent(self) -> bool:
        """
        Whether a write survives a restart.

        False means no secret is configured, so there is nothing to
        encrypt with. The API reports this rather than writing plaintext.
        """

        return bool(self._secret())

    def unavailable_reason(self) -> str:
        """Why persistence is off, phrased for the person who can fix it."""

        if self.persistent:
            return ""

        return (
            "Set AURA_SECRET_KEY (or AURA_SERVER_AUTH_TOKEN) on the server "
            "to store provider keys encrypted at rest. Until then keys can "
            "be set for this process only and are lost on restart."
        )

    # ------------------------------------------------------------------
    # Crypto
    # ------------------------------------------------------------------

    def _fernet(self, salt: bytes):
        """A Fernet built from the configured secret and `salt`."""

        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

        derived = Scrypt(
            salt=salt, length=32, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
        ).derive(self._secret().encode("utf-8"))

        return Fernet(base64.urlsafe_b64encode(derived))

    # ------------------------------------------------------------------
    # Disk
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """
        Read the stored keys, or start empty.

        Every failure here is non-fatal and logged without detail beyond
        the exception type. A corrupt or undecryptable credential file
        must not stop Aura from booting: the environment may still carry
        working keys, and a server that refuses to start because a
        convenience feature's file is damaged is worse than one that
        starts with fewer providers.
        """

        with self._lock:

            if not self.path.exists():
                return

            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                salt = base64.b64decode(raw["salt"])
                payload = raw["keys"]
            except Exception as error:
                logger.warning(
                    "Credential store at %s is unreadable (%s); ignoring it",
                    self.path, type(error).__name__,
                )
                return

            if not self.persistent:
                # A file exists but the secret that would decrypt it is
                # gone. Said out loud: the operator is about to wonder why
                # the keys they saved have no effect.
                logger.warning(
                    "Credential store exists but no secret is configured; "
                    "stored provider keys cannot be decrypted"
                )
                return

            try:
                decrypted = self._fernet(salt).decrypt(payload.encode("utf-8"))
                keys = json.loads(decrypted.decode("utf-8"))
            except Exception as error:
                logger.warning(
                    "Credential store could not be decrypted (%s); the secret "
                    "may have changed", type(error).__name__,
                )
                return

            if isinstance(keys, dict):
                self._keys = {
                    str(k): str(v) for k, v in keys.items() if str(v).strip()
                }
                self._salt = salt

    def _save(self) -> None:
        """
        Encrypt and write. Caller holds the lock.

        Writes to a temporary file and replaces, so a crash mid-write
        cannot leave a half-written blob where a readable one was. The
        file is created 0600 *before* any secret goes into it - opening
        wide and chmod-ing after would leave a window where the keys are
        world-readable.
        """

        if not self.persistent:
            raise CredentialNotPersisted(self.unavailable_reason())

        salt = self._salt or os.urandom(_SALT_BYTES)

        token = self._fernet(salt).encrypt(
            json.dumps(self._keys).encode("utf-8")
        )

        document = json.dumps(
            {
                "version": 1,
                "salt": base64.b64encode(salt).decode("ascii"),
                "keys": token.decode("ascii"),
            },
            indent=2,
        )

        self.path.parent.mkdir(parents=True, exist_ok=True)

        temporary = self.path.with_suffix(self.path.suffix + ".tmp")

        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )

        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(document)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        os.replace(temporary, self.path)

        self._salt = salt

    # ------------------------------------------------------------------
    # Reading - masked only
    # ------------------------------------------------------------------

    def has(self, provider: str) -> bool:
        """Whether a key is available, from the store OR the environment."""

        with self._lock:

            if self._keys.get(str(provider), "").strip():
                return True

        variable = PROVIDER_KEYS.get(str(provider))

        return bool(variable and os.environ.get(variable, "").strip())

    def source_of(self, provider: str) -> str:
        """
        "store", "environment" or "" - which one a provider is using.

        Worth distinguishing in the UI: a key set in the Render dashboard
        cannot be deleted from the phone, and telling the user that up
        front is better than a delete that appears to do nothing.
        """

        with self._lock:
            if self._keys.get(str(provider), "").strip():
                return "store"

        variable = PROVIDER_KEYS.get(str(provider))

        if variable and os.environ.get(variable, "").strip():
            return "environment"

        return ""

    def masked(self, provider: str) -> str:
        """
        The stored key, masked. Never the key itself.

        Falls back to the environment so a provider configured the old way
        still shows as configured - the UI asks one question ("is this
        provider set up") and gets one answer regardless of where the key
        came from.
        """

        with self._lock:
            stored = self._keys.get(str(provider), "")

        if stored.strip():
            return mask(stored)

        variable = PROVIDER_KEYS.get(str(provider))

        if variable:
            return mask(os.environ.get(variable, ""))

        return ""

    def masked_all(self) -> dict[str, str]:
        """Every known provider to its masked key. The API's whole view."""

        return {name: self.masked(name) for name in PROVIDER_KEYS}

    def secret_for(self, provider: str) -> str:
        """
        The real key, for the one caller that needs it: a live test.

        Not reachable from any route directly - `POST /api/providers/test`
        uses it to build a provider and never puts the value in its
        response. Named to be conspicuous in a grep for `secret`.
        """

        with self._lock:
            stored = self._keys.get(str(provider), "")

        if stored.strip():
            return stored

        variable = PROVIDER_KEYS.get(str(provider))

        return os.environ.get(variable, "") if variable else ""

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def set(self, provider: str, key: str) -> None:
        """
        Store a key and apply it to the environment.

        Rejects an unknown provider and a blank key. The masked form of a
        value is never a valid key, so a client that echoes back what it
        was shown is refused rather than storing bullets - which would
        look like success and break the provider.
        """

        name = str(provider).strip().lower()

        if name not in PROVIDER_KEYS:
            raise CredentialError(f"Unknown provider: {provider}")

        value = str(key or "").strip()

        if not value:
            raise CredentialError("An API key cannot be empty")

        if "•" in value or set(value) == {"*"}:
            raise CredentialError(
                "That looks like a masked value, not a key. Paste the real "
                "key, or leave the field untouched to keep the current one."
            )

        with self._lock:
            previous = self._keys.get(name)
            self._keys[name] = value

            try:
                self._save()
            except CredentialNotPersisted as error:
                # In-memory only. The caller decides whether that is
                # acceptable and tells the user; the key still works for
                # this process, which is what a stranded operator needs.
                self._apply_one(name, value)
                raise
            except Exception:
                if previous is None:
                    self._keys.pop(name, None)
                else:
                    self._keys[name] = previous
                raise

            self._apply_one(name, value)

    def delete(self, provider: str) -> bool:
        """
        Forget a stored key. True when there was one.

        The environment variable is cleared too - leaving it set would
        make a deleted key keep working until the next restart, which is
        the opposite of what "delete" means. A key that came from the
        deployment's own environment is NOT removed from disk (there is
        nothing to remove) but is unset for this process, and
        `source_of` is what lets the UI explain that.
        """

        name = str(provider).strip().lower()

        with self._lock:
            existed = self._keys.pop(name, None) is not None

            if existed:
                try:
                    self._save()
                except CredentialError as error:
                    # Deletion still happened for this process - the key is
                    # gone from memory and will be gone from the
                    # environment. Only the durability is lost.
                    logger.warning("Stored key removed in memory only: %s", error)
                except Exception as error:
                    logger.warning(
                        "Stored key removed in memory only (%s)",
                        type(error).__name__,
                    )

        variable = PROVIDER_KEYS.get(name)

        if variable:
            os.environ.pop(variable, None)

        return existed

    # ------------------------------------------------------------------
    # The environment
    # ------------------------------------------------------------------

    def _apply_one(self, provider: str, value: str) -> None:

        variable = PROVIDER_KEYS.get(str(provider))

        if variable:
            os.environ[variable] = value

    def apply(self) -> list[str]:
        """
        Push every stored key into the environment. Returns the names set.

        Stored keys WIN over the deployment's environment. That is the
        point of the feature: the phone is how a bad key gets replaced,
        and a store that lost to a stale dashboard value could not fix
        anything. Called at startup and after each write.
        """

        applied: list[str] = []

        with self._lock:
            items = list(self._keys.items())

        for name, value in items:
            if value.strip():
                self._apply_one(name, value)
                applied.append(name)

        return applied


# One store per process, built on first use. Mirrors how the runtime and
# session manager are held - a module global reached through a function,
# so a test can install its own.
_store: CredentialStore | None = None


def get_credential_store() -> CredentialStore:
    """The process-wide credential store."""

    global _store

    if _store is None:
        _store = CredentialStore()

    return _store


def set_credential_store(store: CredentialStore | None) -> None:
    """Install a store (tests), or reset to lazy construction with None."""

    global _store

    _store = store


