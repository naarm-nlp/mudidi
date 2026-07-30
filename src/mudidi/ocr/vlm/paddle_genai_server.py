"""Manage an in-process PaddleOCR GenAI vLLM server for PaddleOCR-VL-1.5."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

logger = logging.getLogger(__name__)

DEFAULT_PADDLE_GENAI_MODEL = "PaddleOCR-VL-1.5-0.9B"
DEFAULT_PADDLE_GENAI_PORT = 8765
DEFAULT_PADDLE_GENAI_HOST = "127.0.0.1"
DEFAULT_SERVER_VENV = ".venv-paddle-vllm-server"
HEALTH_POLL_INTERVAL_S = 2.0
HEALTH_TIMEOUT_S = 600.0


def paddle_genai_server_python(explicit: str | None = None) -> Path:
    """Return Python executable for the dedicated Paddle GenAI server venv."""
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        raise FileNotFoundError(f"Paddle GenAI server python not found: {explicit}")

    env = os.getenv("PADDLE_VLLM_SERVER_PYTHON", "").strip()
    if env:
        path = Path(env)
        if path.is_file():
            return path

    project_root = Path(__file__).resolve().parents[3]
    candidates = [
        project_root / DEFAULT_SERVER_VENV / "bin" / "python",
        Path.cwd() / DEFAULT_SERVER_VENV / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(
        f"Missing {DEFAULT_SERVER_VENV}. Create a compatible PaddleOCR-VL "
        "server environment or set PADDLE_VLLM_SERVER_PYTHON; see "
        "docs/benchmarking/vlm.md."
    )


def paddle_genai_port(explicit: int | None = None) -> int:
    """Return TCP port for the local Paddle GenAI server."""
    if explicit is not None and explicit > 0:
        return explicit
    env = os.getenv("PADDLE_VL_SERVER_PORT", "").strip()
    if env.isdigit() and int(env) > 0:
        return int(env)
    return DEFAULT_PADDLE_GENAI_PORT


def paddle_genai_server_url(host: str, port: int) -> str:
    """OpenAI-compatible base URL expected by ``PaddleOCRVL(vl_rec_backend=...)``."""
    return f"http://{host}:{port}/v1"


def _health_ok(host: str, port: int, *, timeout: float = 2.0) -> bool:
    try:
        with urlopen(f"http://{host}:{port}/health", timeout=timeout) as resp:
            return resp.status == 200
    except (URLError, OSError, ValueError):
        return False


def is_paddle_genai_vllm_available(python: Path | None = None) -> bool:
    """Return True when the server venv has the genai-vllm-server plugin."""
    try:
        exe = python or paddle_genai_server_python()
    except FileNotFoundError:
        return False
    cmd = [
        str(exe),
        "-c",
        "from paddlex.utils.deps import is_genai_engine_plugin_available; "
        "raise SystemExit(0 if is_genai_engine_plugin_available('vllm-server') else 1)",
    ]
    try:
        return subprocess.run(cmd, check=False, capture_output=True, timeout=60).returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


class PaddleGenaiServerManager:
    """Start and stop a local ``paddleocr genai_server`` subprocess."""

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_PADDLE_GENAI_MODEL,
        host: str = DEFAULT_PADDLE_GENAI_HOST,
        port: int | None = None,
        server_python: Path | None = None,
        health_timeout_s: float = HEALTH_TIMEOUT_S,
    ) -> None:
        self.model_name = model_name
        self.host = host
        self.port = paddle_genai_port(port)
        self.server_python = server_python or paddle_genai_server_python()
        self.health_timeout_s = health_timeout_s
        self._proc: subprocess.Popen[Any] | None = None
        self._owned = False

    @property
    def server_url(self) -> str:
        return paddle_genai_server_url(self.host, self.port)

    def _reuse_existing(self) -> bool:
        if _health_ok(self.host, self.port):
            logger.info(
                "Reusing Paddle GenAI server at %s (already healthy)",
                self.server_url,
            )
            return True
        return False

    def start(self) -> str:
        """Start server if needed and return the OpenAI-compatible base URL."""
        if self._reuse_existing():
            return self.server_url

        if not is_paddle_genai_vllm_available(self.server_python):
            raise RuntimeError(
                f"Paddle GenAI vLLM plugin unavailable in {self.server_python}. "
                "Install the plugin in that environment; see "
                "docs/benchmarking/vlm.md."
            )

        cmd = [
            str(self.server_python),
            "-m",
            "paddleocr",
            "genai_server",
            "--model_name",
            self.model_name,
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--backend",
            "vllm",
        ]
        logger.info("Starting Paddle GenAI vLLM server: %s", " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        self._owned = True

        deadline = time.monotonic() + self.health_timeout_s
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                output = ""
                if self._proc.stdout is not None:
                    output = self._proc.stdout.read() or ""
                raise RuntimeError(
                    f"Paddle GenAI server exited early (code {self._proc.returncode}).\n"
                    f"{output[-4000:]}"
                )
            if _health_ok(self.host, self.port):
                logger.info(
                    "Paddle GenAI server ready at %s (pid %s)",
                    self.server_url,
                    self._proc.pid,
                )
                return self.server_url
            time.sleep(HEALTH_POLL_INTERVAL_S)

        self.stop()
        raise TimeoutError(
            f"Paddle GenAI server did not become healthy within {self.health_timeout_s:.0f}s "
            f"on port {self.port}"
        )

    def stop(self) -> None:
        """Terminate a server started by this manager."""
        if not self._owned or self._proc is None:
            return
        pid = self._proc.pid
        if self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self._proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self._proc = None
        self._owned = False
        logger.info("Stopped Paddle GenAI server (pid %s)", pid)

    def __enter__(self) -> str:
        return self.start()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.stop()


def should_auto_start_paddle_genai_server(args: Any) -> bool:
    """Return True when batch extraction should spawn a local GenAI server."""
    if getattr(args, "vlm_model", None) != "paddleocr-vl-1.5":
        return False
    if getattr(args, "paddle_auto_vllm_server", True) is False:
        return False
    if getattr(args, "paddle_vl_rec_server_url", None):
        return False
    if os.getenv("PADDLE_VL_REC_SERVER_URL", "").strip():
        return False
    return True


def ensure_paddle_vllm_server_args(args: Any) -> PaddleGenaiServerManager | None:
    """Mutate ``args`` for vllm-server mode; return manager if we must stop it later."""
    if not should_auto_start_paddle_genai_server(args):
        url = getattr(args, "paddle_vl_rec_server_url", None) or os.getenv(
            "PADDLE_VL_REC_SERVER_URL", ""
        ).strip()
        if url:
            args.paddle_vl_rec_backend = "vllm-server"
            args.paddle_vl_rec_server_url = url
        return None

    manager = PaddleGenaiServerManager(
        port=paddle_genai_port(getattr(args, "paddle_vl_server_port", None)),
        server_python=(
            Path(args.paddle_vllm_server_python)
            if getattr(args, "paddle_vllm_server_python", None)
            else None
        ),
    )
    args.paddle_vl_rec_backend = "vllm-server"
    args.paddle_vl_rec_server_url = manager.start()
    return manager
