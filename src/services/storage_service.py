"""Local/MinIO file storage for slide_service.

Thin domain wrapper over the shared ``w2v_common.storage.StorageService``
facade (the fleet-wide consolidation of the hand-rolled per-service storage
modules). The facade owns the local/MinIO client, presign + host-rewrite, and
the object primitives; this wrapper keeps only the slide_service-specific
contract on top of it:

* a **relative-key** model (callers pass ``decks/<job>/deck.pptx``),
* a **path-traversal guard** — the facade operates on bare keys and does NOT
  confine a local-backend key to the storage root, so ``../escape`` is rejected
  here before it reaches the facade,
* the **FileNotFoundError** read contract — the facade returns ``None`` for an
  absent object; ``read_file`` translates that (and a rejected traversal) into
  the filesystem-shaped raise its callers expect,
* ``get_full_path`` for the local backend (and its ``NotImplementedError`` on
  the MinIO backend), used by the image resolver.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING

from w2v_common.storage import StorageService as _FacadeStorageService

from src.config import get_settings

if TYPE_CHECKING:
    from src.config import StorageSettings

logger = logging.getLogger(__name__)


class StorageService:
    """Abstraction over local filesystem or MinIO for deck artifacts."""

    def __init__(self) -> None:
        settings = get_settings()
        self._storage_settings: StorageSettings = settings.storage
        self._backend = settings.storage.backend
        self._local_path = Path(settings.storage.local_path)
        self._facade: _FacadeStorageService | None = None

    @property
    def backend_type(self) -> str:
        """Return the configured backend type."""
        return str(self._backend).strip().lower()

    async def initialize(self) -> None:
        """Initialize the configured storage backend."""
        await self._ensure_ready()

    async def _ensure_ready(self) -> _FacadeStorageService:
        """Build + initialize the shared facade once, lazily.

        Callers reach storage both eagerly (ASGI lifespan calls
        ``initialize()``) and lazily (a request handler / DeckBuilder that
        constructs a bare ``StorageService()``), so the facade is created on
        first use and reused thereafter. ``initialize()`` on the facade creates
        the local dir / ensures the bucket and raises ``ValueError`` for an
        unsupported backend — same shape as before.
        """
        if self._facade is None:
            facade = _FacadeStorageService(self._storage_settings)
            await facade.initialize()
            self._facade = facade
        return self._facade

    def _local_full_path(self, relative_path: str) -> Path:
        """Resolve ``relative_path`` against the storage root.

        Confines the result to ``self._local_path``: a traversal like
        ``../etc/passwd`` raises ``ValueError`` rather than escaping. The shared
        facade operates on bare keys and does not enforce this, so the guard
        lives here. Server-side callers always pass a server-generated relative
        path, so a rejection here means a developer or DB-tampering bug worth
        surfacing loudly. Read helpers (``read_file``, ``exists``) catch the
        rejection and translate it to their respective "not found" responses.
        """
        root = self._local_path.resolve()
        candidate = self._local_path / relative_path
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"Invalid storage path: {relative_path}") from exc
        if not resolved.is_relative_to(root):
            raise ValueError(f"Rejected path outside storage root: {relative_path}")
        return resolved

    def _guess_content_type(self, relative_path: str, content_type: str | None = None) -> str:
        if content_type:
            return content_type
        return mimetypes.guess_type(relative_path)[0] or "application/octet-stream"

    async def write_file(
        self,
        relative_path: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> str:
        """Write bytes to storage, return the relative path."""
        facade = await self._ensure_ready()
        if self.backend_type == "local":
            # Reject a traversal before it reaches the facade (which does not guard).
            self._local_full_path(relative_path)
        await facade.put_bytes(
            relative_path,
            data,
            content_type=self._guess_content_type(relative_path, content_type),
        )
        return relative_path

    async def write_text(
        self,
        relative_path: str,
        text: str,
        *,
        content_type: str = "text/plain; charset=utf-8",
    ) -> str:
        """Write text to storage, return the relative path."""
        return await self.write_file(relative_path, text.encode("utf-8"), content_type=content_type)

    async def read_file(self, relative_path: str) -> bytes:
        """Read bytes from storage.

        Raises ``FileNotFoundError`` if the relative path escapes the storage
        root or does not exist — same shape as the underlying filesystem call.
        The facade returns ``None`` for an absent object; this wrapper translates
        that into the ``FileNotFoundError`` its callers expect.
        """
        facade = await self._ensure_ready()
        if self.backend_type == "local":
            try:
                self._local_full_path(relative_path)
            except ValueError as exc:
                raise FileNotFoundError(str(exc)) from exc
        data = await facade.get_bytes(relative_path)
        if data is None:
            raise FileNotFoundError(f"Object not found in storage: {relative_path}")
        return data

    async def exists(self, relative_path: str) -> bool:
        """Check if a file exists in storage.

        Paths outside the storage root return ``False`` — the API contract is
        "exists *inside* storage", not "exists on disk". The facade's
        ``object_exists_key`` is tri-state (True / False / None-indeterminate);
        anything other than a definite True collapses to ``False`` here to
        preserve the existing boolean contract.
        """
        facade = await self._ensure_ready()
        if self.backend_type == "local":
            try:
                self._local_full_path(relative_path)
            except ValueError:
                return False
        return await facade.object_exists_key(relative_path) is True

    async def get_presigned_url(
        self,
        relative_path: str,
        expires_seconds: int | None = None,
    ) -> str | None:
        """Return a presigned download URL when supported.

        Non-MinIO backends return ``None``. When
        ``STORAGE_MINIO_EXTERNAL_ENDPOINT`` is configured the facade rewrites the
        internal host of the presigned URL so clients outside Docker/k8s can
        reach MinIO directly.
        """
        facade = await self._ensure_ready()
        if self.backend_type != "minio":
            return None
        return await facade.presign_get(relative_path, expires=expires_seconds)

    def get_full_path(self, relative_path: str) -> str:
        """Get the full filesystem path for a relative path."""
        if self.backend_type == "local":
            return str(self._local_full_path(relative_path))
        raise NotImplementedError("Full filesystem paths are not available for the MinIO backend")
