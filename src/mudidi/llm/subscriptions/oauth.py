"""Provider-neutral OAuth callback and bounded HTTPS token transport.

Provider adapters own their HTTPS endpoint constants and authorization scopes.
This module only enforces transport and callback safety, and accepts injected
callables so tests never need a real browser or provider.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import ipaddress
from dataclasses import dataclass
import http.server
import json
import math
import secrets
import ssl
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import SplitResult, parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
)
import webbrowser

import certifi
from pydantic import SecretStr

from mudidi.llm.subscriptions.pkce import PkceChallenge
from mudidi.llm.subscriptions.types import (
    SubscriptionAuthError,
    SubscriptionCredential,
    SubscriptionError,
    SubscriptionLoginTransaction,
    SubscriptionProvider,
    SubscriptionTransportError,
)

_DEFAULT_CALLBACK_TIMEOUT = 300.0
_MAX_CALLBACK_TIMEOUT = 600.0
_DEFAULT_HTTP_TIMEOUT = 30.0
_MAX_HTTP_TIMEOUT = 300.0
_DEFAULT_MAX_RESPONSE_BYTES = 1_048_576
_MAX_RESPONSE_BYTES = 8 * 1_048_576
_MAX_EXPIRES_IN_SECONDS = 10**9
_MAX_CALLBACK_URL_BYTES = 16 * 1024
_MAX_CALLBACK_VALUE_LENGTH = 4096


def _callback_response_html(*, success: bool) -> str:
    """Render the secret-free browser page shown after a loopback callback."""

    if success:
        state = "success"
        title = "Authentication successful"
        symbol = "✓"
        message = "You have successfully logged in."
        instruction = "Please close this tab manually."
        role = "status"
    else:
        state = "failure"
        title = "Authentication failed"
        symbol = "×"
        message = "MUDIDI could not complete authentication."
        instruction = "Please close this tab and try again from MUDIDI."
        role = "alert"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>MUDIDI · {title}</title>
  <style>
    :root {{
      color-scheme: dark;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system,
        BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #fff;
      background: #000;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      padding: 24px;
      background-color: #000;
      background-image:
        radial-gradient(circle at 12% 8%, rgba(167, 139, 250, .14), transparent 34%),
        radial-gradient(circle at 88% 20%, rgba(74, 222, 128, .07), transparent 30%),
        radial-gradient(rgba(255, 255, 255, .1) 1px, transparent 1px);
      background-size: auto, auto, 24px 24px;
    }}
    .card {{
      width: min(100%, 430px);
      padding: 42px 38px 40px;
      text-align: center;
      background: #18181b;
      border: 2px solid #fff;
      box-shadow: 8px 8px 0 #a78bfa;
    }}
    .brand {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 32px;
      font-size: 13px;
      font-weight: 900;
      letter-spacing: .16em;
    }}
    .brand-mark {{
      width: 30px;
      height: 30px;
      display: grid;
      place-items: center;
      color: #000;
      background: #a78bfa;
      border: 2px solid #fff;
      box-shadow: 3px 3px 0 #fff;
      font-size: 15px;
      letter-spacing: 0;
    }}
    .result-icon {{
      width: 58px;
      height: 58px;
      margin: 0 auto 24px;
      display: grid;
      place-items: center;
      border: 2px solid currentColor;
      border-radius: 50%;
      font-size: 32px;
      font-weight: 700;
      line-height: 1;
    }}
    [data-auth-result="success"] .result-icon {{ color: #4ade80; }}
    [data-auth-result="failure"] .result-icon {{ color: #f87171; }}
    h1 {{
      margin: 0 0 14px;
      font-size: clamp(22px, 5vw, 28px);
      font-weight: 900;
      letter-spacing: -.025em;
      text-transform: uppercase;
    }}
    p {{
      margin: 0;
      color: #d4d4d8;
      font-size: 15px;
      line-height: 1.65;
    }}
    .instruction {{ color: #a1a1aa; }}
    @media (max-width: 480px) {{
      .card {{ padding: 36px 24px 34px; box-shadow: 6px 6px 0 #a78bfa; }}
    }}
  </style>
</head>
<body>
  <main class="card" data-auth-result="{state}" role="{role}" aria-labelledby="result-title">
    <div class="brand" aria-label="MUDIDI">
      <span class="brand-mark" aria-hidden="true">M</span>
      <span>MUDIDI</span>
    </div>
    <div class="result-icon" aria-hidden="true">{symbol}</div>
    <h1 id="result-title">{title}</h1>
    <p>{message}</p>
    <p class="instruction">{instruction}</p>
  </main>
</body>
</html>
"""


def is_allowed_callback_source(
    host: str | None,
    *,
    container_mode: bool = False,
) -> bool:
    """Return whether a callback source is local for the selected bind mode."""

    if not isinstance(host, str):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if container_mode:
        return address.is_loopback or (
            isinstance(address, ipaddress.IPv4Address)
            and address.is_private
            and not address.is_unspecified
        )
    return address == ipaddress.IPv4Address("127.0.0.1")


def _provider_value(
    provider: SubscriptionProvider | str | None,
) -> SubscriptionProvider | str | None:
    if provider is None or isinstance(provider, SubscriptionProvider):
        return provider
    try:
        return SubscriptionProvider(provider)
    except ValueError:
        return provider


