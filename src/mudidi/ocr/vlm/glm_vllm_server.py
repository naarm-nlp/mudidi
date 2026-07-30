"""Auto-start a local vLLM server for GLM-OCR."""

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

DEFAULT_GLM_MODEL_ID = "zai-org/GLM-OCR"
DEFAULT_GLM_SERVED_NAME = "glm-ocr"
DEFAULT_GLM_VLLM_HOST = "127.0.0.1"
DEFAULT_GLM_VLLM_PORT = 8081
DEFAULT_SERVER_VENV = ".venv-glmocr-vllm"
HEALTH_POLL_INTERVAL_S = 2.0
HEALTH_TIMEOUT_S = 600.0


def glm_vllm_server_python(explicit: str | None = None) -> Path:
    """Return Python executable for the GLM-OCR vLLM server venv."""
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        raise FileNotFoundError(f"GLM vLLM server python not found: {explicit}")

    env = os.getenv("GLM_VLLM_SERVER_PYTHON", "").strip()
    if env:
        path = Path(env)
        if path.is_file():
            return path

    project_root = Path(__file__).resolve().parents[3]
    for candidate in (
        project_root / DEFAULT_SERVER_VENV / "bin" / "python",
        Path.cwd() / DEFAULT_SERVER_VENV / "bin" / "python",
    ):
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(
        f"Missing {DEFAULT_SERVER_VENV}. Create a compatible GLM-OCR vLLM "
        "environment or set GLM_VLLM_SERVER_PYTHON; see "
        "docs/benchmarking/vlm.md."
    )


def glm_vllm_port(explicit: int | None = None) -> int:
    """Return TCP port for the local GLM-OCR vLLM server."""
    if explicit is not None and explicit > 0:
        return explicit
    env = os.getenv("GLM_VLLM_SERVER_PORT", "").strip()
    if env.isdigit() and int(env) > 0:
        return int(env)
    return DEFAULT_GLM_VLLM_PORT


def glm_vllm_server_url(host: str, port: int) -> str:
    """OpenAI-compatible base URL for GLM-OCR vLLM clients."""
    return f"http://{host}:{port}/v1"


def _health_ok(host: str, port: int, *, timeout: float = 2.0) -> bool:
    try:
        with urlopen(f"http://{host}:{port}/health", timeout=timeout) as resp:
            return resp.status == 200
    except (URLError, OSError, ValueError):
        return False


class GlmVllmServerManager:
    """Start and stop ``vllm serve`` for GLM-OCR."""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_GLM_MODEL_ID,
        served_model_name: str = DEFAULT_GLM_SERVED_NAME,
        host: str = DEFAULT_GLM_VLLM_HOST,
        port: int | None = None,
        server_python: Path | None = None,
        health_timeout_s: float = HEALTH_TIMEOUT_S,
    ) -> None:
        self.model_id = model_id
        self.served_model_name = served_model_name
        self.host = host
        self.port = glm_vllm_port(port)
        self.server_python = server_python or glm_vllm_server_python()
        self.health_timeout_s = health_timeout_s
        self._proc: subprocess.Popen[Any] | None = None
        self._owned = False

    @property
    def server_url(self) -> str:
        return glm_vllm_server_url(self.host, self.port)

    def start(self) -> str:
        """Start server if needed and return OpenAI-compatible base URL."""
        if _health_ok(self.host, self.port):
            logger.info("Reusing GLM-OCR vLLM server at %s", self.server_url)
            return self.server_url

        cmd = [
            str(self.server_python),
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--model",
            self.model_id,
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--served-model-name",
            self.served_model_name,
            "--trust-remote-code",
        ]
        logger.info("Starting GLM-OCR vLLM server: %s", " ".join(cmd))
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
                    f"GLM-OCR vLLM server exited early (code {self._proc.returncode}).\n"
                    f"{output[-4000:]}"
                )
            if _health_ok(self.host, self.port):
                logger.info(
                    "GLM-OCR vLLM server ready at %s (pid %s)",
                    self.server_url,
                    self._proc.pid,
                )
                return self.server_url
            time.sleep(HEALTH_POLL_INTERVAL_S)

        self.stop()
        raise TimeoutError(
            f"GLM-OCR vLLM server did not become healthy within "
            f"{self.health_timeout_s:.0f}s on port {self.port}"
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
        logger.info("Stopped GLM-OCR vLLM server (pid %s)", pid)

    def __enter__(self) -> str:
        return self.start()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.stop()


def should_auto_start_glm_vllm_server(args: Any) -> bool:
    """Return True when GLM-OCR batch extraction should spawn vLLM."""
    if getattr(args, "vlm_model", None) != "glm-ocr":
        return False
    if getattr(args, "glm_auto_vllm_server", True) is False:
        return False
    if getattr(args, "glm_vllm_server_url", None):
        return False
    if os.getenv("GLM_VLLM_SERVER_URL", "").strip():
        return False
    backend = getattr(args, "glm_backend", None) or getattr(args, "vlm_backend", None)
    if backend == "transformers":
        return False
    return True


def ensure_glm_vllm_server_args(args: Any) -> GlmVllmServerManager | None:
    """Set vLLM server URL on ``args``; return manager when we must stop it."""
    url = getattr(args, "glm_vllm_server_url", None) or os.getenv(
        "GLM_VLLM_SERVER_URL", ""
    ).strip()
    if url:
        args.glm_backend = "vllm"
        args.glm_vllm_server_url = url
        return None

    if not should_auto_start_glm_vllm_server(args):
        return None

    manager = GlmVllmServerManager(
        port=glm_vllm_port(getattr(args, "glm_vllm_server_port", None)),
        server_python=(
            Path(args.glm_vllm_server_python)
            if getattr(args, "glm_vllm_server_python", None)
            else None
        ),
    )
    args.glm_backend = "vllm"
    args.glm_vllm_server_url = manager.start()
    return manager
