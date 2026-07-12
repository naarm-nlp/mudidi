"""
Unified LLM client wrapping litellm.
Handles API key resolution, model routing, and provider-specific configuration.

Reasoning / thinking behaviour by model family:
  - Gemini 2.5:  old extra_body thinkingConfig approach; we disable it by default
                 (cannot be disabled on 2.5-pro, but we try on others).
  - Gemini 3+:   use litellm's `reasoning_effort` parameter which maps to thinking_level.
                 "low"  → thinking_level: low  (fast, cheap)
                 "high" → thinking_level: high (deep reasoning)
                 "none" also maps to "low" — thinking cannot be fully disabled on Gemini 3.
  - OpenRouter reasoning models (GPT-5.x, Claude Opus 4.x, *-thinking VLMs):
                 ``extra_body.reasoning`` with ``enabled: false`` when effort is
                 ``none``, else ``effort`` + ``exclude: true`` for structured Stage 1.
  - Direct OpenAI / Anthropic (``openai/gpt-5*``, ``anthropic/claude-opus*``, etc.):
                 litellm ``reasoning_effort`` (same markers as OpenRouter slug detection).
  - OpenRouter provider routing: defaults to Parasail for Qwen-style models; OpenAI
                 models (``openai/gpt-*``) route via ``openai``; Anthropic Claude via
                 ``anthropic``. Override with ``OPENROUTER_PROVIDER_ORDER``.
  - Gemini 3+ temperature: omitted from params (litellm locks to 1.0).
  - GPT-5 family (incl. codex): only ``temperature=1`` is accepted; other values are
                 clamped with a warning.

OpenRouter env overrides:
  - ``OPENROUTER_PROVIDER_ORDER`` — comma-separated provider slugs (default: ``parasail``).
  - ``OPENROUTER_PROVIDER_IGNORE`` — slugs to skip (default: ``deepinfra,venice``).
  - ``OPENROUTER_PROVIDER_ALLOW_FALLBACKS`` — ``true``/``false`` (default: ``false``).
  - ``OPENROUTER_MAX_TOKENS`` — optional cap on completion tokens (unset = no cap).
  - ``OPENROUTER_MAX_RETRIES`` — retry attempts for 429/502/503 (default: ``8``).
  - ``GEMINI_MAX_RETRIES`` — retry attempts for direct Gemini 429/500/502/503 (default: ``8``).
  - ``STRUCTURED_MAX_RETRIES`` — retry attempts for truncated/invalid structured JSON (default: ``3``).
  - ``LLM_RATE_LIMIT_MAX_WAIT`` — cap for Retry-After / shared pause seconds (default: ``120``).
  - ``LLM_RATE_LIMIT_REDUCE_CONCURRENCY`` — on 429/rate-limit, drop page workers to 1 (default: ``true``).
  - ``LITELLM_DEBUG`` — set to ``1``/``true`` to enable verbose litellm request/response logging.
"""

import json
import os
import random
import re
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Literal, Optional, Type, TypeVar

import litellm
from dotenv import load_dotenv
from litellm.exceptions import APIError, RateLimitError
from pydantic import BaseModel, ValidationError

load_dotenv()


def _configure_litellm_debug() -> None:
    """Enable verbose litellm logging when ``LITELLM_DEBUG`` is set."""
    if os.getenv("LITELLM_DEBUG", "").lower() in {"1", "true", "yes"}:
        litellm._turn_on_debug()
        print("  [litellm] debug logging enabled (LITELLM_DEBUG=1)")


_configure_litellm_debug()

ReasoningEffort = Literal["none", "low", "medium", "high"]
T = TypeVar("T", bound=BaseModel)
RETRYABLE_HTTP_STATUS = {429, 500, 502, 503}


def _openrouter_max_retries() -> int:
    """Return configured OpenRouter retry count."""
    raw = os.getenv("OPENROUTER_MAX_RETRIES", "8")
    try:
        return max(1, int(raw))
    except ValueError:
        return 8


def _gemini_max_retries() -> int:
    """Return configured direct-Gemini retry count."""
    raw = os.getenv("GEMINI_MAX_RETRIES", os.getenv("OPENROUTER_MAX_RETRIES", "8"))
    try:
        return max(1, int(raw))
    except ValueError:
        return 8


