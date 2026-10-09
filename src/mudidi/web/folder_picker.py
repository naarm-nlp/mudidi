"""Native folder chooser for the loopback dashboard.

A browser cannot report the filesystem path of a folder the user picks, but the
dashboard runs on the user's own machine, so the server opens the operating
system's folder dialog and returns the selected path.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_PROMPT = "Choose the MUDIDI output folder"
_DIALOG_TIMEOUT_SECONDS = 600.0

_APPLESCRIPT = """
on run argv
    activate
    set chosen to choose folder with prompt (item 1 of argv) default location (POSIX file (item 2 of argv))
    return POSIX path of chosen
end run
"""

_POWERSHELL = (
    "Add-Type -AssemblyName System.Windows.Forms;"
    "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog;"
    "$dialog.Description = $env:MUDIDI_PICKER_PROMPT;"
    "$dialog.SelectedPath = $env:MUDIDI_PICKER_INITIAL;"
    "if ($dialog.ShowDialog() -eq 'OK') { [Console]::Out.Write($dialog.SelectedPath) }"
)


class FolderPickerUnavailable(RuntimeError):
    """Raised when this machine cannot show a folder dialog."""


def _linux_command(initial: Path) -> list[str] | None:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    if shutil.which("zenity"):
        return [
            "zenity",
            "--file-selection",
            "--directory",
            f"--title={_PROMPT}",
            f"--filename={initial}/",
        ]
    if shutil.which("kdialog"):
        return ["kdialog", "--title", _PROMPT, "--getexistingdirectory", str(initial)]
    return None


def _command(initial: Path) -> tuple[list[str], dict[str, str]] | None:
    """Return the dialog command and extra environment for this platform."""

    if sys.platform == "darwin":
        if shutil.which("osascript") is None:
            return None
        return ["osascript", "-e", _APPLESCRIPT, _PROMPT, str(initial)], {}
    if sys.platform == "win32":
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if shell is None:
            return None
        return (
            [shell, "-NoProfile", "-STA", "-Command", _POWERSHELL],
            {"MUDIDI_PICKER_PROMPT": _PROMPT, "MUDIDI_PICKER_INITIAL": str(initial)},
        )
    command = _linux_command(initial)
    return (command, {}) if command is not None else None


def folder_picker_available() -> bool:
    """Return whether a native folder dialog can be shown on this machine."""

    return _command(Path.home()) is not None


def existing_start_directory(value: str) -> Path:
    """Return the nearest existing directory for a typed path, else home."""

    home = Path.home()
    if not value.strip():
        return home
    try:
        candidate = Path(value.strip()).expanduser()
        if not candidate.is_absolute():
            return home
        for path in (candidate, *candidate.parents):
            if path.is_dir():
                return path
    except (OSError, RuntimeError, ValueError):
        pass
    return home


def display_path(path: Path) -> str:
    """Abbreviate paths under the home directory the way the form default does."""

    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


def choose_directory(initial: Path) -> Path | None:
    """Show the folder dialog and return the selection, or None when cancelled."""

    resolved = _command(initial)
    if resolved is None:
        raise FolderPickerUnavailable("no folder dialog is available")
    command, extra_environment = resolved
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_DIALOG_TIMEOUT_SECONDS,
            env={**os.environ, **extra_environment},
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    except OSError as exc:
        raise FolderPickerUnavailable("folder dialog could not be opened") from exc
    selected = completed.stdout.strip()
    if completed.returncode != 0 or not selected:
        return None
    path = Path(selected)
    return path if path.is_dir() else None
