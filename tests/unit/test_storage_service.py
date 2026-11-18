"""Unit tests for local and MinIO-backed storage."""

from __future__ import annotations

import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.config import clear_settings_cache
from src.services.storage_service import StorageService


class _FakeMinio:
    def __init__(self, endpoint: str, *, access_key: str, secret_key: str, secure: bool) -> None:
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.secure = secure
        self.bucket_created = False
        self.objects: dict[str, tuple[bytes, str | None]] = {}

    async def bucket_exists(self, _bucket: str) -> bool:
        return self.bucket_created

    async def make_bucket(self, _bucket: str) -> None:
        self.bucket_created = True

    async def put_object(
        self,
        _bucket: str,
        object_name: str,
        data,
        *,
        length: int,
        content_type: str | None = None,
    ) -> None:
        self.objects[object_name] = (data.read(length), content_type)

    async def stat_object(self, _bucket: str, object_name: str) -> None:
        if object_name not in self.objects:
            raise FileNotFoundError(object_name)

    async def presigned_get_object(
        self,
        bucket: str,
        object_name: str,
        *,
        expires: timedelta,
    ) -> str:
        return f"https://minio.test/{bucket}/{object_name}?expires={int(expires.total_seconds())}"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.mark.asyncio
async def test_local_storage_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", str(tmp_path))
    storage = StorageService()

    await storage.initialize()
    relative_path = await storage.write_text("decks/job-1/manifest.json", '{"ok":true}')

    assert relative_path == "decks/job-1/manifest.json"
    assert await storage.exists(relative_path) is True
    assert await storage.read_file(relative_path) == b'{"ok":true}'
    assert await storage.get_presigned_url(relative_path) is None


@pytest.mark.asyncio
async def test_minio_storage_writes_and_presigns(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STORAGE_BACKEND", "minio")
    monkeypatch.setenv("STORAGE_MINIO_ENDPOINT", "minio.test:9000")
    monkeypatch.setenv("STORAGE_MINIO_ACCESS_KEY", "minio")
    monkeypatch.setenv("STORAGE_MINIO_SECRET_KEY", "minio-secret")
    monkeypatch.setenv("STORAGE_MINIO_BUCKET", "slide-service")
    monkeypatch.setenv("STORAGE_PRESIGN_EXPIRY_SECONDS", "900")
    monkeypatch.setitem(sys.modules, "miniopy_async", SimpleNamespace(Minio=_FakeMinio))

    storage = StorageService()

    await storage.initialize()
    relative_path = await storage.write_file(
        "decks/job-1/deck.pdf",
        b"%PDF-1.7\n",
        content_type="application/pdf",
    )

    assert relative_path == "decks/job-1/deck.pdf"
    assert await storage.exists(relative_path) is True
    assert await storage.get_presigned_url(relative_path) == (
        "https://minio.test/slide-service/decks/job-1/deck.pdf?expires=900"
    )

    # The minio client now lives on the shared facade the wrapper delegates to.
    assert storage._facade is not None
    fake_client = storage._facade._minio_client
    assert isinstance(fake_client, _FakeMinio)
    assert fake_client.bucket_created is True
    assert fake_client.objects["decks/job-1/deck.pdf"] == (b"%PDF-1.7\n", "application/pdf")


@pytest.mark.asyncio
async def test_minio_presigned_url_host_rewrite(monkeypatch: pytest.MonkeyPatch):
    """When STORAGE_MINIO_EXTERNAL_ENDPOINT is set the presigned host is rewritten."""
    monkeypatch.setenv("STORAGE_BACKEND", "minio")
    monkeypatch.setenv("STORAGE_MINIO_ENDPOINT", "minio.test:9000")
    monkeypatch.setenv("STORAGE_MINIO_ACCESS_KEY", "minio")
    monkeypatch.setenv("STORAGE_MINIO_SECRET_KEY", "minio-secret")
    monkeypatch.setenv("STORAGE_MINIO_BUCKET", "slide-service")
    monkeypatch.setenv("STORAGE_MINIO_EXTERNAL_ENDPOINT", "https://minio.public:9443")
    monkeypatch.setitem(sys.modules, "miniopy_async", SimpleNamespace(Minio=_FakeMinio))

    storage = StorageService()
    await storage.initialize()
    await storage.write_file("decks/job-1/deck.pdf", b"%PDF-1.7\n", content_type="application/pdf")

    url = await storage.get_presigned_url("decks/job-1/deck.pdf")
    assert url is not None
    # Host swapped to the external endpoint; path (bucket/key) preserved.
    assert url.startswith("https://minio.public:9443/slide-service/decks/job-1/deck.pdf")


@pytest.mark.asyncio
async def test_minio_backend_resolves_and_initializes(monkeypatch: pytest.MonkeyPatch):
    """Dual-backend regression: a minio-backed StorageSettings resolves and the
    wrapper's ``initialize()`` builds the shared facade without raising
    (miniopy mocked). The local backend is exercised by the round-trip test
    above; this asserts the second backend still resolves after the migration.
    """
    from src.config import StorageSettings

    # The config subclass accepts an explicit minio backend + external endpoint.
    settings = StorageSettings(
        backend="minio",
        minio_endpoint="minio.test:9000",
        minio_access_key="a",
        minio_secret_key="s",
        minio_bucket="slide-service",
    )
    assert settings.backend == "minio"

    monkeypatch.setenv("STORAGE_BACKEND", "minio")
    monkeypatch.setenv("STORAGE_MINIO_ENDPOINT", "minio.test:9000")
    monkeypatch.setenv("STORAGE_MINIO_ACCESS_KEY", "a")
    monkeypatch.setenv("STORAGE_MINIO_SECRET_KEY", "s")
    monkeypatch.setenv("STORAGE_MINIO_BUCKET", "slide-service")
    monkeypatch.setitem(sys.modules, "miniopy_async", SimpleNamespace(Minio=_FakeMinio))

    storage = StorageService()
    await storage.initialize()  # must not raise
    assert storage.backend_type == "minio"
    assert storage._facade is not None


@pytest.mark.asyncio
async def test_local_storage_rejects_traversal_on_read(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """A ``../`` relative path must not escape the storage root."""
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", str(root))
    # Plant a target file outside the storage root.
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"sensitive")

    storage = StorageService()
    await storage.initialize()

    assert await storage.exists("../secret.txt") is False
    with pytest.raises(FileNotFoundError):
        await storage.read_file("../secret.txt")
    # Target file untouched.
    assert outside.read_bytes() == b"sensitive"


@pytest.mark.asyncio
async def test_local_storage_rejects_traversal_on_write(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """A ``../`` relative path on write must raise rather than escape."""
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", str(root))

    storage = StorageService()
    await storage.initialize()

    with pytest.raises(ValueError, match="Rejected path outside storage root"):
        await storage.write_text("../escape.txt", "nope")
    # Nothing planted in either location.
    assert not (tmp_path / "escape.txt").exists()