def _structured_max_retries() -> int:
    """Return retry budget for invalid or truncated structured JSON responses."""
    raw = os.getenv("STRUCTURED_MAX_RETRIES", "3")
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def _rate_limit_max_wait() -> float:
    """Return the maximum wait seconds for rate-limit backoff and shared pauses."""
    raw = os.getenv("LLM_RATE_LIMIT_MAX_WAIT", "120")
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 120.0


class _ProviderBackoff:
    """Thread-safe pause gate so concurrent workers back off together on 429s."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._resume_at = 0.0

    def wait_if_paused(self) -> None:
        """Block until any global rate-limit pause has expired."""
        while True:
            with self._lock:
                delay = self._resume_at - time.monotonic()
            if delay <= 0:
                return
            time.sleep(delay)

    def pause(self, seconds: float) -> None:
        """Extend the global pause so all workers wait before the next attempt."""
        max_wait = _rate_limit_max_wait()
        wait = min(max(seconds, 0.0), max_wait) + random.random()
        with self._lock:
            now = time.monotonic()
            new_resume = now + wait
            if new_resume <= self._resume_at:
                return
            pause_seconds = new_resume - max(now, self._resume_at)
            self._resume_at = new_resume
        print(
            f"  [LLM] rate limit — pausing all workers for {pause_seconds:.0f}s"
        )


_backoff = _ProviderBackoff()


class _PageConcurrencyLimiter:
    """Adaptive cap on concurrent page workers (``--batch-size``) after rate limits."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._enabled = False
        self._max_workers = 1
        self._active = 0
        self._reduced = False

    def configure(self, max_workers: int) -> None:
        """Enable limiting when ``max_workers`` > 1; reset state for a new run phase."""
        with self._cond:
            workers = max(1, int(max_workers))
            self._enabled = workers > 1
            self._max_workers = workers
            self._active = 0
            self._reduced = False
            self._cond.notify_all()

    def reduce_to_serial(self) -> None:
        """Drop the in-run concurrency cap to one page at a time."""
        if not _rate_limit_reduce_concurrency_enabled():
            return
        with self._cond:
            if not self._enabled or self._max_workers <= 1 or self._reduced:
                return
            self._reduced = True
            self._max_workers = 1
            self._cond.notify_all()
        print("  [LLM] rate limit — reducing page concurrency to 1")

    def acquire(self) -> None:
        with self._cond:
            while self._enabled and self._active >= self._max_workers:
                self._cond.wait()
            self._active += 1

    def release(self) -> None:
        with self._cond:
            self._active = max(0, self._active - 1)
            self._cond.notify()

    @property
    def max_workers(self) -> int:
        with self._lock:
            return self._max_workers


_page_concurrency = _PageConcurrencyLimiter()


def _rate_limit_reduce_concurrency_enabled() -> bool:
    """Return True when 429s should drop ``--batch-size`` workers to 1 for the rest of the run."""
    return os.getenv("LLM_RATE_LIMIT_REDUCE_CONCURRENCY", "true").lower() in {
        "1",
        "true",
        "yes",
    }


def configure_page_concurrency(max_workers: int) -> None:
    """Set the page worker cap for the current run phase (see ``--batch-size``)."""
    _page_concurrency.configure(max_workers)


@contextmanager
def page_concurrency_slot() -> Iterator[None]:
    """Hold one page-worker slot while ``--batch-size`` > 1."""
    _page_concurrency.acquire()
    try:
        yield
    finally:
        _page_concurrency.release()


def wait_for_provider_backoff() -> None:
    """Wait until a shared provider rate-limit pause clears (for page-level retries)."""
    _backoff.wait_if_paused()


def _is_rate_limit_error(exc: Exception) -> bool:
    """Return True when an exception indicates provider throttling (429 / rate limit)."""
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIError) and getattr(exc, "status_code", None) == 429:
        return True
    message = str(exc).lower()
    return "429" in message or "rate limit" in message


def _is_gemini(model: str) -> bool:
    """Return True for direct Gemini / Google model strings."""
    m = model.lower()
    return "gemini" in m or m.startswith("google/")


