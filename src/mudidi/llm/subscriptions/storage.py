"""Separate encrypted local storage for subscription credentials.

This module intentionally does not import :mod:`cryptography` at module load
time. The optional web extra supplies Fernet when a store is instantiated;
importing the core subscription package therefore remains lightweight.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
from threading import RLock
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no POSIX flock
    fcntl = None  # type: ignore[assignment]

from mudidi.llm.subscriptions.types import (
    SubscriptionAuthError,
    SubscriptionCredential,
    SubscriptionProvider,
    SubscriptionStatus,
)

_DEFAULT_DATABASE_NAME = "subscriptions.sqlite3"
_DEFAULT_KEY_NAME = "subscriptions.key"
_DATABASE_MODE = 0o600
_DIRECTORY_MODE = 0o700


def json_values_equal(left: Any, right: Any) -> bool:
    """Compare JSON values without Python bool/integer coercion."""

    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, Mapping) or isinstance(right, Mapping):
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            return False
        return (
            left.keys() == right.keys()
            and all(json_values_equal(left[key], right[key]) for key in left)
        )
    left_is_sequence = isinstance(left, Sequence) and not isinstance(
        left, (str, bytes, bytearray)
    )
    right_is_sequence = isinstance(right, Sequence) and not isinstance(
        right, (str, bytes, bytearray)
    )
    if left_is_sequence or right_is_sequence:
        if not left_is_sequence or not right_is_sequence:
            return False
        return len(left) == len(right) and all(
            json_values_equal(item, other)
            for item, other in zip(left, right, strict=True)
        )
    if type(left) is not type(right):
        return False
    return left == right


def _provider(value: SubscriptionProvider | str) -> SubscriptionProvider:
    try:
        return value if isinstance(value, SubscriptionProvider) else SubscriptionProvider(value)
    except (TypeError, ValueError):
        raise ValueError("unsupported subscription provider") from None


def _storage_error(
    message: str = "subscription credential storage is unavailable",
    *,
    provider: SubscriptionProvider | None = None,
    reason: str = "storage_error",
    cause: BaseException | None = None,
) -> SubscriptionAuthError:
    error = SubscriptionAuthError(
        message,
        provider=provider,
        category="storage",
        metadata={"reason": reason},
    )
    return error


def _open_directory(path: Path, *, create: bool = False) -> int:
    """Open a managed directory by inode without following any path component."""

    selected = path.expanduser()
    components = selected.parts
    if selected.is_absolute():
        components = components[1:]
    if any(component == ".." for component in components):
        raise _storage_error(
            "managed subscription directory path is unsafe",
            reason="unsafe_path",
        )
    descriptor = -1
    try:
        descriptor = os.open(
            os.sep if selected.is_absolute() else ".",
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW,
        )
        for component in components:
            if component in {"", "."}:
                continue
            try:
                next_descriptor = os.open(
                    component,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, mode=_DIRECTORY_MODE, dir_fd=descriptor)
                except FileExistsError:
                    pass
                next_descriptor = os.open(
                    component,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
            previous_descriptor = descriptor
            descriptor = next_descriptor
            os.close(previous_descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise _storage_error(
                "managed subscription directory is not a real directory",
                reason="unsafe_path",
            )
        os.fchmod(descriptor, _DIRECTORY_MODE)
        return descriptor
    except SubscriptionAuthError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise _storage_error(
            "managed subscription directory is unavailable",
            reason="unsafe_path",
            cause=exc,
        ) from None


def _ensure_directory(path: Path) -> None:
    """Create one managed directory without following path symlinks."""

    descriptor = _open_directory(path, create=True)
    os.close(descriptor)


def _regular_file_descriptor(
    path: Path,
    *,
    flags: int,
    mode: int = _DATABASE_MODE,
    create: bool = False,
    parent_descriptor: int | None = None,
) -> tuple[int, int]:
    """Open a managed file relative to a validated parent directory."""

    parent_descriptor = (
        _open_directory(path.parent)
        if parent_descriptor is None
        else os.dup(parent_descriptor)
    )
    descriptor = -1
    try:
        selected_flags = flags | os.O_NOFOLLOW
        if create:
            selected_flags |= os.O_CREAT
        descriptor = os.open(
            path.name,
            selected_flags,
            mode,
            dir_fd=parent_descriptor,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise _storage_error(
                "managed subscription file is unsafe",
                reason="unsafe_path",
            )
        named_metadata = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            named_metadata.st_dev != metadata.st_dev
            or named_metadata.st_ino != metadata.st_ino
        ):
            raise _storage_error(
                "managed subscription file changed during open",
                reason="unsafe_path",
            )
        return parent_descriptor, descriptor
    except SubscriptionAuthError:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)
        raise


def _ensure_regular_file(
    path: Path,
    *,
    reason: str,
    parent_descriptor: int | None = None,
) -> None:
    """Reject symlinks and non-regular existing managed files."""

    try:
        parent_descriptor, descriptor = _regular_file_descriptor(
            path,
            flags=os.O_RDONLY,
            parent_descriptor=parent_descriptor,
        )
    except FileNotFoundError:
        return
    except SubscriptionAuthError:
        raise
    except OSError as exc:
        raise _storage_error(
            "managed subscription file is unsafe",
            reason=reason,
            cause=exc,
        ) from None
    try:
        os.close(descriptor)
    finally:
        os.close(parent_descriptor)


def _read_key(path: Path, *, directory_fd: int | None = None) -> bytes:
    try:
        parent_descriptor, descriptor = _regular_file_descriptor(
            path,
            flags=os.O_RDONLY,
            parent_descriptor=directory_fd,
        )
    except OSError as exc:
        raise _storage_error(reason="key_unavailable", cause=exc) from None
    try:
        os.fchmod(descriptor, _DATABASE_MODE)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 4096)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).strip()
    except OSError as exc:
        raise _storage_error(reason="key_unavailable", cause=exc) from None
    finally:
        os.close(descriptor)
        os.close(parent_descriptor)


def _load_or_create_key(
    path: Path,
    fernet_type: Any,
    *,
    directory_fd: int | None = None,
) -> bytes:
    """Read or atomically create a mode-0600 Fernet key without symlinks."""

    if directory_fd is None:
        _ensure_directory(path.parent)
    try:
        parent_descriptor, descriptor = _regular_file_descriptor(
            path,
            flags=os.O_WRONLY | os.O_EXCL,
            create=True,
            parent_descriptor=directory_fd,
        )
    except FileExistsError:
        return _validate_key(
            _read_key(path, directory_fd=directory_fd),
            fernet_type,
        )
    except OSError as exc:
        raise _storage_error(reason="key_unavailable", cause=exc) from None
    key = fernet_type.generate_key()
    try:
        view = memoryview(key)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("could not write encryption key")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, _DATABASE_MODE)
    except OSError as exc:
        raise _storage_error(reason="key_unavailable", cause=exc) from None
    finally:
        os.close(descriptor)
        os.close(parent_descriptor)
    return _validate_key(key, fernet_type)


def _validate_key(key: bytes, fernet_type: Any) -> bytes:
    if not key:
        raise _storage_error(reason="key_corrupt")
    try:
        fernet_type(key)
    except Exception as exc:  # Fernet raises ValueError for malformed keys.
        raise _storage_error(reason="key_corrupt", cause=exc) from None
    return key


class SubscriptionStore:
    """Fernet-encrypted SQLite records separate from API-key credentials.

    The database stores only a provider lookup key and Fernet ciphertext.  A
    decrypted :class:`SubscriptionCredential` is returned by ``load`` for the
    backend's in-memory use; ``status`` always constructs a token-free public
    status model instead.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        database_path: str | os.PathLike[str] | None = None,
        db_path: str | os.PathLike[str] | None = None,
        key_path: str | os.PathLike[str] | None = None,
    ) -> None:
        if database_path is not None and db_path is not None:
            raise TypeError("pass only one of database_path or db_path")
        selected_database = database_path if database_path is not None else db_path
        if path is not None and selected_database is not None:
            raise TypeError("pass either path or database_path, not both")
        if path is None and selected_database is None:
            raise TypeError("a subscription store path is required")

        if selected_database is None:
            supplied = Path(path).expanduser()
            if supplied.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                selected_database = supplied
            else:
                selected_database = supplied / _DEFAULT_DATABASE_NAME
        database = Path(selected_database).expanduser()
        key = (
            Path(key_path).expanduser()
            if key_path is not None
            else database.with_name(_DEFAULT_KEY_NAME)
        )
        self.database_path = database
        self.db_path = database
        self.key_path = key
        self._lock = RLock()
        self._database_directory_fd = -1
        self._key_directory_fd = -1

        try:
            self._database_directory_fd = _open_directory(
                database.parent,
                create=True,
            )
            if key.parent == database.parent:
                self._key_directory_fd = self._database_directory_fd
            else:
                self._key_directory_fd = _open_directory(
                    key.parent,
                    create=True,
                )
            _ensure_regular_file(
                database,
                reason="unsafe_database",
                parent_descriptor=self._database_directory_fd,
            )
            _ensure_regular_file(
                key,
                reason="unsafe_key",
                parent_descriptor=self._key_directory_fd,
            )

            # Keep cryptography optional for imports; only store construction needs it.
            from cryptography.fernet import Fernet

            self._fernet = Fernet(
                _load_or_create_key(
                    self.key_path,
                    Fernet,
                    directory_fd=self._key_directory_fd,
                )
            )
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS subscription_credentials (
                        provider TEXT PRIMARY KEY NOT NULL,
                        encrypted_record BLOB NOT NULL
                    )
                    """
                )
        except ImportError as exc:
            self._close_directory_descriptors()
            raise _storage_error(
                "encrypted subscription storage requires the optional web dependency",
                reason="cryptography_unavailable",
                cause=exc,
            ) from None
        except sqlite3.Error as exc:
            self._close_directory_descriptors()
            raise _storage_error(reason="database_unavailable", cause=exc) from None
        except OSError as exc:
            self._close_directory_descriptors()
            raise _storage_error(reason="database_unavailable", cause=exc) from None
        except SubscriptionAuthError:
            self._close_directory_descriptors()
            raise

    def _close_directory_descriptors(self) -> None:
        database_descriptor = getattr(self, "_database_directory_fd", -1)
        key_descriptor = getattr(self, "_key_directory_fd", -1)
        self._database_directory_fd = -1
        self._key_directory_fd = -1
        if key_descriptor >= 0 and key_descriptor != database_descriptor:
            try:
                os.close(key_descriptor)
            except OSError:
                pass
        if database_descriptor >= 0:
            try:
                os.close(database_descriptor)
            except OSError:
                pass

    def __del__(self) -> None:
        self._close_directory_descriptors()

    def __repr__(self) -> str:
        return (
            "SubscriptionStore("
            f"database_path={str(self.database_path)!r}, "
            f"key_path={str(self.key_path)!r})"
        )

    def _connect(self) -> sqlite3.Connection:
        """Open SQLite through a no-follow descriptor bound to its parent inode."""

        parent_descriptor = -1
        descriptor = -1
        connection: sqlite3.Connection | None = None
        try:
            parent_descriptor = os.dup(self._database_directory_fd)
            descriptor = os.open(
                self.database_path.name,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                _DATABASE_MODE,
                dir_fd=parent_descriptor,
            )
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise _storage_error(
                    "managed subscription database is unsafe",
                    reason="unsafe_database",
                )
            named_metadata = os.stat(
                self.database_path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (
                named_metadata.st_dev != metadata.st_dev
                or named_metadata.st_ino != metadata.st_ino
            ):
                raise _storage_error(
                    "managed subscription database changed during open",
                    reason="unsafe_database",
                )
            os.fchmod(descriptor, _DATABASE_MODE)
            descriptor_path = f"/dev/fd/{descriptor}"
            if not os.path.exists(descriptor_path):
                raise _storage_error(
                    "secure SQLite descriptor access is unavailable",
                    reason="database_unavailable",
                )
            connection = sqlite3.connect(
                f"file:{descriptor_path}?mode=rw",
                uri=True,
                timeout=5,
            )
            # /dev/fd paths have no usable sidecar directory; keep journaling
            # in memory while all database bytes remain on the opened inode.
            connection.execute("PRAGMA journal_mode = MEMORY")
            connection.execute("PRAGMA busy_timeout = 5000")
            return connection
        except SubscriptionAuthError:
            if connection is not None:
                connection.close()
            raise
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            raise _storage_error(
                reason="database_unavailable",
                cause=exc,
            ) from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if parent_descriptor >= 0:
                os.close(parent_descriptor)


    @contextmanager
    def provider_lock(self, provider: SubscriptionProvider | str) -> Iterator[None]:
        """Serialize one provider operation across store instances/processes."""

        selected_provider = _provider(provider)
        lock_path = self.database_path.with_name(
            f".{self.database_path.name}.{selected_provider.value}.lock"
        )
        parent_descriptor = -1
        descriptor = -1
        locked = False
        try:
            with self._lock:
                parent_descriptor, descriptor = _regular_file_descriptor(
                    lock_path,
                    flags=os.O_RDWR,
                    mode=_DATABASE_MODE,
                    create=True,
                    parent_descriptor=self._database_directory_fd,
                )
                if fcntl is None:
                    raise _storage_error(
                        "subscription operation locking is unavailable",
                        provider=selected_provider,
                        reason="lock_unavailable",
                    )
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                locked = True
                try:
                    yield
                finally:
                    if locked:
                        try:
                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                        except OSError:
                            pass
        except SubscriptionAuthError:
            raise
        except OSError as exc:
            raise _storage_error(
                "subscription operation locking is unavailable",
                provider=selected_provider,
                reason="lock_unavailable",
                cause=exc,
            ) from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if parent_descriptor >= 0:
                os.close(parent_descriptor)

    def save(
        self,
        provider: SubscriptionProvider | str | SubscriptionCredential,
        credential: SubscriptionCredential | None = None,
    ) -> None:
        """Encrypt and upsert one credential record.

        ``save(credential)`` is accepted as a convenience; the canonical form
        is ``save(provider, credential)`` so a backend's selected provider is
        explicit at the call site.
        """

        if isinstance(provider, SubscriptionCredential):
            if credential is not None:
                raise TypeError("credential must be omitted when passed as provider")
            credential = provider
            selected_provider = credential.provider
        else:
            selected_provider = _provider(provider)
            if credential is None:
                raise TypeError("credential is required")
        if not isinstance(credential, SubscriptionCredential):
            try:
                credential = SubscriptionCredential.model_validate(credential)
            except Exception:
                raise ValueError("invalid subscription credential") from None
        if credential.provider is not selected_provider:
            raise ValueError("credential provider does not match selected provider")

        payload = _serialize_credential(credential)
        try:
            encrypted = self._fernet.encrypt(payload)
            with self._lock, self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO subscription_credentials(provider, encrypted_record)
                    VALUES (?, ?)
                    ON CONFLICT(provider) DO UPDATE SET
                        encrypted_record = excluded.encrypted_record
                    """,
                    (selected_provider.value, encrypted),
                )
        except sqlite3.Error as exc:
            raise _storage_error(
                provider=selected_provider,
                reason="database_unavailable",
                cause=exc,
            ) from None

    def load(
        self,
        provider: SubscriptionProvider | str,
    ) -> SubscriptionCredential | None:
        """Decrypt one credential for backend use, or return ``None``."""

        selected_provider = _provider(provider)
        try:
            with self._lock, self._connect() as connection:
                row = connection.execute(
                    "SELECT encrypted_record FROM subscription_credentials "
                    "WHERE provider = ?",
                    (selected_provider.value,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise _storage_error(
                provider=selected_provider,
                reason="database_unavailable",
                cause=exc,
            ) from None
        if row is None:
            return None
        try:
            encrypted = row[0]
            if not isinstance(encrypted, (bytes, bytearray, memoryview)):
                raise TypeError("ciphertext is not bytes")
            plaintext = self._fernet.decrypt(bytes(encrypted))
            return _deserialize_credential(plaintext, selected_provider)
        except Exception as exc:
            if isinstance(exc, SubscriptionAuthError):
                raise
            raise _storage_error(
                "subscription credential record cannot be decrypted",
                provider=selected_provider,
                reason="corrupt_record",
                cause=exc,
            ) from None

    def delete(self, provider: SubscriptionProvider | str) -> None:
        """Delete only the selected provider's encrypted record."""

        selected_provider = _provider(provider)
        try:
            with self._lock, self._connect() as connection:
                connection.execute(
                    "DELETE FROM subscription_credentials WHERE provider = ?",
                    (selected_provider.value,),
                )
        except sqlite3.Error as exc:
            raise _storage_error(
                provider=selected_provider,
                reason="database_unavailable",
                cause=exc,
            ) from None

    def status(self, provider: SubscriptionProvider | str) -> SubscriptionStatus:
        """Return a token-free status model for UI and route surfaces."""

        selected_provider = _provider(provider)
        credential = self.load(selected_provider)
        if credential is None:
            return SubscriptionStatus(provider=selected_provider, authenticated=False)
        return SubscriptionStatus(
            provider=selected_provider,
            authenticated=True,
            account_label=credential.account_label,
            expires_at=credential.expires_at,
            metadata=credential.redacted_metadata,
        )

    # Small aliases keep provider adapters expressive without changing storage.
    remove = delete
    get = load


def _serialize_credential(credential: SubscriptionCredential) -> bytes:
    """Build the private JSON representation before Fernet encryption."""

    payload: dict[str, Any] = {
        "provider": credential.provider.value,
        "account_label": credential.account_label,
        "access_token": credential.access_token.get_secret_value(),
        "refresh_token": (
            credential.refresh_token.get_secret_value()
            if credential.refresh_token is not None
            else None
        ),
        "expires_at": (
            credential.expires_at.isoformat() if credential.expires_at is not None else None
        ),
        "project_id": credential.project_id,
        "account_id": credential.account_id,
        "metadata": dict(credential.metadata),
        "schema_version": credential.schema_version,
        "updated_at": credential.updated_at.isoformat(),
    }
    try:
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    except (TypeError, ValueError):
        raise ValueError("subscription credential metadata is not JSON serializable") from None


def _deserialize_credential(
    plaintext: bytes,
    provider: SubscriptionProvider,
) -> SubscriptionCredential:
    try:
        payload = json.loads(plaintext.decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("record payload is not an object")
        if payload.get("provider") != provider.value:
            raise ValueError("record provider does not match lookup provider")
        return SubscriptionCredential.model_validate(payload)
    except Exception as exc:
        raise _storage_error(
            "subscription credential record is corrupt",
            provider=provider,
            reason="corrupt_record",
            cause=exc,
        ) from None


__all__ = ["SubscriptionStore"]
