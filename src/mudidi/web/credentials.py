"""Process-memory API credential handling for the localhost application."""

from __future__ import annotations

import os
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
    """Thread-safe temporary credential store with environment fallback."""

    def __init__(self, *, environ: Mapping[str, str] | None = None) -> None:
        self._environ = environ if environ is not None else os.environ
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
        environment_name = _ENVIRONMENT_KEYS.get(provider)
        environment_value = self._environ.get(environment_name, "") if environment_name else ""
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


def credential_environment_name(provider: Provider) -> str | None:
    """Return the allowlisted LiteLLM environment variable for a provider."""

    return _ENVIRONMENT_KEYS.get(provider)