def _openrouter_max_tokens_cap() -> Optional[int]:
    """Return an optional OpenRouter completion cap from ``OPENROUTER_MAX_TOKENS``."""
    raw = os.getenv("OPENROUTER_MAX_TOKENS")
    if raw is None or not raw.strip():
        return None
    try:
        return max(1024, int(raw.strip()))
    except ValueError:
        print(
            f"Warning: invalid OPENROUTER_MAX_TOKENS={raw!r}; ignoring cap."
        )
        return None


def api_key_for_model(model: str) -> Optional[str]:
    """Resolve the API key for a given model string based on provider prefix."""
    model_lower = model.lower()
    if "openrouter" in model_lower:
        return os.getenv("OPEN_ROUTER_API_KEY")
    if "gemini" in model_lower or "google" in model_lower:
        return os.getenv("GEMINI_API_KEY")
    if "claude" in model_lower or "anthropic" in model_lower:
        return os.getenv("ANTHROPIC_API_KEY")
    if "gpt" in model_lower or "openai" in model_lower:
        return os.getenv("OPENAI_API_KEY")
    return None


def _is_gemini3(model: str) -> bool:
    """Return True for Gemini 3+ model strings."""
    m = model.lower()
    return "gemini" in m and any(
        tag in m for tag in ("gemini-3", "gemini-3-flash", "gemini-3-pro", "gemini-3.1")
    )


def _is_gemini25(model: str) -> bool:
    """Return True for Gemini 2.5 model strings."""
    m = model.lower()
    return "gemini" in m and "2.5" in m


def _is_openrouter(model: str) -> bool:
    """Return True for litellm OpenRouter model strings."""
    return "openrouter" in model.lower()


def _model_slug(model: str) -> str:
    """Return the model id portion after an optional ``provider/`` prefix."""
    return model.lower().split("/", maxsplit=1)[-1]


_REASONING_SLUG_MARKERS = (
    "gpt-5",
    "o1",
    "o3",
    "o4",
    "claude-opus",
    "claude-4.",
    "claude-4-",
)


def _slug_supports_reasoning_controls(slug: str) -> bool:
    """Return True when a model slug denotes reasoning-effort controls."""
    if "thinking" in slug:
        return True
    return any(tag in slug for tag in _REASONING_SLUG_MARKERS)


def _openrouter_has_thinking_model(model: str) -> bool:
    """Return True when the OpenRouter model id denotes a reasoning/thinking variant."""
    return _is_openrouter(model) and "thinking" in model.lower()


def _openrouter_supports_reasoning_api(model: str) -> bool:
    """Return True when an OpenRouter model accepts ``extra_body.reasoning``."""
    if not _is_openrouter(model):
        return False
    if _openrouter_has_thinking_model(model):
        return True
    return _slug_supports_reasoning_controls(_model_slug(model))


def _is_direct_openai(model: str) -> bool:
    """Return True for direct OpenAI model strings (not OpenRouter or Gemini)."""
    if _is_openrouter(model) or _is_gemini(model):
        return False
    m = model.lower()
    return "openai" in m or "gpt" in m or m.startswith("gpt-")


def _is_direct_anthropic(model: str) -> bool:
    """Return True for direct Anthropic model strings (not OpenRouter)."""
    if _is_openrouter(model) or _is_gemini(model):
        return False
    m = model.lower()
    return "anthropic" in m or "claude" in m


def _direct_supports_reasoning_effort(model: str) -> bool:
    """Return True when a direct OpenAI/Anthropic call should receive ``reasoning_effort``."""
    if _is_direct_openai(model) or _is_direct_anthropic(model):
        return _slug_supports_reasoning_controls(_model_slug(model))
    return False


def _supports_prompt_cache_key(model: str) -> bool:
    """Return True when litellm should receive OpenAI prompt cache routing hints."""
    return _is_direct_openai(model)


def supports_prompt_cache_key(model: str) -> bool:
    """Return True when ``prompt_cache_key`` should be sent for ``model``."""
    return _supports_prompt_cache_key(model)