def _error(
    error_type: type[SubscriptionError],
    message: str,
    *,
    provider: SubscriptionProvider | str | None = None,
    status: int | None = None,
    reason: str,
) -> SubscriptionError:
    return error_type(
        message,
        provider=_provider_value(provider),
        status=status,
        metadata={"reason": reason},
    )


def _timeout(value: float | int | None, *, default: float, maximum: float) -> float:
    selected = default if value is None else float(value)
    if not math.isfinite(selected) or selected <= 0:
        raise ValueError("timeout must be a finite positive number")
    return min(selected, maximum)


def _host_set(
    allowed_hosts: Mapping[str, Any]
    | set[str]
    | frozenset[str]
    | list[str]
    | tuple[str, ...]
    | None,
) -> frozenset[str] | None:
    if allowed_hosts is None:
        return None
    return frozenset(
        str(host).strip().lower().rstrip(".")
        for host in allowed_hosts
        if str(host).strip()
    )


def validate_provider_url(
    url: str,
    *,
    allowed_hosts: set[str]
    | frozenset[str]
    | tuple[str, ...]
    | list[str]
    | None = None,
) -> SplitResult:
    """Validate a provider endpoint as HTTPS, optionally host-allowlisted."""

    if (
        not isinstance(url, str)
        or len(url.encode("utf-8", "ignore")) > _MAX_CALLBACK_URL_BYTES
    ):
        raise _error(
            SubscriptionTransportError,
            "OAuth endpoint is invalid",
            reason="invalid_endpoint",
        )
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise _error(
            SubscriptionTransportError,
            "OAuth endpoint is invalid",
            reason="invalid_endpoint",
        ) from None
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise _error(
            SubscriptionTransportError,
            "OAuth endpoint must use HTTPS",
            reason="invalid_endpoint",
        )
    normalized_allowed = _host_set(allowed_hosts)
    normalized_hostname = hostname.lower().rstrip(".")
    if normalized_allowed is not None and normalized_hostname not in normalized_allowed:
        raise _error(
            SubscriptionTransportError,
            "OAuth endpoint is not allowlisted",
            reason="endpoint_not_allowlisted",
        )
    return parsed


