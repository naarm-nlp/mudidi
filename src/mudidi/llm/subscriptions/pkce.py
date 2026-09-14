"""Small, provider-neutral RFC 7636 PKCE helpers.

The verifier is intentionally kept in memory only.  Callers should pass it
straight to their token exchange and must not put it in logs, serialized
configuration, or subprocess environments.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import re
import secrets


_CODE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_MAX_STATE_LENGTH = 512


def _validate_verifier(verifier: str) -> str:
    if not isinstance(verifier, str) or _CODE_VERIFIER_RE.fullmatch(verifier) is None:
        raise ValueError("PKCE verifier must be 43-128 RFC 7636 characters")
    return verifier


def _validate_state(state: str) -> str:
    if not isinstance(state, str) or not state or len(state) > _MAX_STATE_LENGTH:
        raise ValueError("OAuth state must be a non-empty bounded string")
    try:
        state.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("OAuth state must be ASCII") from exc
    if any(character.isspace() for character in state):
        raise ValueError("OAuth state must not contain whitespace")
    return state


def _s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True)
class PkceChallenge:
    """An in-memory PKCE verifier/challenge and OAuth state transaction.

    ``verifier`` and ``state`` are generated from independent cryptographically
    secure random values.  The default verifier is 32 random bytes encoded with
    URL-safe base64, yielding a 43-character verifier accepted by RFC 7636.
    """

    verifier: str
    challenge: str
    state: str

    def __post_init__(self) -> None:
        verifier = _validate_verifier(self.verifier)
        _validate_state(self.state)
        expected = _s256_challenge(verifier)
        if self.challenge != expected:
            raise ValueError("PKCE challenge does not match verifier")

    @classmethod
    def generate(
        cls,
        *,
        verifier: str | None = None,
        state: str | None = None,
    ) -> PkceChallenge:
        """Generate a fresh challenge with independent verifier and state."""

        generated_verifier = (
            secrets.token_urlsafe(32) if verifier is None else verifier
        )
        generated_state = secrets.token_urlsafe(32) if state is None else state
        if generated_state == generated_verifier:
            # This is extraordinarily unlikely, but retaining distinct values
            # is an explicit invariant of the authorization transaction.
            generated_state = secrets.token_urlsafe(32)
        return cls(
            verifier=generated_verifier,
            challenge=_s256_challenge(_validate_verifier(generated_verifier)),
            state=generated_state,
        )

    @classmethod
    def create(
        cls,
        *,
        verifier: str | None = None,
        state: str | None = None,
    ) -> PkceChallenge:
        """Alias for :meth:`generate` used by provider adapters."""

        return cls.generate(verifier=verifier, state=state)

    @classmethod
    def from_verifier(cls, verifier: str, *, state: str | None = None) -> PkceChallenge:
        """Build a challenge for a caller-supplied verifier (primarily tests)."""

        cleaned = _validate_verifier(verifier)
        selected_state = (
            secrets.token_urlsafe(32) if state is None else state
        )
        return cls(
            verifier=cleaned,
            challenge=_s256_challenge(cleaned),
            state=selected_state,
        )

    @staticmethod
    def challenge_for(verifier: str) -> str:
        """Return the RFC 7636 S256 challenge for a valid verifier."""

        return _s256_challenge(_validate_verifier(verifier))

    @property
    def code_verifier(self) -> str:
        """OAuth parameter spelling for the verifier."""

        return self.verifier

    @property
    def code_challenge(self) -> str:
        """OAuth parameter spelling for the derived challenge."""

        return self.challenge

    @property
    def code_challenge_method(self) -> str:
        """OAuth parameter value for the RFC 7636 derivation method."""

        return "S256"

    def as_authorization_parameters(self) -> dict[str, str]:
        """Alias for :meth:`authorization_parameters`."""

        return self.authorization_parameters()

    def authorization_parameters(self) -> dict[str, str]:
        """Return only non-secret authorization parameters.

        The verifier is deliberately absent; it belongs only in the subsequent
        token exchange.
        """

        return {
            "code_challenge": self.challenge,
            "code_challenge_method": "S256",
            "state": self.state,
        }

    def __repr__(self) -> str:
        """Avoid exposing verifier/state values in diagnostic output."""

        return "PkceChallenge(verifier='[redacted]', challenge='[redacted]', state='[redacted]')"


__all__ = ["PkceChallenge"]
