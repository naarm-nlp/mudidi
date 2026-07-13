"""Encrypted API credential handling for the localhost application."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock

from pydantic import SecretStr

from mudidi.web.models import Provider

_ENVIRONMENT_KEYS: dict[Provider, str] = {
    Provider.ANTHROPIC: "ANTHROPIC_API_KEY",
    Provider.OPENAI: "OPENAI_API_KEY",
    Provider.GEMINI: "GEMINI_API_KEY",
    Provider.OPENROUTER: "OPEN_ROUTER_API_KEY",
}


class CredentialSource(StrEnum):
    """Non-secret description of where a provider key will be resolved."""

    TEMPORARY = "temporary"
    PERSISTENT = "persistent"
    ENVIRONMENT = "environment"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    """Safe status exposed to templates and run preconditions."""

    provider: Provider
    available: bool
    source: CredentialSource


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    """Secret-bearing value used only at the worker launch boundary."""

    provider: Provider
    source: CredentialSource
    _value: SecretStr

    def get_secret_value(self) -> str:
        """Return the secret at the narrow execution handoff boundary."""

        return self._value.get_secret_value()


class CredentialVault:
    """Thread-safe credential resolver with encrypted persistent storage."""

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        persistent_store: PersistentCredentialStore | None = None,
    ) -> None:
        self._environ = environ if environ is not None else os.environ
        self._persistent_store = persistent_store
        self._temporary: dict[Provider, SecretStr] = {}
        self._lock = RLock()

    def __repr__(self) -> str:
        with self._lock:
            providers = sorted(provider.value for provider in self._temporary)
        return f"CredentialVault(temporary_providers={providers!r})"

    def set_temporary(self, provider: Provider, value: str) -> None:
        """Keep a provider key in process memory until cleared or shutdown."""

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("API key must not be empty")
        if provider is Provider.CUSTOM:
            raise ValueError("custom routing does not define a standard API key")
        with self._lock:
            self._temporary[provider] = SecretStr(cleaned)

    def set_persistent(self, provider: Provider, value: str) -> None:
        """Encrypt and save a provider key in the local dashboard database."""

        cleaned = _validated_credential(provider, value)
        if self._persistent_store is None:
            raise ValueError("persistent credential storage is unavailable")
        self._persistent_store.save(provider, cleaned)

    def reveal_persistent(self, provider: Provider) -> str:
        """Decrypt one explicitly saved key for a user-requested preview."""

        if self._persistent_store is None:
            raise KeyError(provider.value)
        value = self._persistent_store.load(provider)
        if value is None:
            raise KeyError(provider.value)
        return value

    def clear_persistent(self, provider: Provider) -> None:
        """Delete one encrypted provider key."""

        if self._persistent_store is not None:
            self._persistent_store.delete(provider)

    def clear_temporary(self, provider: Provider) -> None:
        """Forget a temporary key, leaving any environment fallback intact."""

        with self._lock:
            self._temporary.pop(provider, None)

    def resolve(self, provider: Provider) -> ResolvedCredential | None:
        """Resolve temporary first, then environment, without persisting either."""

        with self._lock:
            temporary = self._temporary.get(provider)
        if temporary is not None:
            return ResolvedCredential(provider, CredentialSource.TEMPORARY, temporary)
        if self._persistent_store is not None:
            persistent = self._persistent_store.load(provider)
            if persistent is not None:
                return ResolvedCredential(
                    provider,
                    CredentialSource.PERSISTENT,
                    SecretStr(persistent),
                )
        environment_name = _ENVIRONMENT_KEYS.get(provider)
        environment_value = (
            self._environ.get(environment_name, "") if environment_name else ""
        )
        if environment_value.strip():
            return ResolvedCredential(
                provider,
                CredentialSource.ENVIRONMENT,
                SecretStr(environment_value.strip()),
            )
        return None

    def status(self, provider: Provider) -> CredentialStatus:
        """Return non-secret availability suitable for rendering or persistence."""

        resolved = self.resolve(provider)
        if resolved is None:
            return CredentialStatus(provider, False, CredentialSource.MISSING)
        return CredentialStatus(provider, True, resolved.source)

    def redaction_values(self) -> tuple[str, ...]:
        """Return currently resolvable secrets for in-process output redaction."""

        values: list[str] = []
        for provider in _ENVIRONMENT_KEYS:
            resolved = self.resolve(provider)
            if resolved is not None:
                values.append(resolved.get_secret_value())
        return tuple(dict.fromkeys(value for value in values if value))


def credential_environment_name(provider: Provider) -> str | None:
    """Return the allowlisted LiteLLM environment variable for a provider."""

    return _ENVIRONMENT_KEYS.get(provider)


class PersistentCredentialStore:
    """Fernet-encrypted provider credentials stored in the web SQLite database."""

    def __init__(self, *, database_path: Path, key_path: Path) -> None:
        from cryptography.fernet import Fernet

        self.database_path = database_path.expanduser().resolve()
        self.key_path = key_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(_load_or_create_fernet_key(self.key_path))
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_credentials (
                    provider TEXT PRIMARY KEY,
                    encrypted_value BLOB NOT NULL
                )
                """
            )
        try:
            self.database_path.chmod(0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def save(self, provider: Provider, value: str) -> None:
        """Upsert authenticated ciphertext for one supported provider."""

        cleaned = _validated_credential(provider, value)
        encrypted = self._fernet.encrypt(cleaned.encode("utf-8"))
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO provider_credentials(provider, encrypted_value) "
                "VALUES (?, ?) ON CONFLICT(provider) DO UPDATE SET "
                "encrypted_value = excluded.encrypted_value",
                (provider.value, encrypted),
            )

    def load(self, provider: Provider) -> str | None:
        """Decrypt one credential or return ``None`` when it is not saved."""

        from cryptography.fernet import InvalidToken

        with self._connect() as connection:
            row = connection.execute(
                "SELECT encrypted_value FROM provider_credentials WHERE provider = ?",
                (provider.value,),
            ).fetchone()
        if row is None:
            return None
        try:
            return self._fernet.decrypt(bytes(row[0])).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise ValueError("saved credential cannot be decrypted") from exc

    def delete(self, provider: Provider) -> None:
        """Delete one provider ciphertext without affecting other settings."""

        with self._connect() as connection:
            connection.execute(
                "DELETE FROM provider_credentials WHERE provider = ?",
                (provider.value,),
            )


def _validated_credential(provider: Provider, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("API key must not be empty")
    if provider is Provider.CUSTOM:
        raise ValueError("custom routing does not define a standard API key")
    return cleaned


def _load_or_create_fernet_key(path: Path) -> bytes:
    """Load or atomically create a user-readable-only encryption key."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        from cryptography.fernet import Fernet

        try:
            os.write(descriptor, Fernet.generate_key())
        finally:
            os.close(descriptor)
    try:
        path.chmod(0o600)
        return path.read_bytes().strip()
    except OSError as exc:
        raise ValueError("credential encryption key is unavailable") from exc
