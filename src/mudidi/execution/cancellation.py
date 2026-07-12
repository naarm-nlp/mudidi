"""Cooperative cancellation primitives for long-running extraction work."""

from __future__ import annotations

from threading import Event


class ExecutionCancelled(RuntimeError):
    """Raised at a safe checkpoint after cancellation is requested."""


class CancellationToken:
    """Thread-safe cooperative cancellation signal."""

    def __init__(self) -> None:
        self._cancelled = Event()

    @property
    def is_cancelled(self) -> bool:
        """Return whether cancellation has been requested."""

        return self._cancelled.is_set()

    def cancel(self) -> None:
        """Request cancellation at the next safe execution checkpoint."""

        self._cancelled.set()

    def raise_if_cancelled(self) -> None:
        """Raise when cancellation has been requested."""

        if self.is_cancelled:
            raise ExecutionCancelled("execution was cancelled")
