"""Single-process loopback server launcher."""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    data_dir: Path | None = None,
    open_browser: bool = True,
    container_mode: bool = False,
    max_request_bytes: int | None = None,
    max_upload_bytes: int | None = None,
) -> int:
    """Run the local application in one Uvicorn process.

    Multiple Uvicorn workers are intentionally not exposed. Container mode is
    explicit because its all-interface bind must be paired with host-loopback
    port publishing (as in the shipped Compose configuration).
    """

    import uvicorn
    from dotenv import load_dotenv

    from mudidi.web.app import create_app

    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("MUDIDI web may only bind to the loopback interface")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")

    load_dotenv()
    app_kwargs: dict[str, object] = {
        "data_dir": data_dir,
        "container_mode": container_mode,
    }
    if max_request_bytes is not None:
        app_kwargs["max_request_bytes"] = max_request_bytes
    if max_upload_bytes is not None:
        app_kwargs["max_upload_bytes"] = max_upload_bytes
    app = create_app(**app_kwargs)
    browser_url = f"http://localhost:{port}/"
    print(f"MUDIDI dashboard: {browser_url}", flush=True)
    if open_browser:
        threading.Timer(0.8, webbrowser.open, args=(browser_url,)).start()
    bind_host = "0.0.0.0" if container_mode else host
    uvicorn.run(
        app,
        host=bind_host,
        port=port,
        workers=1,
        log_level="warning",
    )
    return 0
