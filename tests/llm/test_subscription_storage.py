"""Focused tests for the separate encrypted subscription credential store."""

from __future__ import annotations
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
import threading

import pytest

from mudidi.llm.subscriptions import (
    SubscriptionAuthError,
    SubscriptionCredential,
    SubscriptionProvider,
)
import mudidi.llm.subscriptions.storage as storage_module
from mudidi.llm.subscriptions.storage import SubscriptionStore
from mudidi.llm.subscriptions.openai_codex import OpenAICodexBackend


ACCESS_TOKEN = "access-token-that-must-never-be-persisted-plaintext"
REFRESH_TOKEN = "refresh-token-that-must-never-be-persisted-plaintext"


def _credential() -> SubscriptionCredential:
    return SubscriptionCredential(
        provider=SubscriptionProvider.OPENAI,
        account_label="Research account",
        access_token=ACCESS_TOKEN,
        refresh_token=REFRESH_TOKEN,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        project_id="project-123",
        account_id="account-456",
        metadata={"workspace": "local"},
    )


def test_subscription_store_is_separate_encrypted_sqlite_and_survives_restart(
    tmp_path: Path,
) -> None:
    store_dir = tmp_path / "subscription-store"
    store = SubscriptionStore(store_dir)
    store.save(SubscriptionProvider.OPENAI, _credential())

    assert store.database_path.exists()
    assert store.key_path.exists()
    assert store.database_path.name == "subscriptions.sqlite3"
    assert store.key_path.name == "subscriptions.key"
    assert store.database_path != tmp_path / "credentials.sqlite3"
    assert store.key_path.stat().st_mode & 0o777 == 0o600
    assert store.database_path.stat().st_mode & 0o777 == 0o600

    with sqlite3.connect(store.database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "subscription_credentials" in tables
        assert "provider_credentials" not in tables

    assert ACCESS_TOKEN.encode() not in store.database_path.read_bytes()
    assert REFRESH_TOKEN.encode() not in store.database_path.read_bytes()
    assert ACCESS_TOKEN.encode() not in store.key_path.read_bytes()
    assert REFRESH_TOKEN.encode() not in store.key_path.read_bytes()

    restarted = SubscriptionStore(store_dir)
    loaded = restarted.load(SubscriptionProvider.OPENAI)
    assert loaded is not None
    assert loaded.access_token.get_secret_value() == ACCESS_TOKEN
    assert loaded.refresh_token is not None
    assert loaded.refresh_token.get_secret_value() == REFRESH_TOKEN
    assert loaded.project_id == "project-123"



def test_subscription_store_repairs_existing_key_permissions(tmp_path: Path) -> None:
    store_dir = tmp_path / "subscription-store"
    initial = SubscriptionStore(store_dir)
    os.chmod(initial.key_path, 0o644)

    restarted = SubscriptionStore(store_dir)

    assert restarted.key_path.stat().st_mode & 0o777 == 0o600
def test_subscription_store_status_and_repr_never_expose_tokens(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path / "subscription-store")
    store.save(SubscriptionProvider.OPENAI, _credential())

    status = store.status(SubscriptionProvider.OPENAI)
    rendered = " ".join((repr(store), repr(status), str(status), repr(status.model_dump())))

    assert status.authenticated is True
    assert status.account_label == "Research account"
    assert ACCESS_TOKEN not in rendered
    assert REFRESH_TOKEN not in rendered
    assert "access_token" not in rendered
    assert "refresh_token" not in rendered


def test_subscription_store_delete_removes_only_selected_record(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path / "subscription-store")
    store.save(SubscriptionProvider.OPENAI, _credential())
    store.save(
        SubscriptionProvider.GOOGLE,
        _credential().model_copy(
            update={"provider": SubscriptionProvider.GOOGLE, "account_label": "Google"}
        ),
    )

    store.delete(SubscriptionProvider.OPENAI)

    assert store.load(SubscriptionProvider.OPENAI) is None
    assert store.status(SubscriptionProvider.OPENAI).authenticated is False
    assert store.load(SubscriptionProvider.GOOGLE) is not None




def test_provider_refresh_and_logout_are_serialized_across_store_instances(
    tmp_path: Path,
) -> None:
    store_dir = tmp_path / "subscription-store"
    initial = SubscriptionStore(store_dir)
    initial.save(SubscriptionProvider.OPENAI, _credential())
    refresh_store = SubscriptionStore(store_dir)
    logout_store = SubscriptionStore(store_dir)
    refresh_started = threading.Event()
    release_refresh = threading.Event()
    logout_finished = threading.Event()
    failures: list[BaseException] = []

    class _BlockingOAuth:
        def refresh(self, _endpoint: str, **_kwargs: object) -> dict[str, object]:
            refresh_started.set()
            if not release_refresh.wait(3):
                raise AssertionError("refresh was not released")
            return {
                "access_token": "rotated-access-token",
                "refresh_token": REFRESH_TOKEN,
                "token_type": "Bearer",
                "expires_in": 3600,
            }

    backend = OpenAICodexBackend(store=refresh_store, oauth_client=_BlockingOAuth())
    logout_backend = OpenAICodexBackend(store=logout_store)

    def refresh() -> None:
        try:
            backend.refresh()
        except BaseException as exc:  # pragma: no cover - surfaced below
            failures.append(exc)

    def logout() -> None:
        try:
            logout_backend.logout()
        except BaseException as exc:  # pragma: no cover - surfaced below
            failures.append(exc)
        finally:
            logout_finished.set()

    refresh_thread = threading.Thread(target=refresh)
    logout_thread = threading.Thread(target=logout)
    refresh_thread.start()
    assert refresh_started.wait(2)
    logout_thread.start()
    assert not logout_finished.wait(0.2)
    release_refresh.set()
    refresh_thread.join(timeout=3)
    logout_thread.join(timeout=3)

    assert not refresh_thread.is_alive()
    assert not logout_thread.is_alive()
    assert failures == []
    assert logout_finished.is_set()
    assert logout_store.load(SubscriptionProvider.OPENAI) is None
def test_subscription_store_rejects_corrupt_ciphertext_with_typed_auth_error(
    tmp_path: Path,
) -> None:
    store = SubscriptionStore(tmp_path / "subscription-store")
    store.save(SubscriptionProvider.OPENAI, _credential())

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE subscription_credentials SET encrypted_record = ? WHERE provider = ?",
            (b"corrupt-ciphertext", SubscriptionProvider.OPENAI.value),
        )

    with pytest.raises(SubscriptionAuthError) as raised:
        store.load(SubscriptionProvider.OPENAI)
    assert raised.value.category == "storage"
    assert ACCESS_TOKEN not in str(raised.value)
    assert REFRESH_TOKEN not in repr(raised.value)