def _apply_openrouter_reasoning(
    params: Dict[str, Any],
    reasoning_effort: ReasoningEffort,
    *,
    exclude_in_response: bool,
) -> None:
    """Map stage reasoning_effort to OpenRouter's ``reasoning`` object (via extra_body)."""
    if reasoning_effort == "none":
        reasoning_cfg: Dict[str, Any] = {"enabled": False}
    else:
        reasoning_cfg = {
            "effort": reasoning_effort,
            "exclude": exclude_in_response,
        }
    extra_body = dict(params.get("extra_body") or {})
    extra_body["reasoning"] = reasoning_cfg
    params["extra_body"] = extra_body
    print(f"  [OpenRouter] reasoning={reasoning_cfg}")


def _parse_csv_env(name: str, default: str) -> List[str]:
    """Parse a comma-separated env var into a trimmed, non-empty slug list."""
    raw = os.getenv(name, default)
    return [part.strip() for part in raw.split(",") if part.strip()]


def _default_openrouter_provider_order(model: str) -> List[str]:
    """Pick a default OpenRouter provider order when ``OPENROUTER_PROVIDER_ORDER`` is unset."""
    slug = model.lower().split("/", maxsplit=1)[-1]
    if slug.startswith("openai/") or "gpt-" in slug:
        return ["openai"]
    if slug.startswith("anthropic/") or "claude" in slug:
        return ["anthropic"]
    return ["parasail"]


def _openrouter_provider_order(model: str) -> List[str]:
    """Resolve provider order from env override or model-family default."""
    model_default = _default_openrouter_provider_order(model)
    raw = os.getenv("OPENROUTER_PROVIDER_ORDER")
    if raw is not None:
        order = _parse_csv_env("OPENROUTER_PROVIDER_ORDER", "")
        if not order:
            return []
        # .env often pins parasail for Qwen; GPT/Claude are not on Parasail.
        if order == ["parasail"] and model_default != ["parasail"]:
            return model_default
        return order
    return model_default


def _apply_openrouter_provider(params: Dict[str, Any]) -> None:
    """Route OpenRouter calls to a provider that hosts the requested model."""
    model = str(params["model"])
    order = _openrouter_provider_order(model)
    if not order:
        return

    default_fallbacks = "false" if order == ["parasail"] else "true"
    allow_fallbacks = os.getenv(
        "OPENROUTER_PROVIDER_ALLOW_FALLBACKS", default_fallbacks
    ).lower() in {"1", "true", "yes"}
    provider_cfg: Dict[str, Any] = {
        "only": order,
        "order": order,
        "allow_fallbacks": allow_fallbacks,
        "require_parameters": False,
    }

    ignored = _parse_csv_env("OPENROUTER_PROVIDER_IGNORE", "deepinfra,venice")
    if ignored:
        provider_cfg["ignore"] = ignored

    extra_body = dict(params.get("extra_body") or {})
    extra_body["provider"] = provider_cfg
    params["extra_body"] = extra_body
    print(f"  [OpenRouter] provider={provider_cfg}")


