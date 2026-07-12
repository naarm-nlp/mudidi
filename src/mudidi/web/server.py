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
) -> int:
    """Run the local application in one Uvicorn process.

    Public network binding and multiple Uvicorn workers are intentionally not
    exposed in v1.
    """

    import uvicorn
    from dotenv import load_dotenv

    from mudidi.web.app import create_app

    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("MUDIDI web may only bind to the loopback interface")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")

    load_dotenv()
    app = create_app(data_dir=data_dir)
    if open_browser:
        url = f"http://{host}:{port}/"
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=host, port=port, workers=1)
    return 0
