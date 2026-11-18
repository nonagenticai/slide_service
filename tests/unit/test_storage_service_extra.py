"""Extra storage service tests covering uncovered branches."""

from __future__ import annotations

import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.config import clear_settings_cache
from src.services.storage_service import StorageService


class _FakeMinio:
    def __init__(self, endpoint, *, access_key, secret_key, secure) -> None:
        self.bucket_created = True  # bucket already exists
        self.objects: dict[str, bytes] = {}

    async def bucket_exists(self, _bucket: str) -> bool:
        return self.bucket_created

    async def make_bucket(self, _bucket: str) -> None:
        self.bucket_created = True

    async def put_object(self, _bucket, object_name, data, *, length, content_type=None) -> None:
        self.objects[object_name] = data.read(length)

    async def stat_object(self, _bucket, object_name) -> None:
        if object_name not in self.objects:
            raise FileNotFoundError(object_name)

    async def get_object(self, _bucket, object_name):
        data = self.objects[object_name]

        class _Resp:
            async def read(self) -> bytes:
                return data

            def close(self) -> None:
                pass

            async def release(self) -> None:
                pass

        return _Resp()

    async def presigned_get_object(self, bucket, object_name, *, expires: timedelta) -> str:
        return f"https://minio.test/{bucket}/{object_name}"


class _FailingMinio(_FakeMinio):
    async def presigned_get_object(self, *args, **kwargs) -> str:
        # The facade catches OSError + miniopy errors and degrades to None; a
        # bare RuntimeError would (correctly) propagate, so model a realistic
        # transport failure.
        raise OSError("presign failed")


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


# ---------------------------------------------------------------------------
# Unsupported backend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsupported_backend_raises(monkeypatch: pytest.MonkeyPatch):
    """Unsupported storage backend raises ValueError."""
    monkeypatch.setenv("STORAGE_BACKEND", "s3-not-supported")
    clear_settings_cache()

    storage = StorageService()
    with pytest.raises(ValueError, match="Unsupported storage backend"):
        await storage.initialize()


# ---------------------------------------------------------------------------
# MinIO: bucket already exists (no make_bucket call)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minio_bucket_already_exists(monkeypatch: pytest.MonkeyPatch):
    """If bucket already exists, make_bucket is not called."""
    monkeypatch.setenv("STORAGE_BACKEND", "minio")
    monkeypatch.setenv("STORAGE_MINIO_ENDPOINT", "minio.test:9000")
    monkeypatch.setenv("STORAGE_MINIO_ACCESS_KEY", "a")
    monkeypatch.setenv("STORAGE_MINIO_SECRET_KEY", "s")
    monkeypatch.setenv("STORAGE_MINIO_BUCKET", "test-bucket")
    monkeypatch.setitem(sys.modules, "miniopy_async", SimpleNamespace(Minio=_FakeMinio))
    clear_settings_cache()

    storage = StorageService()
    await storage.initialize()  # Should not raise
    assert storage._facade is not None
    assert storage._facade._minio_client is not None


