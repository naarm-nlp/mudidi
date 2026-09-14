"""Focused tests for direct-subscription PKCE primitives."""

from __future__ import annotations

import base64
import hashlib
import re
from urllib.parse import urlsplit

import pytest

from mudidi.llm.subscriptions.pkce import PkceChallenge


def test_pkce_uses_rfc7636_s256_and_a_valid_verifier() -> None:
    challenge = PkceChallenge.generate()

    assert 43 <= len(challenge.verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9._~-]+", challenge.verifier)
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(challenge.verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert challenge.challenge == expected
    assert challenge.code_verifier == challenge.verifier
    assert challenge.code_challenge == challenge.challenge


def test_pkce_state_is_unpredictable_and_distinct() -> None:
    first = PkceChallenge.generate()
    second = PkceChallenge.generate()

    assert first.state
    assert second.state
    assert first.state != second.state
    assert first.verifier != second.verifier


@pytest.mark.parametrize(
    "verifier",
    ["", "short", "!" * 43, "x" * 129],
)
def test_pkce_rejects_invalid_verifiers(verifier: str) -> None:
    with pytest.raises(ValueError):
        PkceChallenge.from_verifier(verifier, state="state")


def test_pkce_challenge_state_can_be_passed_to_an_oauth_redirect() -> None:
    challenge = PkceChallenge.generate()
    parsed = urlsplit(
        f"http://127.0.0.1:43123/callback?state={challenge.state}"
    )

    assert parsed.hostname == "127.0.0.1"
