"""Provider-neutral reasoning capability and effort routing.

The public effort ladder follows Oh My Pi's portable taxonomy. Provider
metadata and reviewed MUDIDI rules select a model-specific subset before a
request reaches an API-key or subscription transport.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Sequence, TypeAlias

ReasoningEffort: TypeAlias = Literal[
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
]
ReasoningChoice: TypeAlias = Literal[
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
]
ReasoningMode: TypeAlias = Literal[
    "effort",
    "google-level",
    "budget",
    "anthropic-adaptive",
]
CapabilitySource: TypeAlias = Literal["provider", "reviewed", "unknown"]

ENABLED_REASONING_EFFORTS: tuple[ReasoningEffort, ...] = (
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)
REASONING_CHOICES: tuple[ReasoningChoice, ...] = (
    "none",
    *ENABLED_REASONING_EFFORTS,
)
_EFFORT_RANK = {effort: index for index, effort in enumerate(ENABLED_REASONING_EFFORTS)}
_CLAUDE_ID = re.compile(
    r"(?:^|/)claude-(?P<family>fable|mythos|opus|sonnet|haiku)-"
    r"(?P<major>\d+)(?:[.-](?P<minor>\d{1,2})(?!\d))?"
)
_CLAUDE_LEGACY_ID = re.compile(
    r"(?:^|/)claude-(?P<major>\d+)[.-](?P<minor>\d{1,2})(?!\d)-"
    r"(?P<family>opus|sonnet|haiku)"
)
_REVISION = re.compile(
    r"(?:^|[-_/])(?P<major>\d+)[.-](?P<minor>\d+)(?:[.-](?P<patch>\d+))?"
)


def _validated_effort(value: object) -> ReasoningEffort | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized not in _EFFORT_RANK:
        return None
    return normalized  # type: ignore[return-value]


def parse_reasoning_levels(values: object) -> tuple[ReasoningEffort, ...]:
    """Return recognized provider levels in canonical order."""

    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return ()
    found: set[ReasoningEffort] = set()
    for value in values:
        candidate = value.get("effort") if isinstance(value, dict) else value
        effort = _validated_effort(candidate)
        if effort is not None:
            found.add(effort)
    return tuple(effort for effort in ENABLED_REASONING_EFFORTS if effort in found)


@dataclass(frozen=True, slots=True)
class ReasoningProfile:
    """Reasoning capabilities baked onto one normalized model option."""

    efforts: tuple[ReasoningEffort, ...]
    default: ReasoningEffort | None = None
    supports_off: bool = False
    effort_map: tuple[tuple[ReasoningEffort, ReasoningEffort], ...] = ()
    wire_model_map: tuple[tuple[ReasoningEffort, str], ...] = ()
    mode: ReasoningMode = "effort"
    source: CapabilitySource = "unknown"

    def __post_init__(self) -> None:
        ordered = tuple(
            effort for effort in ENABLED_REASONING_EFFORTS if effort in self.efforts
        )
        if ordered != self.efforts or len(set(self.efforts)) != len(self.efforts):
            raise ValueError("reasoning efforts must be unique and canonically ordered")
        if self.default is not None and self.default not in self.efforts:
            raise ValueError("reasoning default must be a supported effort")
        if any(
            source not in _EFFORT_RANK or target not in _EFFORT_RANK
            for source, target in self.effort_map
        ):
            raise ValueError("reasoning effort map contains an unknown effort")
        if any(
            effort not in _EFFORT_RANK or not model_id
            for effort, model_id in self.wire_model_map
        ):
            raise ValueError("reasoning wire-model map is invalid")

    def to_payload(self) -> dict[str, object]:
        """Return the non-secret browser capability representation."""

        return {
            "efforts": list(self.efforts),
            "default": self.default,
            "supports_off": self.supports_off,
            "source": self.source,
        }


def choose_supported_effort(
    profile: ReasoningProfile,
    requested: str | None,
) -> ReasoningEffort | None:
    """Resolve a requested effort to a supported portable level.

    Explicit maps win. Otherwise an unsupported value clamps to the nearest
    supported level no greater than the request, falling back to the lowest.
    """

    if not profile.efforts:
        return None
    if requested is None or requested.strip().lower() in {"none", "off"}:
        return (
            profile.default
            if profile.source == "unknown" and profile.default is not None
            else profile.efforts[0]
        )
    effort = _validated_effort(requested)
    if effort is None:
        return profile.default or profile.efforts[0]
    if effort in profile.efforts:
        return effort
    mapped = dict(profile.effort_map).get(effort)
    if mapped in profile.efforts:
        return mapped
    requested_rank = _EFFORT_RANK[effort]
    lower = [
        candidate
        for candidate in profile.efforts
        if _EFFORT_RANK[candidate] <= requested_rank
    ]
    return lower[-1] if lower else profile.efforts[0]


def wire_effort(
    profile: ReasoningProfile, requested: str | None
) -> ReasoningEffort | None:
    """Return the model's provider-facing effort for a portable request."""

    selected = choose_supported_effort(profile, requested)
    if selected is None:
        return None
    return dict(profile.effort_map).get(selected, selected)


def wire_model_id(
    profile: ReasoningProfile,
    model_id: str,
    requested: str | None,
) -> str:
    """Return the provider model ID routed for the resolved effort."""

    selected = choose_supported_effort(profile, requested)
    if selected is None:
        return model_id
    return dict(profile.wire_model_map).get(selected, model_id)


def _model_slug(model_id: str) -> str:
    lowered = model_id.strip().lower()
    return lowered.split("/", maxsplit=1)[-1]