# ---------------------------------------------------------------------------
# MinIO: missing miniopy_async package
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minio_missing_package(monkeypatch: pytest.MonkeyPatch):
    """Missing miniopy-async package raises RuntimeError."""
    monkeypatch.setenv("STORAGE_BACKEND", "minio")
    monkeypatch.setenv("STORAGE_MINIO_ENDPOINT", "minio.test:9000")
    monkeypatch.setenv("STORAGE_MINIO_ACCESS_KEY", "a")
    monkeypatch.setenv("STORAGE_MINIO_SECRET_KEY", "s")
    clear_settings_cache()

    # Mock the import inside _init_minio to simulate missing package
    import builtins

    original_import = builtins.__import__

    def _mock_import(name, *args, **kwargs):
        if name == "miniopy_async":
            raise ImportError("No module named 'miniopy_async'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _mock_import)

    storage = StorageService()
    with pytest.raises(RuntimeError, match="miniopy-async is required"):
        await storage.initialize()


# ---------------------------------------------------------------------------
# MinIO: read_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minio_read_file(monkeypatch: pytest.MonkeyPatch):
    """read_file fetches bytes from MinIO object store."""
    monkeypatch.setenv("STORAGE_BACKEND", "minio")
    monkeypatch.setenv("STORAGE_MINIO_ENDPOINT", "minio.test:9000")
    monkeypatch.setenv("STORAGE_MINIO_ACCESS_KEY", "a")
    monkeypatch.setenv("STORAGE_MINIO_SECRET_KEY", "s")
    monkeypatch.setenv("STORAGE_MINIO_BUCKET", "test-bucket")
    monkeypatch.setitem(sys.modules, "miniopy_async", SimpleNamespace(Minio=_FakeMinio))
    clear_settings_cache()

    storage = StorageService()
    await storage.initialize()
    await storage.write_file("some/file.txt", b"hello", content_type="text/plain")
    data = await storage.read_file("some/file.txt")
    assert data == b"hello"


# ---------------------------------------------------------------------------
# MinIO: exists returns False for missing object
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minio_exists_false(monkeypatch: pytest.MonkeyPatch):
    """exists returns False when MinIO stat_object raises."""
    monkeypatch.setenv("STORAGE_BACKEND", "minio")
    monkeypatch.setenv("STORAGE_MINIO_ENDPOINT", "minio.test:9000")
    monkeypatch.setenv("STORAGE_MINIO_ACCESS_KEY", "a")
    monkeypatch.setenv("STORAGE_MINIO_SECRET_KEY", "s")
    monkeypatch.setenv("STORAGE_MINIO_BUCKET", "test-bucket")
    monkeypatch.setitem(sys.modules, "miniopy_async", SimpleNamespace(Minio=_FakeMinio))
    clear_settings_cache()

    storage = StorageService()
    await storage.initialize()
    result = await storage.exists("no/such/file.txt")
    assert result is False


# ---------------------------------------------------------------------------
# MinIO: presigned URL failure returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minio_presigned_url_failure(monkeypatch: pytest.MonkeyPatch):
    """presigned URL failure returns None (fail-safe)."""
    monkeypatch.setenv("STORAGE_BACKEND", "minio")
    monkeypatch.setenv("STORAGE_MINIO_ENDPOINT", "minio.test:9000")
    monkeypatch.setenv("STORAGE_MINIO_ACCESS_KEY", "a")
    monkeypatch.setenv("STORAGE_MINIO_SECRET_KEY", "s")
    monkeypatch.setenv("STORAGE_MINIO_BUCKET", "test-bucket")
    monkeypatch.setitem(sys.modules, "miniopy_async", SimpleNamespace(Minio=_FailingMinio))
    clear_settings_cache()

    storage = StorageService()
    await storage.initialize()
    result = await storage.get_presigned_url("some/file.txt")
    assert result is None


# ---------------------------------------------------------------------------
# MinIO: get_full_path raises NotImplementedError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minio_get_full_path_raises(monkeypatch: pytest.MonkeyPatch):
    """get_full_path raises NotImplementedError for MinIO backend."""
    monkeypatch.setenv("STORAGE_BACKEND", "minio")
    monkeypatch.setenv("STORAGE_MINIO_ENDPOINT", "minio.test:9000")
    monkeypatch.setenv("STORAGE_MINIO_ACCESS_KEY", "a")
    monkeypatch.setenv("STORAGE_MINIO_SECRET_KEY", "s")
    monkeypatch.setenv("STORAGE_MINIO_BUCKET", "test-bucket")
    monkeypatch.setitem(sys.modules, "miniopy_async", SimpleNamespace(Minio=_FakeMinio))
    clear_settings_cache()

    storage = StorageService()
    await storage.initialize()
    with pytest.raises(NotImplementedError):
        storage.get_full_path("some/file.txt")


# ---------------------------------------------------------------------------
# MinIO: _guess_content_type fallback
# ---------------------------------------------------------------------------


def test_guess_content_type_fallback(monkeypatch: pytest.MonkeyPatch):
    """Unknown extension falls back to application/octet-stream."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", "/tmp/test")
    clear_settings_cache()

    storage = StorageService()
    ct = storage._guess_content_type("some/file.unknown_ext_xyz")
    assert ct == "application/octet-stream"


def test_guess_content_type_provided(monkeypatch: pytest.MonkeyPatch):
    """Explicit content_type is returned as-is."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", "/tmp/test")
    clear_settings_cache()

    storage = StorageService()
    ct = storage._guess_content_type("some/file.pdf", content_type="application/pdf")
    assert ct == "application/pdf"
