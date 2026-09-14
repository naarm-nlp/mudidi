"""
Unified LLM client wrapping litellm.
Handles API key resolution, model routing, and provider-specific configuration.

Reasoning / thinking behaviour:
  - The canonical portable ladder is minimal, low, medium, high, xhigh, max.
  - Known model families clamp requests to their reviewed or provider-advertised
    subset. Unknown API-key/custom models retain the complete ladder.
  - Legacy ``none`` resolves to the known model's lowest effort, or ``low`` for
    an unknown model; it does not disable reasoning.
  - OpenRouter receives ``extra_body.reasoning``. Direct OpenAI, Anthropic, and
    Gemini calls receive LiteLLM's ``reasoning_effort``.
  - Explicit OpenAI-compatible 400/422 invalid-effort responses may retry once
    with a provider-listed neighboring effort.
  - OpenRouter provider routing: defaults to Parasail for Qwen-style models; OpenAI
                 models (``openai/gpt-*``) route via ``openai``; Anthropic Claude via
                 ``anthropic``. Override with ``OPENROUTER_PROVIDER_ORDER``.

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
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError
from mudidi.llm.reasoning import (
    ENABLED_REASONING_EFFORTS,
    ReasoningChoice as ReasoningEffort,
    choose_supported_effort,
    resolve_reasoning_profile,
)

from mudidi.llm.subscriptions import (
    AuthMode,
    CompletionRequest,
    CompletionResult,
    SubscriptionAuthError,
    SubscriptionBackend,
    SubscriptionProvider,
    SubscriptionRuntime,
    SubscriptionRuntimeRouter,
    SubscriptionUnsupportedRequest,
    resolve_subscription_model,
)

SubscriptionExecutionBackend = (
    SubscriptionBackend | SubscriptionRuntime | SubscriptionRuntimeRouter
)


def _load_dotenv() -> None:
    """Load API-key environment overrides only on an API-key code path."""
    from dotenv import load_dotenv

    load_dotenv()


class _LazyLiteLLM:
    """Load LiteLLM and dotenv only when the API-key path is used."""

    def __init__(self) -> None:
        self._module: Any | None = None

    def _load(self) -> Any:
        if self._module is None:
            _load_dotenv()
            import litellm as module

            self._module = module
            _configure_litellm_debug(module)
        return self._module

    def __getattr__(self, name: str) -> Any:
        return getattr(self._load(), name)


litellm: Any = _LazyLiteLLM()


def _configure_litellm_debug(module: Any) -> None:
    """Enable verbose LiteLLM logging on first API-key use."""
    if os.getenv("LITELLM_DEBUG", "").lower() in {"1", "true", "yes"}:
        module._turn_on_debug()
        print("  [litellm] debug logging enabled (LITELLM_DEBUG=1)")


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
        print(f"  [LLM] rate limit — pausing all workers for {pause_seconds:.0f}s")


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


def _is_litellm_exception(exc: Exception, name: str) -> bool:
    return any(cls.__name__ == name for cls in type(exc).__mro__)


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
    if _is_litellm_exception(exc, "RateLimitError"):
        return True
    if (
        _is_litellm_exception(exc, "APIError")
        and getattr(exc, "status_code", None) == 429
    ):
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
        print(f"Warning: invalid OPENROUTER_MAX_TOKENS={raw!r}; ignoring cap.")
        return None


def api_key_for_model(model: str) -> Optional[str]:
    """Resolve the API key for a given model string based on provider prefix."""
    _load_dotenv()
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


def _default_subscription_store_path() -> Path:
    """Return the local encrypted subscription-store directory."""

    for name in ("MUDIDI_SUBSCRIPTION_STORE", "MUDIDI_SUBSCRIPTION_STORE_PATH"):
        configured = os.getenv(name)
        if configured and configured.strip():
            return Path(configured).expanduser()
    return Path.home() / ".local" / "share" / "mudidi" / "subscriptions"


def resolve_subscription_runtime(
    auth: Any,
    *,
    backend: SubscriptionBackend | None = None,
    store: Any | None = None,
    store_path: str | os.PathLike[str] | None = None,
) -> SubscriptionRuntime | SubscriptionRuntimeRouter | None:
    """Resolve and eagerly authenticate every configured subscription backend."""

    raw_mode = getattr(auth, "mode", AuthMode.API_KEY)
    try:
        mode = raw_mode if isinstance(raw_mode, AuthMode) else AuthMode(raw_mode)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsupported authentication mode: {raw_mode!r}") from exc
    if mode is AuthMode.API_KEY:
        return None

    raw_providers = getattr(auth, "providers", ())
    try:
        providers = tuple(
            provider
            if isinstance(provider, SubscriptionProvider)
            else SubscriptionProvider(provider)
            for provider in raw_providers
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "auth.providers must contain only: openai, google, claude"
        ) from exc
    if not providers:
        raise ValueError("auth.providers must contain at least one provider")
    if backend is not None and store is not None:
        raise ValueError("pass a subscription backend or store, not both")
    if backend is not None and len(providers) != 1:
        raise ValueError("a single injected backend requires exactly one provider")

    selected_store = store
    if selected_store is None:
        selected_store = (
            Path(store_path).expanduser()
            if store_path is not None
            else _default_subscription_store_path()
        )
    runtimes = tuple(
        _resolve_one_subscription_runtime(
            provider,
            backend=backend if len(providers) == 1 else None,
            store=selected_store,
        )
        for provider in providers
    )
    if len(runtimes) == 1:
        return runtimes[0]
    return SubscriptionRuntimeRouter(runtimes)


def _resolve_one_subscription_runtime(
    provider: SubscriptionProvider,
    *,
    backend: SubscriptionBackend | None,
    store: Any,
) -> SubscriptionRuntime:
    if backend is None:
        if provider is SubscriptionProvider.OPENAI:
            from mudidi.llm.subscriptions.openai_codex import OpenAICodexBackend

            backend = OpenAICodexBackend(store=store)
        elif provider is SubscriptionProvider.GOOGLE:
            from mudidi.llm.subscriptions.google_antigravity import (
                GoogleAntigravityBackend,
            )

            backend = GoogleAntigravityBackend(store=store)
        else:
            from mudidi.llm.subscriptions.claude_research import ClaudeResearchBackend

            backend = ClaudeResearchBackend(store=store)

    raw_backend_provider = getattr(backend, "provider", provider)
    try:
        backend_provider = (
            raw_backend_provider
            if isinstance(raw_backend_provider, SubscriptionProvider)
            else SubscriptionProvider(raw_backend_provider)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("subscription backend has an unsupported provider") from exc
    if backend_provider is not provider:
        raise ValueError(
            f"subscription backend provider {backend_provider.value!r} "
            f"does not match configured provider {provider.value!r}"
        )

    probe_status = getattr(backend, "probe_status", None)
    status = probe_status() if callable(probe_status) else backend.status()
    expires_at = getattr(status, "expires_at", None)
    if not bool(getattr(status, "authenticated", False)) and expires_at is not None:
        backend.refresh()
        status = backend.status()
    if not bool(getattr(status, "authenticated", False)):
        raise SubscriptionAuthError(
            f"no authenticated {provider.value} subscription credential is available",
            provider=provider,
            metadata={"reason": "missing_credential"},
        )
    return SubscriptionRuntime(provider=provider, backend=backend)


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
    """Map a portable reasoning effort to OpenRouter's reasoning object."""
    reasoning_cfg: Dict[str, Any] = {
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
    for extractor in (
        _extract_openrouter_retry_after,
        _extract_retry_after_from_headers,
    ):
        retry_after = extractor(exc)
        if retry_after is not None:
            return min(max(retry_after + 1.0, 1.0), max_wait)
    return min(_exponential_retry_wait_seconds(attempt), max_wait)


def _exponential_retry_wait_seconds(attempt: int) -> float:
    """Exponential backoff capped at 30 seconds."""
    return float(min(2**attempt, 30))


def _extract_openrouter_affordable_max_tokens(exc: Exception) -> Optional[int]:
    """Parse OpenRouter 402 'can only afford N' output token budget from an error."""
    match = re.search(r"can only afford (\d+)", str(exc), re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _is_retryable_transient_error(exc: Exception) -> bool:
    """Return True for transient provider failures worth retrying."""
    is_litellm_error = _is_litellm_exception(exc, "APIError") or _is_litellm_exception(
        exc, "RateLimitError"
    )
    if is_litellm_error:
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


_ALLOWED_REASONING_CUE = re.compile(
    r"(?:must be|one of|allowed values?|supported values?(?: are)?|expected|"
    r"(?:valid|supported|allowed) levels?(?: are)?)[^.\n]+",
    re.IGNORECASE,
)
_REASONING_FIELD = re.compile(
    r"reasoning[_. ]effort|reasoning value|(?:valid|supported|allowed) "
    r"(?:levels?|values?)",
    re.IGNORECASE,
)


def _exception_text(exc: Exception) -> str:
    parts = [str(exc)]
    response = getattr(exc, "response", None)
    text = getattr(response, "text", None)
    if isinstance(text, str):
        parts.append(text)
    json_method = getattr(response, "json", None)
    if callable(json_method):
        try:
            payload = json_method()
        except Exception:
            payload = None
        if payload is not None:
            try:
                parts.append(json.dumps(payload, ensure_ascii=True))
            except (TypeError, ValueError):
                pass
    return "\n".join(parts)


def _current_reasoning_effort(params: Dict[str, Any]) -> str | None:
    direct = params.get("reasoning_effort")
    if isinstance(direct, str):
        return direct
    extra_body = params.get("extra_body")
    if not isinstance(extra_body, dict):
        return None
    reasoning = extra_body.get("reasoning")
    if isinstance(reasoning, dict) and isinstance(reasoning.get("effort"), str):
        return str(reasoning["effort"])
    return None


def _reasoning_effort_fallback(
    exc: Exception,
    params: Dict[str, Any],
) -> str | None:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status not in {400, 422}:
        return None
    if any(getattr(exc, name, None) for name in ("output", "partial_response")):
        return None
    current = _current_reasoning_effort(params)
    if current not in ENABLED_REASONING_EFFORTS:
        return None
    message = _exception_text(exc)
    if _REASONING_FIELD.search(message) is None:
        return None
    allowed_match = _ALLOWED_REASONING_CUE.search(message)
    if allowed_match is None:
        return None
    allowed = [
        effort
        for effort in ENABLED_REASONING_EFFORTS
        if re.search(
            rf"\b{re.escape(effort)}\b",
            allowed_match.group(0),
            re.IGNORECASE,
        )
        and effort != current
    ]
    if not allowed:
        return None
    special = {
        "minimal": ("low",),
        "xhigh": ("max", "high"),
        "max": ("xhigh",),
    }
    for candidate in special.get(current, ()):
        if candidate in allowed:
            return candidate
    ranks = {effort: index for index, effort in enumerate(ENABLED_REASONING_EFFORTS)}
    return min(
        allowed,
        key=lambda effort: (abs(ranks[effort] - ranks[current]), -ranks[effort]),
    )


def _replace_reasoning_effort(params: Dict[str, Any], effort: str) -> None:
    if isinstance(params.get("reasoning_effort"), str):
        params["reasoning_effort"] = effort
    extra_body = params.get("extra_body")
    if isinstance(extra_body, dict):
        reasoning = extra_body.get("reasoning")
        if isinstance(reasoning, dict) and isinstance(reasoning.get("effort"), str):
            reasoning["effort"] = effort


def _completion_with_retries(
    params: Dict[str, Any],
    *,
    _reasoning_fallback_used: bool = False,
):
    """Call litellm with transient retries and one explicit effort fallback."""

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
            if not _reasoning_fallback_used and (
                _is_direct_openai(model) or _is_openrouter(model)
            ):
                fallback = _reasoning_effort_fallback(exc, params)
                if fallback is not None:
                    _replace_reasoning_effort(params, fallback)
                    return _completion_with_retries(
                        params,
                        _reasoning_fallback_used=True,
                    )
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
    raise RuntimeError(
        f"{_retry_label_for_model(model)} completion failed without an exception"
    )


def _supports_reasoning_effort(model: str) -> bool:
    """Return True for Gemini or other models that accept litellm ``reasoning_effort``."""
    if _is_gemini3(model) or _is_gemini25(model):
        return True
    return "thinking" in model.lower()


def _reasoning_provider(model: str) -> str:
    if _is_openrouter(model):
        return "openrouter"
    if _is_gemini(model):
        return "gemini"
    if _is_direct_anthropic(model):
        return "anthropic"
    if _is_direct_openai(model):
        return "openai"
    return "custom"


def _build_params(
    model: str,
    messages: List[Dict[str, Any]],
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
        print(
            f"Warning: No API key found for model '{model}'. Relying on environment variables."
        )

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

    if reasoning_effort is not None:
        profile = resolve_reasoning_profile(_reasoning_provider(model), model)
        effort = choose_supported_effort(profile, reasoning_effort)
        if effort is not None and _is_openrouter(model):
            _apply_openrouter_reasoning(
                params,
                effort,
                exclude_in_response=True,
            )
        elif effort is not None:
            params["reasoning_effort"] = effort
            print(f"  [{_reasoning_provider(model)}] reasoning_effort={effort}")

    if top_p is not None:
        params["top_p"] = top_p
    if prompt_cache_key and _supports_prompt_cache_key(model):
        params["prompt_cache_key"] = prompt_cache_key
    if prompt_cache_retention and _supports_prompt_cache_key(model):
        params["prompt_cache_retention"] = prompt_cache_retention
    if _is_openrouter(model):
        _apply_openrouter_provider(params)

    return params


def _subscription_runtime_for_model(
    backend: SubscriptionBackend
    | SubscriptionRuntime
    | SubscriptionRuntimeRouter
    | None,
    model: str,
) -> SubscriptionBackend | SubscriptionRuntime | None:
    if isinstance(backend, SubscriptionRuntimeRouter):
        return backend.runtime_for_model(model)
    return backend


def _subscription_backend(
    backend: SubscriptionBackend
    | SubscriptionRuntime
    | SubscriptionRuntimeRouter
    | None,
    model: str,
) -> SubscriptionBackend | None:
    selected = _subscription_runtime_for_model(backend, model)
    if selected is None:
        return None
    if isinstance(selected, SubscriptionRuntime):
        return selected.backend
    return selected


def _subscription_provider(
    backend: SubscriptionBackend | SubscriptionRuntime | None,
) -> SubscriptionProvider | None:
    if backend is None:
        return None
    if isinstance(backend, SubscriptionRuntime):
        return backend.provider
    raw_provider = getattr(backend, "provider", None)
    if raw_provider is None:
        return None
    try:
        return (
            raw_provider
            if isinstance(raw_provider, SubscriptionProvider)
            else SubscriptionProvider(raw_provider)
        )
    except (TypeError, ValueError):
        return None


def _subscription_request_model(
    backend: SubscriptionBackend
    | SubscriptionRuntime
    | SubscriptionRuntimeRouter
    | None,
    model: str,
) -> str:
    """Validate and strip the selected subscription provider prefix."""

    selected = _subscription_runtime_for_model(backend, model)
    provider = _subscription_provider(selected)
    if provider is None:
        return model
    return resolve_subscription_model(provider, model)


_SUBSCRIPTION_SCHEMA_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "title",
        "description",
    }
)
_SUBSCRIPTION_SCHEMA_TYPES = frozenset(
    {"object", "array", "string", "number", "integer", "boolean", "null"}
)


def _normalize_subscription_schema(
    schema: Any,
    *,
    provider: SubscriptionProvider | str | None = None,
) -> dict[str, Any]:
    """Translate Pydantic JSON Schema to a provider-safe adapter subset.

    The provider adapters intentionally accept a small schema vocabulary.
    References and unsupported constraints are removed, but unions and
    nullable types are retained as JSON-Schema type lists.  The original
    Pydantic model remains authoritative when the response is parsed.
    """
    if not isinstance(schema, dict):
        raise TypeError("structured response schema must be a JSON object")
    selected_provider: SubscriptionProvider | None
    if provider is None:
        selected_provider = None
    else:
        try:
            selected_provider = (
                provider
                if isinstance(provider, SubscriptionProvider)
                else SubscriptionProvider(provider)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"unsupported subscription provider: {provider!r}"
            ) from exc
    strict_openai = selected_provider is SubscriptionProvider.OPENAI
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        definitions = schema.get("definitions")
    if not isinstance(definitions, dict):
        definitions = {}

    def unsupported(message: str) -> None:
        raise SubscriptionUnsupportedRequest(
            message,
            provider=selected_provider,
            metadata={"reason": "unsupported_schema"},
        )

    def copy_metadata(source: dict[str, Any], target: dict[str, Any]) -> None:
        for key in ("enum", "const", "title", "description"):
            if key in source:
                target[key] = source[key]

    def normalize(node: Any, stack: tuple[str, ...] = ()) -> dict[str, Any]:
        if not isinstance(node, dict):
            unsupported("structured response schema node is invalid")
        reference = node.get("$ref")
        if isinstance(reference, str):
            name = reference.rsplit("/", 1)[-1]
            target = definitions.get(name)
            if not isinstance(target, dict) or name in stack:
                unsupported("structured response schema reference is invalid")
            resolved = normalize(target, (*stack, name))
            copy_metadata(node, resolved)
            return resolved

        alternatives: Any = None
        for key in ("anyOf", "oneOf"):
            if key in node:
                alternatives = node[key]
                break
        if alternatives is not None:
            if not isinstance(alternatives, list) or not alternatives:
                unsupported("structured response schema union is invalid")
            normalized_alternatives: list[dict[str, Any]] = []
            for alternative in alternatives:
                if not isinstance(alternative, dict):
                    unsupported("structured response schema union branch is invalid")
                normalized_alternatives.append(normalize(alternative, stack))
            if len(normalized_alternatives) == 1:
                result = normalized_alternatives[0]
                copy_metadata(node, result)
                return result
            type_values: list[str] = []
            complex_values: list[dict[str, Any]] = []
            scalar_types = {"string", "number", "integer", "boolean", "null"}
            for normalized in normalized_alternatives:
                normalized_type = normalized.get("type")
                if isinstance(normalized_type, str):
                    if normalized_type in scalar_types:
                        type_values.append(normalized_type)
                    else:
                        complex_values.append(normalized)
                elif isinstance(normalized_type, list) and all(
                    item in scalar_types for item in normalized_type
                ):
                    type_values.extend(normalized_type)
                else:
                    complex_values.append(normalized)
            if complex_values:
                if len(complex_values) != 1 or any(
                    item != "null" for item in type_values
                ):
                    unsupported("structured response schema union is too complex")
                result = complex_values[0]
                branch_type = result.get("type")
                if isinstance(branch_type, str):
                    result["type"] = [branch_type, "null"]
                elif isinstance(branch_type, list):
                    result["type"] = list(dict.fromkeys([*branch_type, "null"]))
                else:
                    unsupported("structured response schema union is too complex")
            elif type_values:
                unique_types = list(dict.fromkeys(type_values))
                result = {
                    "type": unique_types[0] if len(unique_types) == 1 else unique_types
                }
            else:
                unsupported("structured response schema union has no usable types")
            copy_metadata(node, result)
            return result

        result: dict[str, Any] = {}
        raw_type = node.get("type")
        if isinstance(raw_type, str):
            if raw_type not in _SUBSCRIPTION_SCHEMA_TYPES:
                unsupported(
                    f"structured response schema type is unsupported: {raw_type!r}"
                )
            result["type"] = raw_type
        elif isinstance(raw_type, list):
            if not raw_type or any(
                item not in _SUBSCRIPTION_SCHEMA_TYPES for item in raw_type
            ):
                unsupported("structured response schema type union is unsupported")
            result["type"] = list(dict.fromkeys(raw_type))

        properties = node.get("properties")
        if properties is not None:
            if not isinstance(properties, dict):
                unsupported("structured response schema properties are invalid")
            existing_type = result.get("type")
            if (
                isinstance(existing_type, list)
                and "object" in existing_type
                and "null" in existing_type
            ):
                result["type"] = existing_type
            else:
                result["type"] = "object"
            result["properties"] = {
                str(key): normalize(value, stack) for key, value in properties.items()
            }
            if strict_openai:
                result["required"] = list(result["properties"])
            elif isinstance(node.get("required"), list):
                result["required"] = [
                    value for value in node["required"] if isinstance(value, str)
                ]
            additional = node.get("additionalProperties")
            if isinstance(additional, dict):
                if selected_provider is not None:
                    unsupported(
                        "schema-valued additionalProperties are unsupported by "
                        f"{selected_provider.value}"
                    )
                result["additionalProperties"] = normalize(additional, stack)
            elif isinstance(additional, bool):
                if strict_openai and additional:
                    unsupported(
                        "OpenAI strict schemas require additionalProperties=false"
                    )
                result["additionalProperties"] = additional
            else:
                result["additionalProperties"] = False
        elif result.get("type") == "object":
            additional = node.get("additionalProperties")
            if isinstance(additional, dict):
                if selected_provider is not None:
                    unsupported(
                        "schema-valued additionalProperties are unsupported by "
                        f"{selected_provider.value}"
                    )
                result["additionalProperties"] = normalize(additional, stack)
            elif isinstance(additional, bool):
                if strict_openai and additional:
                    unsupported(
                        "OpenAI strict schemas require additionalProperties=false"
                    )
                result["additionalProperties"] = additional
            else:
                result["additionalProperties"] = False
        elif "additionalProperties" in node:
            additional = node["additionalProperties"]
            if isinstance(additional, dict):
                if selected_provider is not None:
                    unsupported(
                        "schema-valued additionalProperties are unsupported by "
                        f"{selected_provider.value}"
                    )
                result["additionalProperties"] = normalize(additional, stack)
            elif isinstance(additional, bool):
                result["additionalProperties"] = additional
            else:
                unsupported("structured response additionalProperties is invalid")
        if "items" in node:
            result["items"] = normalize(node["items"], stack)
        copy_metadata(node, result)
        return {
            key: value
            for key, value in result.items()
            if key in _SUBSCRIPTION_SCHEMA_KEYS
        }

    return normalize(schema)


def _subscription_request(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: int,
    reasoning_effort: Optional[ReasoningEffort],
    schema: dict[str, Any] | None = None,
    prompt_cache_key: Optional[str] = None,
) -> CompletionRequest:
    return CompletionRequest(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        reasoning=reasoning_effort,
        schema=schema,
        cache_key=prompt_cache_key,
    )


def _subscription_result(result: Any) -> CompletionResult:
    if isinstance(result, CompletionResult):
        return result
    if isinstance(result, dict):
        return CompletionResult.model_validate(result)
    try:
        return CompletionResult(
            text=result.text,
            structured_json=getattr(result, "structured_json", None),
            usage=getattr(result, "usage", {}),
            finish_reason=getattr(result, "finish_reason", None),
            provider=getattr(result, "provider", None),
            model=getattr(result, "model", None),
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise TypeError(
            "subscription backend returned an invalid completion result"
        ) from exc


def _subscription_token_count(usage: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = usage.get(key)
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def _subscription_usage(
    model: str,
    result: CompletionResult,
) -> Dict[str, Any]:
    raw = dict(result.usage or {})
    prompt_tokens = _subscription_token_count(raw, "prompt_tokens", "input_tokens")
    completion_tokens = _subscription_token_count(
        raw, "completion_tokens", "output_tokens"
    )
    total_tokens = _subscription_token_count(raw, "total_tokens")
    if not total_tokens:
        total_tokens = prompt_tokens + completion_tokens
    usage: Dict[str, Any] = {
        "model": result.model or model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    for key in (
        "image_tokens",
        "text_tokens",
        "cached_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "reasoning_tokens",
        "response_text_tokens",
    ):
        if raw.get(key) is not None:
            usage[key] = raw[key]
    cost = raw.get("cost_usd")
    if cost is None:
        usage["cost_usd"] = None
    else:
        try:
            numeric_cost = float(cost)
        except (TypeError, ValueError):
            numeric_cost = 0.0
        usage["cost_usd"] = round(numeric_cost, 8) if numeric_cost > 0 else None
    usage["billing_mode"] = "subscription"
    return usage


def _subscription_text(result: CompletionResult) -> str:
    if not isinstance(result.text, str):
        raise ValueError("subscription backend returned non-text completion output")
    return result.text


def complete(
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: int = 64000,
    reasoning_effort: Optional[ReasoningEffort] = None,
    top_p: Optional[float] = None,
    prompt_cache_key: Optional[str] = None,
    prompt_cache_retention: Optional[str] = None,
    *,
    backend: SubscriptionExecutionBackend | None = None,
) -> str:
    """

    Args:
        model: litellm-compatible model string.
        messages: Chat messages in OpenAI format.
        max_tokens: Maximum response tokens.
        reasoning_effort: Controls the thinking/reasoning budget.

    Returns:
        Raw response content string.
    """
    selected_backend = _subscription_backend(backend, model)
    if selected_backend is not None:
        request = _subscription_request(
            model=_subscription_request_model(backend, model),
            messages=messages,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            prompt_cache_key=prompt_cache_key,
        )
        result = _subscription_result(selected_backend.complete(request))
        return _subscription_text(result)

    params = _build_params(
        model,
        messages,
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
    max_tokens: int = 64000,
    reasoning_effort: Optional[ReasoningEffort] = None,
    top_p: Optional[float] = None,
    prompt_cache_key: Optional[str] = None,
    prompt_cache_retention: Optional[str] = None,
    *,
    backend: SubscriptionExecutionBackend | None = None,
) -> tuple[str, Dict[str, Any]]:
    """
    Like ``complete`` but also returns a usage dict (tokens, cost_usd).
    """
    selected_backend = _subscription_backend(backend, model)
    if selected_backend is not None:
        request = _subscription_request(
            model=_subscription_request_model(backend, model),
            messages=messages,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            prompt_cache_key=prompt_cache_key,
        )
        result = _subscription_result(selected_backend.complete(request))
        return _subscription_text(result), _subscription_usage(model, result)

    params = _build_params(
        model,
        messages,
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


def _merge_usage_totals(
    base: Dict[str, Any], addition: Dict[str, Any]
) -> Dict[str, Any]:
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
    max_tokens: int = 64000,
    reasoning_effort: Optional[ReasoningEffort] = None,
    top_p: Optional[float] = None,
    prompt_cache_key: Optional[str] = None,
    prompt_cache_retention: Optional[str] = None,
    *,
    backend: SubscriptionExecutionBackend | None = None,
) -> tuple[T, str, Dict[str, Any]]:
    """
    Send a chat completion request with structured output enforcement.

    Returns:
        (parsed_model, raw_json_str, usage_dict) where usage_dict contains
        token counts, image tokens, and cost_usd for this call.
    """
    selected_backend = _subscription_backend(backend, model)
    if selected_backend is not None:
        schema = _normalize_subscription_schema(
            response_schema.model_json_schema(),
            provider=_subscription_provider(backend),
        )
        request = _subscription_request(
            model=_subscription_request_model(backend, model),
            messages=messages,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            schema=schema,
            prompt_cache_key=prompt_cache_key,
        )
        result = _subscription_result(selected_backend.complete_structured(request))
        content = _subscription_text(result)
        if result.structured_json is None:
            parsed = response_schema.model_validate_json(content)
        elif isinstance(result.structured_json, str):
            parsed = response_schema.model_validate_json(result.structured_json)
        else:
            parsed = response_schema.model_validate(result.structured_json)
        return parsed, content, _subscription_usage(model, result)
    params = _build_params(
        model,
        messages,
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
        if attempt > 0:
            params = _build_params(
                model,
                messages,
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
            f"{response_schema.__name__} (attempt {attempt + 1}/{max_attempts})",
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
            if (
                not _is_retryable_structured_response(
                    finish_reason=finish_reason, exc=exc
                )
                or attempt >= max_attempts - 1
            ):
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
