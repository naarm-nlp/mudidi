"""Production worker phase decomposition and credential pipe handling."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from mudidi.config.yaml_config import InferenceConfig
from mudidi.llm.subscriptions import SubscriptionProvider
from mudidi.paths import MDF_PARSING_GUIDE_FILENAME
from mudidi.schemas.field_cheatsheet import DictionaryMarkerCheatsheet

_CREDENTIAL_ENVIRONMENTS = {
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "OPEN_ROUTER_API_KEY",
}
_SUBSCRIPTION_DESCRIPTOR_FIELDS = frozenset({"auth_mode", "providers"})
_API_KEY_MESSAGE_FIELDS = frozenset({"auth_mode", "credentials"})


class _DuplicateCredentialKey(ValueError):
    """Raised when a credential boundary object repeats a key."""


def _reject_duplicate_keys(
    pairs: list[tuple[object, object]],
) -> dict[object, object]:
    payload: dict[object, object] = {}
    for key, value in pairs:
        if key in payload:
            raise _DuplicateCredentialKey("credential message contains duplicate keys")
        payload[key] = value
    return payload


class InferencePhase(StrEnum):
    """Web-owned stage decomposition that always pauses before Pass 2."""

    STAGE1 = "stage1"
    STAGE1_THEN_PASS1 = "stage1_then_pass1"
    PASS1 = "pass1"
    PASS2 = "pass2"
    USER_GUIDE = "user_guide"


@dataclass(frozen=True, slots=True)
class SubscriptionDescriptor:
    """Non-secret worker selector for subscription backends."""

    auth_mode: str
    providers: tuple[SubscriptionProvider, ...]

    def __post_init__(self) -> None:
        if self.auth_mode != "subscription":
            raise ValueError("subscription descriptor auth_mode must be 'subscription'")
        if not self.providers:
            raise ValueError("subscription descriptor requires providers")
        if any(
            not isinstance(provider, SubscriptionProvider)
            for provider in self.providers
        ):
            raise ValueError("subscription descriptor provider is unsupported")
        if len(set(self.providers)) != len(self.providers):
            raise ValueError("subscription descriptor providers must be unique")

    def as_message(self) -> dict[str, str | list[str]]:
        """Return the exact descriptor payload accepted by the worker."""

        return {
            "auth_mode": self.auth_mode,
            "providers": [provider.value for provider in self.providers],
        }


@dataclass(frozen=True, slots=True)
class InferencePhaseResult:
    """Non-secret result used by the worker event adapter."""

    return_code: int
    parse_rules_path: Path | None = None


ExecutionCallable = Callable[..., int]


def phase_configs(
    config: InferenceConfig,
    phase: InferencePhase,
) -> tuple[InferenceConfig, ...]:
    """Return safe typed configs for one web-owned execution phase."""

    if phase is InferencePhase.USER_GUIDE:
        if config.pipeline.parse_rules_file is None:
            raise ValueError("user-guide execution requires an uploaded MDF guide")
        if config.pipeline.stage == "all":
            stages = ("1", "2")
        elif config.pipeline.stage in {"2", "2-pass-1"}:
            stages = ("2",)
        else:
            raise ValueError("uploaded MDF guides require an MDF parsing pipeline")
    else:
        stages = {
            InferencePhase.STAGE1: ("1",),
            InferencePhase.STAGE1_THEN_PASS1: ("1", "2-pass-1"),
            InferencePhase.PASS1: ("2-pass-1",),
            InferencePhase.PASS2: ("2-pass-2",),
        }[phase]
    return tuple(
        config.model_copy(
            update={"pipeline": config.pipeline.model_copy(update={"stage": stage})}
        )
        for stage in stages
    )


def run_inference_phase(
    config: InferenceConfig,
    phase: InferencePhase,
    *,
    execute: ExecutionCallable,
    approved_rules: DictionaryMarkerCheatsheet | None,
    on_stage_started: Callable[[str], None] | None = None,
) -> InferencePhaseResult:
    """Execute one decomposed phase without allowing automatic web Pass 2."""

    if phase is InferencePhase.PASS2 and approved_rules is None:
        raise ValueError("Pass 2 requires loaded approved parse rules")
    configurations = phase_configs(config, phase)
    for phase_config in configurations:
        if on_stage_started is not None:
            on_stage_started(phase_config.pipeline.stage)
        result = execute(
            phase_config,
            approved_parse_rules=(
                approved_rules if phase_config.pipeline.stage == "2-pass-2" else None
            ),
        )
        if result != 0:
            return InferencePhaseResult(return_code=result)
    parse_rules_path = None
    if configurations[-1].pipeline.stage == "2-pass-1":
        parse_rules_path = config.output.directory / MDF_PARSING_GUIDE_FILENAME
    return InferencePhaseResult(return_code=0, parse_rules_path=parse_rules_path)


def apply_credential_message(
    message: str,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> SubscriptionDescriptor | None:
    """Validate a one-shot credential map or subscription descriptor."""

    try:
        payload = json.loads(message, object_pairs_hook=_reject_duplicate_keys)
    except _DuplicateCredentialKey as exc:
        raise ValueError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise ValueError("credential message is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("credential message must be an object")
    if payload == {}:
        return None

    if payload.get("auth_mode") == "subscription" or "providers" in payload:
        if set(payload) != _SUBSCRIPTION_DESCRIPTOR_FIELDS:
            raise ValueError("subscription descriptor contains unknown or mixed fields")
        if payload.get("auth_mode") != "subscription":
            raise ValueError("subscription descriptor auth_mode is unsupported")
        raw_providers = payload.get("providers")
        if not isinstance(raw_providers, list) or not raw_providers:
            raise ValueError(
                "subscription descriptor providers must be a non-empty list"
            )
        try:
            providers = tuple(SubscriptionProvider(item) for item in raw_providers)
        except (TypeError, ValueError) as exc:
            raise ValueError("subscription descriptor provider is unsupported") from exc
        return SubscriptionDescriptor(auth_mode="subscription", providers=providers)

    if set(payload) != _API_KEY_MESSAGE_FIELDS:
        raise ValueError("credential message contains unknown fields")
    if payload.get("auth_mode") != "api_key":
        raise ValueError("credential message auth_mode is unsupported")
    credentials = payload.get("credentials")
    if not isinstance(credentials, dict) or not credentials:
        raise ValueError("credential message credentials must be a non-empty object")
    target = environ if environ is not None else os.environ
    resolved: dict[str, str] = {}
    for environment_name, api_key in credentials.items():
        if environment_name not in _CREDENTIAL_ENVIRONMENTS:
            raise ValueError("credential message uses an unsupported environment name")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("credential message has an empty API key")
        resolved[str(environment_name)] = api_key.strip()
    target.update(resolved)
    return None
