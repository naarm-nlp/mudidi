from mudidi.llm.reasoning import (
    ENABLED_REASONING_EFFORTS,
    ReasoningProfile,
    choose_supported_effort,
    parse_reasoning_levels,
    resolve_reasoning_profile,
)


def test_portable_reasoning_order_runs_from_minimal_through_max() -> None:
    assert ENABLED_REASONING_EFFORTS == (
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )


def test_parse_reasoning_levels_accepts_provider_objects_and_ignores_unknowns() -> None:
    assert parse_reasoning_levels(
        [
            {"effort": "max"},
            {"effort": "low"},
            {"effort": "ultra"},
            "xhigh",
            "low",
            None,
        ]
    ) == ("low", "xhigh", "max")


def test_unknown_model_offers_every_portable_effort() -> None:
    profile = resolve_reasoning_profile("custom", "vendor/future-model")

    assert profile.efforts == ENABLED_REASONING_EFFORTS
    assert profile.default == "low"
    assert profile.source == "unknown"
    assert choose_supported_effort(profile, "none") == "low"


def test_google_flash_profile_exposes_only_valid_levels() -> None:
    profile = resolve_reasoning_profile("gemini", "gemini/gemini-3.8-flash")

    assert profile.efforts == ("minimal", "low", "medium", "high")
    assert choose_supported_effort(profile, "xhigh") == "high"
    assert choose_supported_effort(profile, "none") == "minimal"


def test_claude_dated_major_release_is_not_misread_as_a_minor_version() -> None:
    profile = resolve_reasoning_profile(
        "anthropic",
        "anthropic/claude-opus-4-20250514",
    )
    unsupported = resolve_reasoning_profile(
        "anthropic",
        "anthropic/claude-3-5-sonnet-20241022",
    )

    assert profile.mode == "budget"
    assert profile.efforts == ("minimal", "low", "medium", "high")
    assert unsupported.efforts == ()


def test_claude_adaptive_profile_supports_high_end_efforts() -> None:
    profile = resolve_reasoning_profile("anthropic", "anthropic/claude-opus-5")

    assert profile.efforts == ("low", "medium", "high", "xhigh", "max")
    assert profile.mode == "anthropic-adaptive"


def test_advertised_efforts_override_reviewed_family_defaults() -> None:
    profile = resolve_reasoning_profile(
        "openai",
        "openai/gpt-5.5",
        advertised_efforts=[{"effort": "low"}, {"effort": "xhigh"}],
        advertised_default="xhigh",
    )

    assert profile.efforts == ("low", "xhigh")
    assert profile.default == "xhigh"
    assert profile.source == "provider"


def test_explicit_effort_map_is_applied_before_floor_clamping() -> None:
    profile = ReasoningProfile(
        efforts=("low", "high", "max"),
        default="high",
        effort_map=(("medium", "high"), ("xhigh", "max")),
        source="reviewed",
    )

    assert choose_supported_effort(profile, "medium") == "high"
    assert choose_supported_effort(profile, "xhigh") == "max"
    assert choose_supported_effort(profile, "minimal") == "low"


def test_openrouter_uses_the_upstream_model_family_profile() -> None:
    profile = resolve_reasoning_profile(
        "openrouter",
        "openrouter/anthropic/claude-sonnet-5",
    )

    assert profile.efforts == ("low", "medium", "high", "xhigh", "max")
    assert profile.mode == "anthropic-adaptive"


def test_reasoning_profile_payload_contains_only_public_capabilities() -> None:
    profile = ReasoningProfile(
        efforts=("low", "high"),
        default="high",
        supports_off=True,
        effort_map=(("medium", "high"),),
        source="provider",
    )

    assert profile.to_payload() == {
        "efforts": ["low", "high"],
        "default": "high",
        "supports_off": True,
        "source": "provider",
    }