def _extract_openrouter_retry_after(exc: Exception) -> Optional[float]:
    """Parse OpenRouter ``retry_after_seconds`` from an exception payload."""
    text = str(exc)
    start = text.find("{")
    if start >= 0:
        try:
            payload = json.loads(text[start:])
            metadata = payload.get("error", {}).get("metadata", {})
            for key in ("retry_after_seconds", "retry_after_seconds_raw"):
                value = metadata.get(key)
                if value is not None:
                    return float(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    match = re.search(r'"retry_after_seconds(?:_raw)?"\s*:\s*([\d.]+)', text)
    if match:
        return float(match.group(1))
    return None


def _extract_retry_after_from_headers(exc: Exception) -> Optional[float]:
    """Parse ``Retry-After`` from an HTTP response attached to an exception."""
    response = getattr(exc, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    for key in ("Retry-After", "retry-after"):
        value = headers.get(key) if hasattr(headers, "get") else None
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _retry_wait_seconds(exc: Exception, attempt: int) -> float:
    """Compute retry wait from provider hints or exponential backoff."""
    max_wait = _rate_limit_max_wait()
    for extractor in (_extract_openrouter_retry_after, _extract_retry_after_from_headers):
        retry_after = extractor(exc)
        if retry_after is not None:
            return min(max(retry_after + 1.0, 1.0), max_wait)
    return min(_exponential_retry_wait_seconds(attempt), max_wait)


def _exponential_retry_wait_seconds(attempt: int) -> float:
    """Exponential backoff capped at 30 seconds."""
    return float(min(2 ** attempt, 30))


def _extract_openrouter_affordable_max_tokens(exc: Exception) -> Optional[int]:
    """Parse OpenRouter 402 'can only afford N' output token budget from an error."""
    match = re.search(r"can only afford (\d+)", str(exc), re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _is_retryable_transient_error(exc: Exception) -> bool:
    """Return True for transient provider failures worth retrying."""
    if isinstance(exc, (APIError, RateLimitError)):
        status_code = getattr(exc, "status_code", None)
        if status_code in RETRYABLE_HTTP_STATUS:
            return True
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "429",
            "500",
            "502",
            "503",
            "bad gateway",
            "rate limit",
            "unavailable",
            "service unavailable",
            "unable to get json",
            "expecting value",
        )
    )


def is_retryable_transient_error(exc: Exception) -> bool:
    """Return True when a failed page/worker may succeed after backoff."""
    return _is_retryable_transient_error(exc)


def _max_retries_for_model(model: str) -> int:
    """Return retry budget for a model family (1 = no retries)."""
    if _is_openrouter(model):
        return _openrouter_max_retries()
    if _is_gemini(model):
        return _gemini_max_retries()
    return 1


def _retry_label_for_model(model: str) -> str:
    if _is_openrouter(model):
        return "OpenRouter"
    if _is_gemini(model):
        return "Gemini"
    return "LLM"


def _completion_with_retries(params: Dict[str, Any]):
    """Call litellm.completion with backoff on transient provider errors."""
    model = str(params["model"])
    max_retries = _max_retries_for_model(model)
    credit_adjustments = 3
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        _backoff.wait_if_paused()
        try:
            return litellm.completion(**params)
        except Exception as exc:
            last_exc = exc
            if _is_openrouter(model):
                affordable = _extract_openrouter_affordable_max_tokens(exc)
                current = int(params.get("max_tokens", 0))
                if (
                    affordable is not None
                    and affordable < current
                    and credit_adjustments > 0
                ):
                    credit_adjustments -= 1
                    lowered = max(1024, affordable - 64)
                    print(
                        f"  [OpenRouter] credit reservation exceeded: "
                        f"max_tokens {current} → {lowered}"
                    )
                    params["max_tokens"] = lowered
                    continue

            if max_retries <= 1 or not _is_retryable_transient_error(exc):
                raise
            if not (_is_openrouter(model) or _is_gemini(model)):
                raise
            if attempt >= max_retries - 1:
                raise
            wait_seconds = _retry_wait_seconds(exc, attempt)
            label = _retry_label_for_model(model)
            print(
                f"  [{label}] transient error, retry "
                f"{attempt + 2}/{max_retries} in {wait_seconds:.0f}s"
            )
            if _is_rate_limit_error(exc):
                _page_concurrency.reduce_to_serial()
            _backoff.pause(wait_seconds)
            _backoff.wait_if_paused()
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{_retry_label_for_model(model)} completion failed without an exception")


def _supports_reasoning_effort(model: str) -> bool:
    """Return True for Gemini or other models that accept litellm ``reasoning_effort``."""
    if _is_gemini3(model) or _is_gemini25(model):
        return True
    return "thinking" in model.lower()


def _requires_temperature_one(model: str) -> bool:
    """Return True when the provider only accepts ``temperature=1`` (GPT-5 family)."""
    slug = _model_slug(model)
    return "gpt-5" in slug


def _effective_temperature(model: str, temperature: float) -> float:
    """Resolve sampling temperature for ``model``, applying provider constraints."""
    if _requires_temperature_one(model):
        return 1.0
    return temperature


def _set_temperature_param(
    params: Dict[str, Any], model: str, temperature: float
) -> None:
    """Attach ``temperature`` to completion params when the model supports it."""
    if _is_gemini3(model):
        return
    effective = _effective_temperature(model, temperature)
    if _requires_temperature_one(model) and abs(temperature - effective) > 1e-6:
        print(
            f"  [{_retry_label_for_model(model)}] temperature {temperature} → {effective} "
            "(GPT-5 family only supports temperature=1)"
        )
    params["temperature"] = effective


def _build_params(
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float,
    max_tokens: int,
    reasoning_effort: Optional[ReasoningEffort],
    *,
    top_p: Optional[float] = None,
    prompt_cache_key: Optional[str] = None,
    prompt_cache_retention: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble the litellm.completion kwargs, applying model-family-specific rules."""
    api_key = api_key_for_model(model)
    if not api_key:
        print(f"Warning: No API key found for model '{model}'. Relying on environment variables.")

    params: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if _is_openrouter(model):
        token_cap = _openrouter_max_tokens_cap()
        if token_cap is not None and max_tokens > token_cap:
            print(f"  [OpenRouter] capping max_tokens {max_tokens} → {token_cap}")
            params["max_tokens"] = token_cap
    if api_key:
        params["api_key"] = api_key

    if _is_gemini3(model):
        effort = "low" if reasoning_effort in (None, "none") else reasoning_effort
        params["reasoning_effort"] = effort
        print(f"  [Gemini 3] reasoning_effort={effort} (temperature fixed at 1.0 by litellm)")
    elif _is_gemini25(model):
        _set_temperature_param(params, model, temperature)
        if reasoning_effort in (None, "none", "low"):
            params["extra_body"] = {
                "generationConfig": {"thinking": {"thinkingConfig": {"mode": "DISABLED"}}}
            }
            print("  [Gemini 2.5] thinking disabled via extra_body")
        else:
            params["reasoning_effort"] = reasoning_effort
            print(f"  [Gemini 2.5] reasoning_effort={reasoning_effort}")
    else:
        _set_temperature_param(params, model, temperature)
        if reasoning_effort and _openrouter_supports_reasoning_api(model):
            _apply_openrouter_reasoning(
                params,
                reasoning_effort,
                exclude_in_response=True,
            )
        elif reasoning_effort and _direct_supports_reasoning_effort(model):
            params["reasoning_effort"] = reasoning_effort
            label = "OpenAI" if _is_direct_openai(model) else "Anthropic"
            print(f"  [{label}] reasoning_effort={reasoning_effort}")
        elif reasoning_effort and _supports_reasoning_effort(model):
            params["reasoning_effort"] = reasoning_effort

    if top_p is not None:
        params["top_p"] = top_p
    if prompt_cache_key and _supports_prompt_cache_key(model):
        params["prompt_cache_key"] = prompt_cache_key
    if prompt_cache_retention and _supports_prompt_cache_key(model):
        params["prompt_cache_retention"] = prompt_cache_retention
    if _is_openrouter(model):
        _apply_openrouter_provider(params)

    return params


def complete(
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.1,
    max_tokens: int = 64000,
    reasoning_effort: Optional[ReasoningEffort] = None,
    top_p: Optional[float] = None,
    prompt_cache_key: Optional[str] = None,
    prompt_cache_retention: Optional[str] = None,
) -> str:
    """
    Send a chat completion request via litellm and return the response text.

    Args:
        model: litellm-compatible model string.
        messages: Chat messages in OpenAI format.
        temperature: Sampling temperature. Ignored for Gemini 3+ (locked to 1.0).
        max_tokens: Maximum response tokens.
        reasoning_effort: Controls the thinking/reasoning budget.

    Returns:
        Raw response content string.
    """
    params = _build_params(
        model,
        messages,
        temperature,
        max_tokens,
        reasoning_effort,
        top_p=top_p,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=prompt_cache_retention,
    )

    print(f"Calling LLM API with model: {model}...")
    response = _completion_with_retries(params)

    print(f"Response received. Model: {response.model}")
    print(f"Finish reason: {response.choices[0].finish_reason}")
    print(f"Usage: {response.usage}")

    content = response.choices[0].message.content
    if content is None:
        raise ValueError(
            "API returned None content. This may be due to token limits or API configuration."
        )

    print(f"Content length: {len(content)} characters")
    return content


def complete_with_usage(
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.1,
    max_tokens: int = 64000,
    reasoning_effort: Optional[ReasoningEffort] = None,
    top_p: Optional[float] = None,
    prompt_cache_key: Optional[str] = None,
    prompt_cache_retention: Optional[str] = None,
) -> tuple[str, Dict[str, Any]]:
    """
    Like ``complete`` but also returns a usage dict (tokens, cost_usd).
    """
    params = _build_params(
        model,
        messages,
        temperature,
        max_tokens,
        reasoning_effort,
        top_p=top_p,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=prompt_cache_retention,
    )

    print(f"Calling LLM API with model: {model}...")
    response = _completion_with_retries(params)

    print(f"Response received. Model: {response.model}")
    print(f"Finish reason: {response.choices[0].finish_reason}")
    print(f"Usage: {response.usage}")

    content = response.choices[0].message.content
    if content is None:
        raise ValueError(
            "API returned None content. This may be due to token limits or API configuration."
        )

    print(f"Content length: {len(content)} characters")
    return content, _extract_usage(model, response)


def _token_detail_value(details: Any, key: str) -> Optional[int]:
    """Read an integer token count from a litellm token-details dict or wrapper."""
    if details is None:
        return None
    if isinstance(details, dict):
        value = details.get(key)
    else:
        value = getattr(details, key, None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _merge_usage_totals(base: Dict[str, Any], addition: Dict[str, Any]) -> Dict[str, Any]:
    """Accumulate token counts and cost across structured-output retry attempts."""
    merged = dict(base)
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        merged[key] = int(merged.get(key, 0) or 0) + int(addition.get(key, 0) or 0)
    for key in (
        "image_tokens",
        "text_tokens",
        "cached_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "reasoning_tokens",
        "response_text_tokens",
    ):
        if addition.get(key) is not None:
            merged[key] = int(merged.get(key, 0) or 0) + int(addition[key])
    base_cost = merged.get("cost_usd")
    add_cost = addition.get("cost_usd")
    if base_cost is not None and add_cost is not None:
        merged["cost_usd"] = round(float(base_cost) + float(add_cost), 8)
    elif add_cost is not None:
        merged["cost_usd"] = add_cost
    return merged


def _is_retryable_structured_response(
    *,
    finish_reason: Optional[str],
    exc: Optional[Exception],
) -> bool:
    """Return True when a structured response should be re-requested."""
    if finish_reason in {"length", "tool_calls"}:
        return True
    if isinstance(exc, ValidationError):
        for error in exc.errors():
            if error.get("type") == "json_invalid":
                return True
    return False


def complete_structured(
    model: str,
    messages: List[Dict[str, Any]],
    response_schema: Type[T],
    temperature: float = 0.1,
    max_tokens: int = 64000,
    reasoning_effort: Optional[ReasoningEffort] = None,
    top_p: Optional[float] = None,
    prompt_cache_key: Optional[str] = None,
    prompt_cache_retention: Optional[str] = None,
) -> tuple[T, str, Dict[str, Any]]:
    """
    Send a chat completion request with structured output enforcement.

    Returns:
        (parsed_model, raw_json_str, usage_dict) where usage_dict contains
        token counts, image tokens, and cost_usd for this call.
    """
    params = _build_params(
        model,
        messages,
        temperature,
        max_tokens,
        reasoning_effort,
        top_p=top_p,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=prompt_cache_retention,
    )
    params["response_format"] = response_schema

    max_attempts = _structured_max_retries()
    usage_total: Dict[str, Any] = {"model": model}
    last_exc: Optional[Exception] = None
    last_content: Optional[str] = None
    last_finish_reason: Optional[str] = None

    for attempt in range(max_attempts):
        attempt_temperature = _effective_temperature(
            model, min(temperature + 0.2 * attempt, 0.8)
        )
        if attempt > 0:
            params = _build_params(
                model,
                messages,
                attempt_temperature,
                max_tokens,
                reasoning_effort,
                top_p=top_p,
                prompt_cache_key=prompt_cache_key,
                prompt_cache_retention=prompt_cache_retention,
            )
            params["response_format"] = response_schema
            params["frequency_penalty"] = min(0.3 * attempt, 1.0)
            params["presence_penalty"] = min(0.2 * attempt, 0.8)
            if _is_openrouter(model):
                extra_body = dict(params.get("extra_body") or {})
                provider_cfg = dict(extra_body.get("provider") or {})
                provider_cfg["allow_fallbacks"] = True
                extra_body["provider"] = provider_cfg
                params["extra_body"] = extra_body
        print(
            f"Calling LLM API (structured) with model: {model} → "
            f"{response_schema.__name__} (attempt {attempt + 1}/{max_attempts}, "
            f"temperature={attempt_temperature})",
            flush=True,
        )
        response = _completion_with_retries(params)

        print(f"Response received. Model: {response.model}", flush=True)
        finish_reason = response.choices[0].finish_reason
        last_finish_reason = finish_reason
        print(f"Finish reason: {finish_reason}", flush=True)
        print(f"Usage: {response.usage}", flush=True)

        content = response.choices[0].message.content
        last_content = content
        if content is None:
            last_exc = ValueError(
                "API returned None content. This may be due to token limits or API configuration."
            )
            if attempt >= max_attempts - 1:
                raise last_exc
            wait_seconds = _exponential_retry_wait_seconds(attempt)
            print(
                f"  [structured] empty content, retry "
                f"{attempt + 2}/{max_attempts} in {wait_seconds:.0f}s",
                flush=True,
            )
            time.sleep(wait_seconds)
            continue

        usage_total = _merge_usage_totals(usage_total, _extract_usage(model, response))
        try:
            return response_schema.model_validate_json(content), content, usage_total
        except ValidationError as exc:
            last_exc = exc
            if not _is_retryable_structured_response(
                finish_reason=finish_reason, exc=exc
            ) or attempt >= max_attempts - 1:
                raise
            wait_seconds = _exponential_retry_wait_seconds(attempt)
            print(
                f"  [structured] invalid/truncated JSON (finish_reason={finish_reason!r}), "
                f"retry {attempt + 2}/{max_attempts} in {wait_seconds:.0f}s",
                flush=True,
            )
            time.sleep(wait_seconds)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(
        "Structured completion failed without a validation error "
        f"(finish_reason={last_finish_reason!r}, content_len="
        f"{len(last_content) if last_content is not None else 0})"
    )


def _extract_usage(model: str, response) -> Dict[str, Any]:
    """Extract token counts and estimated cost from a litellm completion response."""
    u = response.usage
    usage: Dict[str, Any] = {
        "model": model,
        "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
        "total_tokens": getattr(u, "total_tokens", 0) or 0,
    }

    # Pull out image / text token breakdown when available (Gemini reports these)
    pd = getattr(u, "prompt_tokens_details", None)
    if pd:
        if isinstance(pd, dict):
            usage["image_tokens"] = pd.get("image_tokens")
            usage["text_tokens"] = pd.get("text_tokens")
            usage["cached_tokens"] = pd.get("cached_tokens")
        else:
            usage["image_tokens"] = getattr(pd, "image_tokens", None)
            usage["text_tokens"] = getattr(pd, "text_tokens", None)
            usage["cached_tokens"] = getattr(pd, "cached_tokens", None)
    for key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
        value = getattr(u, key, None)
        if value is not None:
            usage[key] = value

    # Completion breakdown: reasoning vs visible response text (Gemini, OpenAI o/GPT-5,
    # Anthropic extended thinking, OpenRouter passthrough — all via litellm Usage).
    cd = getattr(u, "completion_tokens_details", None)
    reasoning_tokens = _token_detail_value(cd, "reasoning_tokens")
    if reasoning_tokens is None:
        reasoning_tokens = _token_detail_value(u, "reasoning_tokens")
    response_text_tokens = _token_detail_value(cd, "text_tokens")
    if reasoning_tokens is not None:
        usage["reasoning_tokens"] = reasoning_tokens
    if response_text_tokens is not None:
        usage["response_text_tokens"] = response_text_tokens

    # Attempt cost calculation via litellm's built-in pricing table
    try:
        cost = litellm.completion_cost(completion_response=response)
        usage["cost_usd"] = round(cost, 8)
    except Exception:
        usage["cost_usd"] = None

    return usage