def _revision(model_id: str) -> tuple[int, int, int] | None:
    match = _REVISION.search(model_id)
    if match is None:
        return None
    return tuple(int(match.group(name) or 0) for name in ("major", "minor", "patch"))  # type: ignore[return-value]


def _reviewed_google_profile(model_id: str) -> ReasoningProfile | None:
    slug = _model_slug(model_id)
    if slug in {"gemini-3-flash-agent", "gemini-pro-agent"}:
        return ReasoningProfile(
            efforts=("high",),
            default="high",
            mode="google-level",
            source="reviewed",
        )
    revision = _revision(slug)
    if "gemini" not in slug or revision is None:
        return None
    mode: ReasoningMode = "google-level" if revision[0] >= 3 else "budget"
    if "flash" in slug or "lite" in slug or revision[0] < 3:
        return ReasoningProfile(
            efforts=("minimal", "low", "medium", "high"),
            default="low",
            mode=mode,
            source="reviewed",
        )
    if "pro" in slug and revision[0] >= 3:
        return ReasoningProfile(
            efforts=("low", "high"),
            default="high",
            mode=mode,
            source="reviewed",
        )
    return None


def _reviewed_claude_profile(model_id: str) -> ReasoningProfile | None:
    match = _CLAUDE_ID.search(model_id.lower()) or _CLAUDE_LEGACY_ID.search(
        model_id.lower()
    )
    if match is None:
        return None
    family = match.group("family")
    revision = (int(match.group("major")), int(match.group("minor") or 0))
    if revision < (3, 7):
        return ReasoningProfile(efforts=(), source="reviewed")

    if revision >= (4, 7):
        return ReasoningProfile(
            efforts=("low", "medium", "high", "xhigh", "max"),
            default="high",
            mode="anthropic-adaptive",
            source="reviewed",
        )
    if revision == (4, 6):
        efforts: tuple[ReasoningEffort, ...] = (
            ("low", "medium", "high", "max")
            if family == "opus"
            else ("low", "medium", "high")
        )
        return ReasoningProfile(
            efforts=efforts,
            default="high",
            mode="anthropic-adaptive",
            source="reviewed",
        )
    return ReasoningProfile(
        efforts=("minimal", "low", "medium", "high"),
        default="low",
        mode="budget",
        source="reviewed",
    )


def _reviewed_openai_profile(model_id: str) -> ReasoningProfile | None:
    slug = _model_slug(model_id)
    if slug.startswith(("gpt-4", "gpt-3.5")):
        return ReasoningProfile(efforts=(), source="reviewed")
    if "gpt-5.6" in slug:
        efforts: tuple[ReasoningEffort, ...] = ("low", "medium", "high", "xhigh", "max")
    elif "gpt-5.5" in slug:
        efforts = ("low", "medium", "high", "xhigh")
    elif any(marker in slug for marker in ("gpt-5", "o1", "o3", "o4")):
        efforts = ("minimal", "low", "medium", "high")
    else:
        return None
    return ReasoningProfile(
        efforts=efforts,
        default="medium" if "medium" in efforts else efforts[0],
        source="reviewed",
    )


def _unknown_profile() -> ReasoningProfile:
    return ReasoningProfile(
        efforts=ENABLED_REASONING_EFFORTS,
        default="low",
        source="unknown",
    )


def resolve_reasoning_profile(
    provider: str,
    model_id: str,
    *,
    advertised_efforts: object = None,
    advertised_default: object = None,
    supports_off: bool = False,
    wire_model_map: tuple[tuple[ReasoningEffort, str], ...] = (),
) -> ReasoningProfile:
    """Resolve provider metadata and reviewed rules into one model profile."""

    normalized_provider = provider.strip().lower()
    reviewed: ReasoningProfile | None
    if normalized_provider in {"gemini", "google", "google-antigravity"}:
        reviewed = _reviewed_google_profile(model_id)
    elif normalized_provider in {"anthropic", "claude"}:
        reviewed = _reviewed_claude_profile(model_id)
    elif normalized_provider in {"openai", "openai-codex"}:
        reviewed = _reviewed_openai_profile(model_id)
    elif normalized_provider == "openrouter":
        upstream = _model_slug(model_id)
        if upstream.startswith("anthropic/"):
            reviewed = _reviewed_claude_profile(upstream)
        elif upstream.startswith("google/"):
            reviewed = _reviewed_google_profile(upstream)
        elif upstream.startswith("openai/"):
            reviewed = _reviewed_openai_profile(upstream)
        else:
            reviewed = None
    else:
        reviewed = None

    efforts = parse_reasoning_levels(advertised_efforts)
    if efforts:
        default = _validated_effort(advertised_default)
        if default not in efforts:
            default = (
                reviewed.default
                if reviewed is not None and reviewed.default in efforts
                else efforts[0]
            )
        return ReasoningProfile(
            efforts=efforts,
            default=default,
            supports_off=supports_off,
            effort_map=reviewed.effort_map if reviewed is not None else (),
            wire_model_map=wire_model_map,
            mode=reviewed.mode if reviewed is not None else "effort",
            source="provider",
        )
    if reviewed is not None:
        return ReasoningProfile(
            efforts=reviewed.efforts,
            default=reviewed.default,
            supports_off=supports_off or reviewed.supports_off,
            effort_map=reviewed.effort_map,
            wire_model_map=wire_model_map or reviewed.wire_model_map,
            mode=reviewed.mode,
            source=reviewed.source,
        )
    return _unknown_profile()