def test_subscription_store_binds_database_open_to_parent_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = tmp_path / "subscription-store"
    outside = tmp_path / "outside"
    outside.mkdir()
    moved_store_dir = tmp_path / "moved-subscription-store"
    original_open = storage_module.os.open
    replaced = False

    def racing_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replaced
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if (
            not replaced
            and dir_fd is not None
            and flags & getattr(os, "O_DIRECTORY", 0)
            and Path(path).name == store_dir.name
        ):
            replaced = True
            store_dir.rename(moved_store_dir)
            store_dir.symlink_to(outside, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(storage_module.os, "open", racing_open)
    store = SubscriptionStore(store_dir)
    store.save(SubscriptionProvider.OPENAI, _credential())

    moved_store = SubscriptionStore(moved_store_dir)
    assert moved_store.load(SubscriptionProvider.OPENAI) is not None
    assert not (outside / "subscriptions.sqlite3").exists()



def test_subscription_store_rejects_intermediate_directory_symlink(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (data_root / "nested").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SubscriptionAuthError):
        SubscriptionStore(data_root / "nested" / "store")

    assert not (outside / "subscriptions.sqlite3").exists()
    assert not (outside / "subscriptions.key").exists()

def test_subscription_store_rejects_database_inode_swap_before_sqlite_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SubscriptionStore(tmp_path / "subscription-store")
    database = store.database_path
    moved_database = tmp_path / "moved.sqlite3"
    outside_database = tmp_path / "outside.sqlite3"
    original_open = storage_module.os.open
    replaced = False

    def racing_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replaced
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if (
            not replaced
            and dir_fd is not None
            and flags & os.O_RDWR
            and Path(path).name == database.name
        ):
            replaced = True
            database.rename(moved_database)
            database.symlink_to(outside_database)
        return descriptor

    monkeypatch.setattr(storage_module.os, "open", racing_open)
    with pytest.raises(SubscriptionAuthError):
        store.save(SubscriptionProvider.OPENAI, _credential())

    assert not outside_database.exists()
