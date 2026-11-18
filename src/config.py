"""Configuration management for slide_service using Pydantic Settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from w2v_common.config import (
    APISettings as _BaseAPISettings,
)
from w2v_common.config import (
    AuthSettings,
    DatabaseSettings,
    LoggingSettings,
    RedisSettings,
)
from w2v_common.config import (
    CacheSettings as BaseCacheSettings,
)
from w2v_common.config import (
    StorageSettings as _BaseStorageSettings,
)


class APISettings(_BaseAPISettings):
    """Slide service API settings (port 6312)."""

    port: int = Field(default=6312, description="API server port")


class StorageSettings(_BaseStorageSettings):
    """File storage configuration for slide_service."""

    minio_external_endpoint: str | None = Field(
        default=None,
        description="External MinIO endpoint for presigned URLs (replaces internal host when set)",
    )


class PresentationSettings(BaseSettings):
    """Presentation-specific configuration."""

    default_theme: str = Field(default="modern", description="Default deck theme")
    default_aspect_ratio: str = Field(default="16:9", description="Default aspect ratio")
    max_slides_per_deck: int = Field(default=50, description="Hard limit on slides per deck")
    notes_enabled: bool = Field(default=True, description="Include speaker notes")

    model_config = SettingsConfigDict(
        env_prefix="PRESENTATION_", env_file=".env", case_sensitive=False, extra="ignore"
    )


class CacheSettings(BaseCacheSettings):
    """Result caching configuration with deck-specific TTL."""

    deck_ttl: int = Field(default=3600, description="Deck result cache TTL in seconds")


class RenderSettings(BaseSettings):
    """Chromium slide-render configuration (html_slide_renderer + browser_pool)."""

    render_max_concurrency: int = Field(
        default=2,
        description="Pool size = max concurrent Chromium render instances (process-wide gate)",
    )
    browser_max_renders: int = Field(
        default=200,
        description="Leases before a pooled Chromium is relaunched (memory-ratchet bound)",
    )
    page_timeout_ms: int = Field(
        default=15000, description="Per-slide page load + render timeout in milliseconds"
    )
    device_scale_factor: float = Field(
        default=2.0,
        ge=1.0,
        le=4.0,
        description=(
            "Chromium device pixel ratio for slide PNG capture (env "
            "RENDER_DEVICE_SCALE_FACTOR). 1.0 = 1920x1080. Raising it keeps the "
            "identical 1920x1080 CSS layout (no re-wrap) but emits a sharper "
            "higher-resolution PNG: ~1.333 -> 2560x1440 (2K), 2.0 -> 3840x2160 "
            "(4K). Per-request render_scale overrides this default."
            "\n\n"
            "The default is 2.0 (4K), NOT 1.0, and that is a DELIVERED-QUALITY "
            "decision, not a nicety. The video compiler's ken_burns stage zooms "
            "*into* the slide PNG, so at 1.0 it was cropping a sub-region of a "
            "1920x1080 bitmap and upscaling it back to a 1080p video frame: every "
            "zoomed slide shipped soft and below 1080p. Rendering at 2x gives the "
            "zoom 4x the pixel area to crop from, so the delivered frame stays "
            "true 1080p across the whole zoom range. The same defect was fixed the "
            "same way in the sibling html_render_service. Do not 'optimise' this "
            "back to 1.0 -- it is not a bandwidth tradeoff, it is the difference "
            "between a sharp deliverable and a blurry one. Cost is bounded and "
            "measured: the capture is a FIXED-viewport full_page=False shot, so "
            "the peak bitmap is 3840x2160x4B ~= 33MB vs ~8MB, i.e. ~+25MB per "
            "concurrent render (~+50MB across the default 2-slot pool) against a "
            "4Gi pod limit; PNG bytes roughly 2.2x (58KB -> 130KB measured) and "
            "wall-clock is flat. 1.333 was rejected as the alternative because it "
            "rounds to an ODD width (2559x1440), and an odd dimension is hostile "
            "to the yuv420p encoders downstream."
        ),
    )
    max_image_fetch_bytes: int = Field(
        default=25 * 1024 * 1024,
        ge=0,
        description="Max bytes for a remote/presigned image fetch; 0 = unlimited.",
    )

    model_config = SettingsConfigDict(
        env_prefix="RENDER_", env_file=".env", case_sensitive=False, extra="ignore"
    )


class ReaperSettings(BaseSettings):
    """Stuck-deck-job reaper configuration (multi-replica safety).

    A dead pod leaves its ``DeckJob`` wedged at ``status='processing'``: the Redis
    lock TTL-expires but nothing re-drives or fails the orphan. A lease-gated
    periodic reaper (``src.services.deck_reaper``) sweeps these to ``failed``.
    """

    enabled: bool = Field(
        default=True, description="Run the periodic stuck-job reaper background loop"
    )
    stale_threshold_seconds: int = Field(
        default=900,
        ge=60,
        description=(
            "A processing job whose updated_at is older than this is a reap candidate. "
            "Kept comfortably larger than the 300s Redis lock TTL AND the heartbeat "
            "interval so a live-but-slow job is never reaped (default 15 min)."
        ),
    )
    interval_seconds: int = Field(
        default=300, ge=30, description="How often the reaper sweep runs (seconds)"
    )
    heartbeat_seconds: int = Field(
        default=60,
        ge=10,
        description=(
            "How often a running _process_deck bumps its DeckJob.updated_at so a "
            "genuinely long job keeps a fresh updated_at and is never reaped."
        ),
    )

    model_config = SettingsConfigDict(
        env_prefix="REAPER_", env_file=".env", case_sensitive=False, extra="ignore"
    )


class Settings(BaseSettings):
    """Main application settings combining all configuration sections."""

    app_name: str = Field(default="Slide Service", description="Application name")
    app_version: str = Field(default="0.1.0", description="Application version")
    environment: str = Field(
        default="development", description="Environment (development, staging, production)"
    )

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    api: APISettings = Field(default_factory=APISettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    presentation: PresentationSettings = Field(default_factory=PresentationSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    render: RenderSettings = Field(default_factory=RenderSettings)
    reaper: ReaperSettings = Field(default_factory=ReaperSettings)

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    def get_database_url(self) -> str:
        """Return the configured PostgreSQL database URL."""
        return self.database.url

    def get_redis_url(self) -> str:
        """Return the configured Redis connection URL."""
        return self.redis.url

    def is_production(self) -> bool:
        """Return True if the environment is production."""
        return self.environment.lower() == "production"

    def is_development(self) -> bool:
        """Return True if the environment is development."""
        return self.environment.lower() == "development"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


def clear_settings_cache() -> None:
    """Clear the cached settings instance (useful for testing)."""
    get_settings.cache_clear()