def validate_loopback_redirect_uri(uri: str) -> SplitResult:
    """Validate an HTTP redirect URI bound specifically to loopback."""

    if (
        not isinstance(uri, str)
        or len(uri.encode("utf-8", "ignore")) > _MAX_CALLBACK_URL_BYTES
    ):
        raise _error(
            SubscriptionAuthError,
            "OAuth redirect URI is invalid",
            reason="invalid_redirect_uri",
        )
    try:
        parsed = urlsplit(uri)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise _error(
            SubscriptionAuthError,
            "OAuth redirect URI is invalid",
            reason="invalid_redirect_uri",
        ) from None
    if (
        parsed.scheme.lower() != "http"
        or hostname not in {"127.0.0.1", "localhost"}
        or port is None
        or not 1 <= port <= 65535
        or not parsed.path.startswith("/")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise _error(
            SubscriptionAuthError,
            "OAuth redirect URI must be a loopback URL",
            reason="invalid_redirect_uri",
        )
    return parsed


@dataclass(frozen=True, slots=True)
class OAuthCallback:
    """Validated authorization input returned from a loopback callback."""

    code: str
    state: str

    def __repr__(self) -> str:
        # Authorization codes are credentials at this boundary.
        return "OAuthCallback(code='[redacted]', state='[redacted]')"


def begin_login_transaction(
    build_authorization_url: Callable[[PkceChallenge, str], str],
    redirect_uri: str,
) -> SubscriptionLoginTransaction:
    """Create one loopback-only PKCE transaction without waiting for a callback."""

    try:
        validate_loopback_redirect_uri(redirect_uri)
        challenge = PkceChallenge.generate()
        authorization_url = build_authorization_url(challenge, redirect_uri)
    except SubscriptionError:
        raise
    except Exception:
        raise _error(
            SubscriptionAuthError,
            "OAuth authorization could not be started",
            reason="authorization_start_failed",
        ) from None
    return SubscriptionLoginTransaction(
        authorization_url=authorization_url,
        redirect_uri=redirect_uri,
        state=challenge.state,
        verifier=SecretStr(challenge.verifier),
    )


def validate_login_transaction(
    transaction: SubscriptionLoginTransaction,
    *,
    code: str,
    state: str,
    provider: SubscriptionProvider,
) -> None:
    """Validate one callback against its server-held PKCE transaction."""

    if not isinstance(transaction, SubscriptionLoginTransaction):
        raise _error(
            SubscriptionAuthError,
            "OAuth login transaction is invalid",
            provider=provider,
            reason="invalid_transaction",
        )
    if (
        not isinstance(code, str)
        or not code.strip()
        or not isinstance(state, str)
        or not state
        or not secrets.compare_digest(state, transaction.state)
    ):
        raise _error(
            SubscriptionAuthError,
            "OAuth callback state is invalid",
            provider=provider,
            reason="state_mismatch",
        )


def complete_login_transaction(
    transaction: SubscriptionLoginTransaction,
    *,
    code: str,
    state: str,
    exchange: Callable[..., Any] | None,
    token_endpoint: str,
    client_id: str,
    provider: SubscriptionProvider,
    credential_from_token_response: Callable[[Any], SubscriptionCredential],
    save: Callable[[SubscriptionCredential], None],
    enrich: Callable[[SubscriptionCredential], SubscriptionCredential] | None = None,
    exchange_parameters: Mapping[str, Any] | None = None,
) -> SubscriptionCredential:
    """Validate one pending PKCE callback, exchange it, and persist credentials."""
    validate_login_transaction(
        transaction,
        code=code,
        state=state,
        provider=provider,
    )
    if not callable(exchange):
        raise _error(
            SubscriptionAuthError,
            "OAuth token exchange is unavailable",
            provider=provider,
            reason="token_exchange_unavailable",
        )
    try:
        exchange_kwargs = {
            "code": code,
            "redirect_uri": transaction.redirect_uri,
            "client_id": client_id,
            "code_verifier": transaction.verifier.get_secret_value(),
            **(dict(exchange_parameters) if exchange_parameters is not None else {}),
        }
        try:
            payload = exchange(
                token_endpoint,
                provider=provider,
                **exchange_kwargs,
            )
        except TypeError:
            payload = exchange(token_endpoint, **exchange_kwargs)
        credential = credential_from_token_response(payload)
        if enrich is not None:
            credential = enrich(credential)
        save(credential)
        return credential
    except SubscriptionError:
        raise
    except Exception:
        raise _error(
            SubscriptionAuthError,
            "OAuth token exchange failed",
            provider=provider,
            reason="token_exchange_failed",
        ) from None


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


_DEFAULT_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def _build_no_redirect_opener() -> Any:
    return build_opener(
        _NoRedirectHandler(),
        HTTPSHandler(context=_DEFAULT_SSL_CONTEXT),
    )


_NO_REDIRECT_OPENER = _build_no_redirect_opener()


class _CallbackServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False


class LoopbackOAuthReceiver:
    """Receive one OAuth callback on a local HTTP listener.

    The default listener binds to ``127.0.0.1``.  Explicit container mode
    binds to the container interface and accepts only loopback or private
    container-network sources.  By default it advertises an ephemeral port and
    the same ``127.0.0.1`` authority; registered fixed ports and a ``localhost``
    advertised authority require explicit opt-in.

    ``listener`` is an optional test seam.  It receives ``(redirect_uri,
    callback)`` and may synchronously or asynchronously call ``callback`` with
    a callback URL or query mapping.  ``opener`` receives the authorization URL
    and defaults to the system browser only when no opener is injected.
    """

    def __init__(
        self,
        expected_state: str | PkceChallenge | None = None,
        *,
        state: str | None = None,
        challenge: PkceChallenge | None = None,
        pkce: PkceChallenge | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        path: str = "/callback",
        timeout: float | int | None = None,
        listener: Callable[..., Any] | Any | None = None,
        opener: Callable[[str], Any] | Any | None = None,
        redirect_host: str | None = None,
        allow_fixed_port: bool = False,
        container_mode: bool = False,
        callback_handler: Callable[[OAuthCallback], Any] | None = None,
    ) -> None:
        transaction = pkce or challenge
        if isinstance(expected_state, PkceChallenge):
            if transaction is not None and transaction is not expected_state:
                raise ValueError("conflicting PKCE transactions")
            transaction = expected_state
            expected_state = None
        selected_state = (
            state
            if state is not None
            else expected_state
            if expected_state is not None
            else transaction.state
            if transaction is not None
            else None
        )
        if transaction is not None and selected_state != transaction.state:
            raise ValueError("expected OAuth state does not match PKCE transaction")
        if not isinstance(selected_state, str) or not selected_state:
            raise ValueError("expected OAuth state is required")
        if len(selected_state) > _MAX_CALLBACK_VALUE_LENGTH or any(
            character.isspace() for character in selected_state
        ):
            raise ValueError("expected OAuth state is invalid")
        try:
            selected_state.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("expected OAuth state must be ASCII") from exc
        if not isinstance(container_mode, bool):
            raise ValueError("container_mode must be a bool")
        if container_mode:
            if host not in {"127.0.0.1", "0.0.0.0"}:
                raise ValueError(
                    "OAuth callback listener host is invalid for container mode"
                )
            bind_host = "0.0.0.0"
        else:
            if host != "127.0.0.1":
                raise ValueError("OAuth callback listener must bind to 127.0.0.1")
            bind_host = host
        if (
            not isinstance(port, int)
            or isinstance(port, bool)
            or not 0 <= port <= 65535
        ):
            raise ValueError("OAuth callback listener port is invalid")
        if not isinstance(allow_fixed_port, bool):
            raise ValueError("allow_fixed_port must be a bool")
        if port != 0 and not allow_fixed_port:
            raise ValueError(
                "OAuth callback listener requires allow_fixed_port=True for a fixed port"
            )
        selected_redirect_host = (
            "127.0.0.1"
            if container_mode and redirect_host is None
            else host
            if redirect_host is None
            else redirect_host
        )
        if selected_redirect_host not in {"127.0.0.1", "localhost"}:
            raise ValueError("OAuth callback redirect host must be loopback")
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or "?" in path
            or "#" in path
        ):
            raise ValueError("OAuth callback path is invalid")
        self.expected_state = selected_state
        self.pkce = transaction
        self.challenge = transaction
        self.timeout = _timeout(
            timeout,
            default=_DEFAULT_CALLBACK_TIMEOUT,
            maximum=_MAX_CALLBACK_TIMEOUT,
        )
        self.listener = listener
        self.opener = opener
        self.callback_handler = callback_handler
        self.container_mode = container_mode
        self._result: OAuthCallback | None = None
        self._error: BaseException | None = None
        self._event = threading.Event()
        self._lock = threading.RLock()
        self._server_started = False
        self._closed = False

        receiver = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(receiver.timeout)

            def handle(self) -> None:
                try:
                    super().handle()
                except (OSError, TimeoutError):
                    # Slow/incomplete clients must not hold the receiver open.
                    return None

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler spelling
                try:
                    receiver._validate_host_header(self.headers.get("Host"))
                    receiver.handle_callback(
                        self.path,
                        client_address=self.client_address,
                    )
                except SubscriptionAuthError as exc:
                    receiver._set_error(exc)
                    receiver._write_response(self, success=False)
                except Exception:
                    receiver._set_error(
                        _error(
                            SubscriptionAuthError,
                            "OAuth callback could not be validated",
                            reason="invalid_callback",
                        )
                    )
                    receiver._write_response(self, success=False)
                else:
                    receiver._write_response(self, success=True)

            def log_message(self, _format: str, *_args: object) -> None:
                # The request target contains the authorization code and state.
                return None

        self._server = _CallbackServer((bind_host, port), Handler)
        self._server.timeout = self.timeout
        self._path = path
        self.redirect_uri = (
            f"http://{selected_redirect_host}:{self._server.server_port}{path}"
        )
        validate_loopback_redirect_uri(self.redirect_uri)
        self.callback_url = self.redirect_uri

    def __repr__(self) -> str:
        return (
            "LoopbackOAuthReceiver("
            f"redirect_uri={self.redirect_uri!r}, timeout={self.timeout!r})"
        )

    def __enter__(self) -> LoopbackOAuthReceiver:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _set_error(self, error: BaseException) -> None:
        with self._lock:
            if self._result is None and self._error is None:
                self._error = error
                self._event.set()

    def _validate_host_header(self, host_header: str | None) -> None:
        expected = urlsplit(self.redirect_uri).netloc
        if not isinstance(host_header, str) or host_header.strip() != expected:
            raise _error(
                SubscriptionAuthError,
                "OAuth callback authority is invalid",
                reason="invalid_callback_authority",
            )

    def _write_response(
        self,
        handler: http.server.BaseHTTPRequestHandler,
        *,
        success: bool,
    ) -> None:
        body = _callback_response_html(success=success).encode("utf-8")
        try:
            handler.send_response(200 if success else 400)
            handler.send_header("Content-Type", "text/html; charset=utf-8")
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Referrer-Policy", "no-referrer")
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.send_header("X-Frame-Options", "DENY")
            handler.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            )
            handler.end_headers()
            handler.wfile.write(body)
        except OSError:
            # The browser may disconnect after the callback has been accepted.
            return None

    def _validate_client_address(
        self,
        client_address: tuple[str, int] | None,
    ) -> None:
        if client_address is None:
            return
        try:
            source_host = client_address[0]
        except (IndexError, TypeError):
            source_host = None
        if not is_allowed_callback_source(
            source_host,
            container_mode=self.container_mode,
        ):
            raise _error(
                SubscriptionAuthError,
                "OAuth callback origin is invalid",
                reason="non_loopback_callback",
            )

    def handle_callback(
        self,
        callback: str | bytes | Mapping[str, Any],
        *,
        client_address: tuple[str, int] | None = None,
    ) -> OAuthCallback:
        """Validate one callback URL/query and atomically retain its result."""
        self._validate_client_address(client_address)
        values = self._callback_values(callback)
        state = self._single_value(values, "state")
        if state is None or not secrets.compare_digest(state, self.expected_state):
            raise _error(
                SubscriptionAuthError,
                "OAuth callback state is invalid",
                reason="state_mismatch",
            )
        if "error" in values:
            self._single_value(values, "error")
            raise _error(
                SubscriptionAuthError,
                "OAuth authorization was denied",
                reason="authorization_denied",
            )
        code = self._single_value(values, "code")
        if code is None:
            raise _error(
                SubscriptionAuthError,
                "OAuth callback did not contain an authorization code",
                reason="missing_authorization_code",
            )
        result = OAuthCallback(code=code, state=state)
        with self._lock:
            if self._result is not None:
                raise _error(
                    SubscriptionAuthError,
                    "OAuth callback was received more than once",
                    reason="duplicate_callback",
                )
            if self.callback_handler is not None:
                try:
                    self.callback_handler(result)
                except SubscriptionError as exc:
                    self._error = exc
                    self._event.set()
                    raise
                except BaseException:
                    error = _error(
                        SubscriptionAuthError,
                        "OAuth callback could not be completed",
                        reason="callback_completion_failed",
                    )
                    self._error = error
                    self._event.set()
                    raise error from None
            self._result = result
            self._event.set()
        return result

    def _callback_values(
        self, callback: str | bytes | Mapping[str, Any]
    ) -> dict[str, list[str]]:
        if isinstance(callback, Mapping):
            raw_values: Mapping[str, Any] = callback
            values: dict[str, list[str]] = {}
            for key, raw in raw_values.items():
                if not isinstance(key, str):
                    continue
                items = raw if isinstance(raw, (list, tuple)) else [raw]
                converted: list[str] = []
                for item in items:
                    if isinstance(item, bytes):
                        try:
                            item = item.decode("utf-8", "strict")
                        except UnicodeDecodeError:
                            raise _error(
                                SubscriptionAuthError,
                                "OAuth callback parameters are invalid",
                                reason="invalid_callback_parameters",
                            ) from None
                    if not isinstance(item, str):
                        raise _error(
                            SubscriptionAuthError,
                            "OAuth callback parameters are invalid",
                            reason="invalid_callback_parameters",
                        )
                    if len(item) > _MAX_CALLBACK_VALUE_LENGTH:
                        raise _error(
                            SubscriptionAuthError,
                            "OAuth callback parameters are too large",
                            reason="callback_too_large",
                        )
                    converted.append(item)
                values[key] = converted
            return values
        if isinstance(callback, bytes):
            try:
                callback = callback.decode("ascii")
            except UnicodeDecodeError:
                raise _error(
                    SubscriptionAuthError,
                    "OAuth callback is invalid",
                    reason="invalid_callback",
                ) from None
        if (
            not isinstance(callback, str)
            or len(callback.encode("utf-8", "ignore")) > _MAX_CALLBACK_URL_BYTES
        ):
            raise _error(
                SubscriptionAuthError,
                "OAuth callback is invalid",
                reason="invalid_callback",
            )
        if callback.startswith("?"):
            query = callback[1:]
            parsed = None
        elif callback.startswith("/"):
            parsed = urlsplit(callback)
            query = parsed.query
        else:
            parsed = urlsplit(callback)
            query = parsed.query
        if parsed is not None:
            expected = urlsplit(self.redirect_uri)
            try:
                parsed_port = parsed.port
                parsed_hostname = parsed.hostname
            except ValueError:
                raise _error(
                    SubscriptionAuthError,
                    "OAuth callback URI is invalid",
                    reason="invalid_callback_uri",
                ) from None
            has_authority = bool(parsed.scheme or parsed.netloc)
            if has_authority:
                authority_invalid = (
                    parsed.scheme != expected.scheme
                    or parsed_hostname != expected.hostname
                    or parsed_port != expected.port
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.fragment
                    or parsed.path != expected.path
                )
            else:
                authority_invalid = parsed.path != expected.path
            if authority_invalid:
                raise _error(
                    SubscriptionAuthError,
                    "OAuth callback URI is invalid",
                    reason="invalid_callback_uri",
                )
        try:
            values = parse_qs(query, keep_blank_values=True, strict_parsing=False)
        except ValueError:
            raise _error(
                SubscriptionAuthError,
                "OAuth callback parameters are invalid",
                reason="invalid_callback_parameters",
            ) from None
        for key, items in values.items():
            if any(len(item) > _MAX_CALLBACK_VALUE_LENGTH for item in items):
                raise _error(
                    SubscriptionAuthError,
                    "OAuth callback parameters are too large",
                    reason="callback_too_large",
                )
        return values

    @staticmethod
    def _single_value(values: Mapping[str, list[str]], key: str) -> str | None:
        items = values.get(key)
        if items is None:
            return None
        if len(items) != 1 or not items[0]:
            raise _error(
                SubscriptionAuthError,
                "OAuth callback parameter is invalid",
                reason="invalid_callback_parameters",
            )
        return items[0]

    def _run_injected_listener(self) -> None:
        listener = self.listener
        try:
            if callable(listener):
                try:
                    returned = listener(self.redirect_uri, self.handle_callback)
                except TypeError:
                    returned = listener(self.redirect_uri)
            elif hasattr(listener, "listen"):
                returned = listener.listen(self.redirect_uri, self.handle_callback)
            elif hasattr(listener, "wait"):
                returned = listener.wait(self.redirect_uri, self.handle_callback)
            else:
                raise TypeError("listener seam must be callable or expose listen/wait")
            if returned is not None and not self._event.is_set():
                if isinstance(returned, OAuthCallback):
                    self.handle_callback(
                        {"code": returned.code, "state": returned.state}
                    )
                else:
                    self.handle_callback(returned)
        except SubscriptionError as exc:
            self._set_error(exc)
        except BaseException:
            self._set_error(
                _error(
                    SubscriptionAuthError,
                    "OAuth callback could not be validated",
                    reason="invalid_callback",
                )
            )

    def _invoke_opener(self, authorization_url: str, opener: Any) -> None:
        try:
            if callable(opener):
                opener(authorization_url)
            elif hasattr(opener, "open"):
                opener.open(authorization_url)
            elif hasattr(opener, "open_new_tab"):
                opener.open_new_tab(authorization_url)
            else:
                raise TypeError("opener seam must be callable or expose open")
        except BaseException:
            self._set_error(
                _error(
                    SubscriptionTransportError,
                    "OAuth authorization browser could not be opened",
                    reason="opener_failed",
                )
            )

    def _run_http_listener(self) -> None:
        try:
            self._server.serve_forever(poll_interval=0.05)
        except OSError:
            if not self._closed:
                self._set_error(
                    _error(
                        SubscriptionTransportError,
                        "OAuth callback listener failed",
                        reason="listener_failed",
                    )
                )

    def _await_result(self, timeout: float) -> OAuthCallback:
        if not self._event.wait(timeout):
            raise _error(
                SubscriptionTransportError,
                "OAuth callback timed out",
                reason="callback_timeout",
            )
        with self._lock:
            error = self._error
            result = self._result
        if error is not None:
            if isinstance(error, SubscriptionError):
                raise error
            raise _error(
                SubscriptionAuthError,
                "OAuth callback could not be validated",
                reason="invalid_callback",
            ) from None
        if result is None:
            raise _error(
                SubscriptionTransportError,
                "OAuth callback did not produce authorization input",
                reason="missing_callback_result",
            )
        return result

    def receive(
        self,
        authorization_url: str | None = None,
        *,
        opener: Callable[[str], Any] | Any | None = None,
        listener: Callable[..., Any] | Any | None = None,
        timeout: float | int | None = None,
    ) -> OAuthCallback:
        """Open an authorization URL and wait for one validated callback."""

        selected_timeout = _timeout(
            timeout,
            default=self.timeout,
            maximum=_MAX_CALLBACK_TIMEOUT,
        )
        if authorization_url is not None:
            validate_provider_url(authorization_url)
        if opener is None:
            opener = self.opener
        if listener is None:
            listener = self.listener
        with self._lock:
            if self._closed:
                closed = True
            else:
                closed = False
                self._event.clear()
                self._result = None
                self._error = None

                if listener is not None:
                    listener_thread = threading.Thread(
                        target=self._run_injected_listener,
                        daemon=True,
                        name="mudidi-oauth-test-listener",
                    )
                    self.listener = listener
                    listener_thread.start()
                else:
                    self._server_started = True
                    server_thread = threading.Thread(
                        target=self._run_http_listener,
                        daemon=True,
                        name="mudidi-oauth-loopback-listener",
                    )
                    server_thread.start()

        if closed:
            return self._await_result(selected_timeout)
        if authorization_url is not None:
            self._invoke_opener(
                authorization_url,
                opener if opener is not None else webbrowser.open,
            )
        try:
            return self._await_result(selected_timeout)
        finally:
            self.close()

    def wait_for_callback(self, *, timeout: float | int | None = None) -> OAuthCallback:
        """Wait for a callback after a caller-managed listener start."""

        return self._await_result(
            _timeout(timeout, default=self.timeout, maximum=_MAX_CALLBACK_TIMEOUT)
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._result is None and self._error is None:
                self._error = _error(
                    SubscriptionTransportError,
                    "OAuth callback listener closed",
                    reason="callback_closed",
                )
                self._event.set()
        if self._server_started:
            try:
                self._server.shutdown()
            except (OSError, RuntimeError):
                pass
            self._server_started = False
        try:
            self._server.server_close()
        except OSError:
            pass

    listen = receive
    run = receive
    wait = wait_for_callback
    submit = handle_callback
    accept = handle_callback


class OAuthHttpClient:
    """Bounded HTTPS form/JSON client for token exchanges."""

    def __init__(
        self,
        *,
        opener: Any | None = None,
        fetch: Callable[..., Any] | None = None,
        timeout: float | int | None = None,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        allowed_hosts: set[str]
        | frozenset[str]
        | tuple[str, ...]
        | list[str]
        | None = None,
        user_agent: str | None = None,
    ) -> None:
        selected_max = int(max_response_bytes)
        if selected_max <= 0:
            raise ValueError("max_response_bytes must be positive")
        if user_agent is not None and (
            not isinstance(user_agent, str)
            or not user_agent
            or len(user_agent) > 256
            or not user_agent.isascii()
            or any(
                ord(character) < 32 or ord(character) == 127 for character in user_agent
            )
        ):
            raise ValueError("user_agent must contain only printable ASCII characters")
        self.timeout = _timeout(
            timeout,
            default=_DEFAULT_HTTP_TIMEOUT,
            maximum=_MAX_HTTP_TIMEOUT,
        )
        self.max_response_bytes = min(selected_max, _MAX_RESPONSE_BYTES)
        self.allowed_hosts = _host_set(allowed_hosts)
        self.user_agent = user_agent
        self._opener = opener
        self._fetch = fetch

    def __repr__(self) -> str:
        hosts = sorted(self.allowed_hosts) if self.allowed_hosts is not None else None
        return (
            "OAuthHttpClient("
            f"timeout={self.timeout!r}, max_response_bytes={self.max_response_bytes!r}, "
            f"allowed_hosts={hosts!r})"
        )

    def build_authorization_url(
        self,
        endpoint: str,
        parameters: Mapping[str, Any],
    ) -> str:
        """Add non-secret authorization parameters to an HTTPS endpoint."""

        parsed = validate_provider_url(endpoint, allowed_hosts=self.allowed_hosts)
        encoded = urlencode(
            [(str(key), str(value)) for key, value in parameters.items()],
            doseq=True,
        )
        query = f"{parsed.query}&{encoded}" if parsed.query else encoded
        return urlunsplit(parsed._replace(query=query))

    def exchange_code(
        self,
        endpoint: str,
        *,
        code: str,
        redirect_uri: str,
        client_id: str,
        code_verifier: str,
        client_secret: str | None = None,
        extra_params: Mapping[str, Any] | None = None,
        provider: SubscriptionProvider | str | None = None,
        timeout: float | int | None = None,
    ) -> dict[str, Any]:
        """Exchange an authorization code using the in-memory PKCE verifier."""

        validate_provider_url(endpoint, allowed_hosts=self.allowed_hosts)
        validate_loopback_redirect_uri(redirect_uri)
        self._require_text(code, "authorization code")
        self._require_text(client_id, "OAuth client id")
        PkceChallenge.challenge_for(code_verifier)
        params: dict[str, Any] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
        }
        if client_secret is not None:
            self._require_text(client_secret, "OAuth client secret")
            params["client_secret"] = client_secret
        self._merge_extra(params, extra_params)
        return self.request_token(
            endpoint,
            params,
            provider=provider,
            timeout=timeout,
        )

    def exchange_authorization_code(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Alias for :meth:`exchange_code`."""

        return self.exchange_code(*args, **kwargs)

    exchange = exchange_code
    token_exchange = exchange_code

    def refresh(
        self,
        endpoint: str,
        *,
        refresh_token: str,
        client_id: str,
        client_secret: str | None = None,
        scope: str | None = None,
        extra_params: Mapping[str, Any] | None = None,
        provider: SubscriptionProvider | str | None = None,
        timeout: float | int | None = None,
    ) -> dict[str, Any]:
        """Exchange an in-memory refresh token for a new token response."""

        validate_provider_url(endpoint, allowed_hosts=self.allowed_hosts)
        self._require_text(refresh_token, "refresh token")
        self._require_text(client_id, "OAuth client id")
        params: dict[str, Any] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        if client_secret is not None:
            self._require_text(client_secret, "OAuth client secret")
            params["client_secret"] = client_secret
        if scope is not None:
            self._require_text(scope, "OAuth scope")
            params["scope"] = scope
        self._merge_extra(params, extra_params)
        return self.request_token(endpoint, params, provider=provider, timeout=timeout)

    def refresh_token(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Alias for :meth:`refresh`."""

        return self.refresh(*args, **kwargs)

    refresh_access_token = refresh

    def request_token(
        self,
        endpoint: str,
        parameters: Mapping[str, Any],
        *,
        provider: SubscriptionProvider | str | None = None,
        timeout: float | int | None = None,
    ) -> dict[str, Any]:
        """POST URL-encoded parameters and validate a token response."""

        validate_provider_url(endpoint, allowed_hosts=self.allowed_hosts)
        selected_timeout = _timeout(
            timeout,
            default=self.timeout,
            maximum=_MAX_HTTP_TIMEOUT,
        )
        try:
            encoded = urlencode(
                [(str(key), str(value)) for key, value in parameters.items()],
                doseq=True,
            ).encode("ascii")
        except (TypeError, UnicodeEncodeError, ValueError):
            raise _error(
                SubscriptionAuthError,
                "OAuth token request parameters are invalid",
                provider=provider,
                reason="invalid_request_parameters",
            ) from None
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "Cache-Control": "no-store",
        }
        if self.user_agent is not None:
            headers["User-Agent"] = self.user_agent
        request = Request(
            endpoint,
            data=encoded,
            headers=headers,
            method="POST",
        )
        return self._request_token_body(request, selected_timeout, provider=provider)

    def request_json_token(
        self,
        endpoint: str,
        parameters: Mapping[str, Any],
        *,
        provider: SubscriptionProvider | str | None = None,
        timeout: float | int | None = None,
    ) -> dict[str, Any]:
        """POST a JSON token request and validate its bounded response."""

        validate_provider_url(endpoint, allowed_hosts=self.allowed_hosts)
        selected_timeout = _timeout(
            timeout,
            default=self.timeout,
            maximum=_MAX_HTTP_TIMEOUT,
        )
        try:
            encoded = json.dumps(
                {str(key): value for key, value in parameters.items()},
                ensure_ascii=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, UnicodeEncodeError, ValueError):
            raise _error(
                SubscriptionAuthError,
                "OAuth JSON token request parameters are invalid",
                provider=provider,
                reason="invalid_request_parameters",
            ) from None
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
        }
        if self.user_agent is not None:
            headers["User-Agent"] = self.user_agent
        request = Request(
            endpoint,
            data=encoded,
            headers=headers,
            method="POST",
        )
        return self._request_token_body(request, selected_timeout, provider=provider)

    def _request_token_body(
        self,
        request: Request,
        selected_timeout: float,
        *,
        provider: SubscriptionProvider | str | None,
    ) -> dict[str, Any]:
        try:
            response = self._open(request, selected_timeout)
            self._validate_response_target(
                response,
                provider=provider,
            )
            status, payload = self._read_response(response)
        except HTTPError as exc:
            raise _error(
                SubscriptionAuthError
                if 400 <= exc.code < 500
                else SubscriptionTransportError,
                "OAuth token request failed",
                provider=provider,
                status=exc.code,
                reason="token_http_error",
            ) from None
        except (OSError, URLError, TimeoutError):
            raise _error(
                SubscriptionTransportError,
                "OAuth token request could not be delivered",
                provider=provider,
                reason="token_transport_error",
            ) from None
        except SubscriptionError:
            raise
        except Exception:
            raise _error(
                SubscriptionTransportError,
                "OAuth token response could not be read",
                provider=provider,
                reason="token_response_error",
            ) from None

        if status < 200 or status >= 300:
            error_type: type[SubscriptionError] = (
                SubscriptionAuthError
                if 400 <= status < 500
                else SubscriptionTransportError
            )
            raise _error(
                error_type,
                "OAuth token request failed",
                provider=provider,
                status=status,
                reason="token_http_error",
            )
        if len(payload) > self.max_response_bytes:
            raise _error(
                SubscriptionTransportError,
                "OAuth token response is too large",
                provider=provider,
                status=status,
                reason="response_too_large",
            )
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            raise _error(
                SubscriptionTransportError,
                "OAuth token response is not valid JSON",
                provider=provider,
                status=status,
                reason="malformed_token_response",
            ) from None
        return self._validate_token_response(decoded, provider=provider, status=status)

    def post_token(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Alias for :meth:`request_token`."""

        return self.request_token(*args, **kwargs)

    @staticmethod
    def _require_text(value: Any, label: str) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > _MAX_CALLBACK_VALUE_LENGTH
        ):
            raise ValueError(f"{label} must be a non-empty bounded string")
        return value

    @staticmethod
    def _merge_extra(
        parameters: dict[str, Any], extra: Mapping[str, Any] | None
    ) -> None:
        if extra is None:
            return
        if not isinstance(extra, Mapping):
            raise TypeError("extra_params must be a mapping")
        for key, value in extra.items():
            name = str(key)
            if name in {
                "grant_type",
                "code",
                "redirect_uri",
                "client_id",
                "code_verifier",
            }:
                continue
            parameters[name] = value

    def _open(self, request: Request, timeout: float) -> Any:
        if self._fetch is not None:
            fetch = self._fetch
            try:
                return fetch(request, timeout=timeout)
            except TypeError:
                try:
                    return fetch(request, timeout)
                except TypeError:
                    try:
                        return fetch(
                            request.full_url,
                            request.data,
                            dict(request.headers),
                            timeout,
                        )
                    except TypeError:
                        return fetch(
                            request.full_url,
                            request.data,
                            dict(request.headers),
                        )
        opener = self._opener
        if opener is None:
            return _NO_REDIRECT_OPENER.open(request, timeout=timeout)
        if hasattr(opener, "open"):
            return opener.open(request, timeout=timeout)
        if callable(opener):
            try:
                return opener(request, timeout=timeout)
            except TypeError:
                try:
                    return opener(request, timeout)
                except TypeError:
                    try:
                        return opener(
                            request.full_url,
                            request.data,
                            dict(request.headers),
                            timeout,
                        )
                    except TypeError:
                        return opener(
                            request.full_url,
                            request.data,
                            dict(request.headers),
                        )
        raise TypeError("opener seam must be callable or expose open")

    def _validate_response_target(
        self,
        response: Any,
        *,
        provider: SubscriptionProvider | str | None,
    ) -> None:
        """Reject a response whose final URL leaves the HTTPS allowlist.

        The default opener rejects redirects before sending another request.
        Injected opener/fetch seams may implement their own redirect policy, so
        validate their reported final URL as a second defense.
        """

        target = getattr(response, "geturl", None)
        if callable(target):
            target = target()
        if target is None:
            target = getattr(response, "url", None)
        if target is None:
            return
        try:
            validate_provider_url(target, allowed_hosts=self.allowed_hosts)
        except (SubscriptionError, TypeError):
            raise _error(
                SubscriptionTransportError,
                "OAuth token endpoint redirect is not allowed",
                provider=provider,
                reason="endpoint_redirect",
            ) from None

    def _read_response(self, response: Any) -> tuple[int, bytes]:
        if isinstance(response, Mapping):
            return 200, json.dumps(response, ensure_ascii=True).encode("utf-8")
        if isinstance(response, str):
            return 200, response.encode("utf-8")
        if isinstance(response, (bytes, bytearray, memoryview)):
            return 200, bytes(response)
        status = getattr(response, "status", None)
        if status is None and hasattr(response, "getcode"):
            status = response.getcode()
        if status is None:
            status = 200
        try:
            raw = response.read(self.max_response_bytes + 1)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if not isinstance(raw, (bytes, bytearray, memoryview)):
            raise TypeError("OAuth response body is not bytes")
        return int(status), bytes(raw)

    @staticmethod
    def _validate_token_response(
        payload: Any,
        *,
        provider: SubscriptionProvider | str | None,
        status: int,
    ) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise _error(
                SubscriptionAuthError,
                "OAuth token response is invalid",
                provider=provider,
                status=status,
                reason="malformed_token_response",
            )
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            # Do not name or echo the absent/invalid field in user-facing text.
            raise _error(
                SubscriptionAuthError,
                "OAuth token response is missing a required token value",
                provider=provider,
                status=status,
                reason="missing_access_token",
            )
        token_type = payload.get("token_type")
        if token_type is not None and (
            not isinstance(token_type, str) or not token_type.strip()
        ):
            raise _error(
                SubscriptionAuthError,
                "OAuth token response is invalid",
                provider=provider,
                status=status,
                reason="invalid_token_type",
            )
        refresh_token = payload.get("refresh_token")
        if refresh_token is not None and (
            not isinstance(refresh_token, str) or not refresh_token.strip()
        ):
            raise _error(
                SubscriptionAuthError,
                "OAuth token response is invalid",
                provider=provider,
                status=status,
                reason="invalid_refresh_token",
            )
        expires_in = payload.get("expires_in")
        invalid_expiry = False
        if expires_in is not None:
            if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)):
                invalid_expiry = True
            elif isinstance(expires_in, int):
                invalid_expiry = not 0 <= expires_in <= _MAX_EXPIRES_IN_SECONDS
            else:
                try:
                    invalid_expiry = not (
                        math.isfinite(expires_in)
                        and 0 <= expires_in <= _MAX_EXPIRES_IN_SECONDS
                    )
                except (OverflowError, ValueError):
                    invalid_expiry = True
        if invalid_expiry:
            raise _error(
                SubscriptionAuthError,
                "OAuth token response is invalid",
                provider=provider,
                status=status,
                reason="invalid_expiry",
            )
        return dict(payload)


__all__ = [
    "LoopbackOAuthReceiver",
    "OAuthCallback",
    "OAuthHttpClient",
    "validate_loopback_redirect_uri",
    "validate_provider_url",
]
