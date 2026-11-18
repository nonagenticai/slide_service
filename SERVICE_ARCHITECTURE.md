# Slide Service Architecture

## Overview

- **Port:** `6312`
- **DB port:** `5462`
- **Redis DB:** `6396/1`
- **Repo path:** `slide_service/`
- **Purpose:** Generate branded PowerPoint decks, slide manifests, and presentation assets as an independent microservice.
- **Authoring layer:** Python + `python-pptx`
- **LLM integration:** Optional content shaping routed through `worker_management` via `WMClient` (worker `slide-bullet-extractor`); WM is the sole LLM gateway and handles provider/model + license-token metering

The slide service is a **consumer-agnostic microservice**. It accepts structured content, applies templates, optionally polishes content via LLM, and produces `.pptx` files plus a JSON manifest. Any orchestrator or application can call it -- it has no hard dependencies on w2v services.

## Why A Dedicated Service

The existing ecosystem can produce HTML redesigns, screenshots, recordings, title cards, and compiled videos. What it lacks is a first-class service for structured, editable deck output.

Slides are useful in three ways:

1. **Sales artifact** -- send directly as `.pptx`
2. **Structured intermediate asset** -- any video compiler or renderer can consume the manifest
3. **Training format** -- chaptered tutorial decks for enterprise workflows

PowerPoint is the canonical output because it is human-editable after generation. The service uses `python-pptx` for authoring and treats rendering/export as a downstream concern.

## Service Scope

### In Scope (MVP)

- Template CRUD -- create, read, update, soft-delete templates via API
- 11 seed templates with DB-stored slot definitions and styling rules
- Async deck generation from structured content + selected template(s)
- LLM content shaping (optional per-request) -- summarize, generate headlines/notes/transitions
- Branded `.pptx` output with notes, timing hints, transition/animation metadata
- Consumer-agnostic JSON manifest
- PPTX download endpoint
- Brand configuration (colors, fonts, theme)
- Template versioning for reproducibility
- Auth middleware (license token validation)
- Health/readiness/liveness endpoints
- Observability (structured logging, request tracing)
- Docker + CI/CD

### Out Of Scope

- Code or repo analysis
- Campaign orchestration
- Final video compilation (consumers handle this)
- Browser recording
- Free-form office document generation

### Phase 2

- PNG/PDF export via headless LibreOffice bridge
- Charts and data tables
- Localized deck variants
- Per-slide animations beyond metadata
- Template marketplace
- Webhook notifications on job completion
- Real-time collaboration

## API Surface

All routes follow the unversioned convention: `/api/<resource>`.

### Template CRUD

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/templates` | List active templates (paginated, filterable by `category`, `layout_type`) |
| `GET` | `/api/templates/{template_id}` | Get single template with slot definitions |
| `POST` | `/api/templates` | Create custom template |
| `PUT` | `/api/templates/{template_id}` | Update template (auto-versions if used by any deck) |
| `DELETE` | `/api/templates/{template_id}` | Soft-delete (sets `is_active=false`) |
| `GET` | `/api/templates/{template_id}/versions` | List frozen versions of a template |

### Deck Generation (Async Job)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/decks` | Create deck generation job (returns 202) |
| `GET` | `/api/decks/{job_id}` | Poll job status and metadata |
| `GET` | `/api/decks/{job_id}/pptx` | Download generated `.pptx` |
| `GET` | `/api/decks/{job_id}/manifest` | Download manifest JSON |
| `POST` | `/api/decks/{job_id}/export` | Export PNG/PDF (Phase 2) |

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Full health (DB, Redis, storage) |
| `GET` | `/health/ready` | Readiness check |
| `GET` | `/health/live` | Liveness check |

## Request / Response Schemas

### `POST /api/decks` -- Create Deck Job

```json
{
  "title": "Q1 Website Audit Findings",
  "template_ids": ["uuid-of-title", "uuid-of-scorecard", "uuid-of-finding-detail"],
  "slides": [
    {
      "template_id": "uuid-of-title",
      "content": {
        "title": "Website Performance Audit",
        "subtitle": "Q1 2026 Results"
      }
    },
    {
      "template_id": "uuid-of-scorecard",
      "content": {
        "title": "Overall Scores",
        "metrics": [
          {"label": "Performance", "value": "72/100"},
          {"label": "Accessibility", "value": "88/100"}
        ]
      }
    }
  ],
  "brand": {
    "primary_color": "#1a73e8",
    "secondary_color": "#34a853",
    "font_family": "Inter",
    "logo_url": "https://example.com/logo.png",
    "theme": "modern"
  },
  "aspect_ratio": "16:9",
  "content_shaper": {
    "enabled": true,
    "summarize": true,
    "generate_speaker_notes": true,
    "generate_transitions": true,
    "tone": "professional"
  },
  "timing": {
    "target_duration_seconds": 120,
    "default_slide_duration_seconds": 15
  }
}
```

### `POST /api/decks` -- Response (202)

```json
{
  "job_id": "019563a2-...",
  "status": "processing"
}
```

### `GET /api/decks/{job_id}` -- Response

```json
{
  "job_id": "019563a2-...",
  "status": "completed",
  "slide_count": 8,
  "created_at": "2026-03-10T14:00:00Z",
  "error": null
}
```

### `POST /api/templates` -- Create Template

```json
{
  "name": "scorecard",
  "display_name": "Scorecard",
  "category": "data",
  "layout_type": "scorecard",
  "slot_definitions": {
    "title": {"type": "text", "max_length": 80, "required": true},
    "metrics": {"type": "list", "max_items": 6, "required": true, "item_schema": {
      "label": {"type": "text", "max_length": 40},
      "value": {"type": "text", "max_length": 20}
    }},
    "footer": {"type": "text", "max_length": 120, "required": false}
  },
  "styling_rules": {
    "background": "brand_primary_light",
    "title_font_size": 28,
    "metric_font_size": 36,
    "layout": "grid",
    "grid_columns": 3
  },
  "default_transition": {"type": "fade", "duration_seconds": 0.5},
  "default_timing_hint_seconds": 12
}
```

## Manifest Format

The manifest is consumer-agnostic. No references to specific downstream services.

```json
{
  "manifest_version": "1.0",
  "deck_id": "019563a2-...",
  "title": "Q1 Website Audit Findings",
  "aspect_ratio": "16:9",
  "slide_count": 8,
  "total_estimated_duration_seconds": 120.0,
  "brand": {
    "primary_color": "#1a73e8",
    "secondary_color": "#34a853",
    "font_family": "Inter",
    "theme": "modern"
  },
  "assets": {
    "pptx": "decks/019563a2-.../deck.pptx",
    "manifest": "decks/019563a2-.../manifest.json"
  },
  "slides": [
    {
      "index": 0,
      "template_name": "title",
      "template_version_id": "019563b1-...",
      "content": {
        "title": "Website Performance Audit",
        "subtitle": "Q1 2026 Results"
      },
      "speaker_notes": "Welcome to the Q1 website performance audit...",
      "timing_hint_seconds": 8.0,
      "transition": {"type": "fade", "duration_seconds": 0.5},
      "animations": []
    }
  ]
}
```

## Template System

### Design Principles

1. **Templates in DB, not code** -- Slot definitions, styling rules, and metadata are stored in PostgreSQL. Python code in `src/services/templates/*.py` provides rendering logic only.
2. **Template versioning** -- When a template is used by a deck, a snapshot is frozen in `template_versions`. Subsequent template edits create new versions without breaking existing decks.
3. **Seed templates** -- 11 built-in templates are seeded on first startup. They have `is_system=true` and cannot be deleted.
4. **Deterministic first** -- Templates define fixed layouts with content slots. LLM freedom is constrained to content polishing within slots, not layout generation.

### 11 Seed Templates

| Name | Layout Type | Slots | Use Case |
|------|-------------|-------|----------|
| `title` | Title slide | title, subtitle, logo | Opening slide |
| `agenda` | Bullet list | title, items[] | Outline / table of contents |
| `scorecard` | Metric grid | title, metrics[], footer | Performance scores |
| `finding_detail` | Two-column | title, description, severity, recommendation, screenshot_url | Individual finding |
| `comparison` | Side-by-side | title, before_label, before_image_url, after_label, after_image_url, caption | Before/after |
| `feature_spotlight` | Hero + text | title, description, image_url, bullets[] | Feature highlight |
| `process_flow` | Numbered steps | title, steps[] | Workflow / process |
| `metric_summary` | KPI dashboard | title, kpis[], trend_description | Data overview |
| `roadmap` | Timeline | title, phases[] | Phased plan |
| `cta` | CTA card | title, body, cta_text, cta_url, contact_info | Call to action / next steps |
| `tutorial_step` | Step-by-step | step_number, title, instruction, screenshot_url, tip | Tutorial |

Each seed template defines:
- `slot_definitions` (JSONB) -- typed slots with max_length, required flag, nested item schemas
- `styling_rules` (JSONB) -- font sizes, layout mode, background, spacing
- `default_transition` -- transition type and duration
- `default_timing_hint_seconds` -- suggested display duration

## Database Models

Three tables, following the same ORM pattern as `narrative_service/src/database/models.py`.

### `slide_templates`

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer | Auto-increment PK |
| `template_id` | String(36) | UUID, unique, indexed |
| `tenant_id` | String(255) | Tenant scope, indexed |
| `name` | String(100) | Machine name (e.g. `scorecard`) |
| `display_name` | String(200) | Human label |
| `category` | String(50) | Grouping (e.g. `data`, `narrative`, `cta`) |
| `layout_type` | String(50) | Layout identifier |
| `slot_definitions` | JSONB | Typed slot schema |
| `styling_rules` | JSONB | Visual configuration |
| `default_transition` | JSONB | Default transition config |
| `default_timing_hint_seconds` | Float | Suggested slide duration |
| `is_system` | Boolean | True for seed templates |
| `is_active` | Boolean | Soft-delete flag |
| `created_at` | DateTime(tz) | server_default=func.now() |
| `updated_at` | DateTime(tz) | onupdate=func.now() |

### `template_versions`

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer | Auto-increment PK |
| `version_id` | String(36) | UUID, unique, indexed |
| `template_id` | String(36) | FK to `slide_templates.template_id` |
| `version_number` | Integer | Monotonic per template |
| `snapshot_hash` | String(64) | SHA-256 of frozen data |
| `slot_definitions` | JSONB | Frozen slot schema at this version |
| `styling_rules` | JSONB | Frozen styling at this version |
| `default_transition` | JSONB | Frozen transition config |
| `default_timing_hint_seconds` | Float | Frozen duration hint |
| `created_at` | DateTime(tz) | server_default=func.now() |

### `deck_jobs`

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer | Auto-increment PK |
| `job_id` | String(36) | UUID, unique, indexed |
| `tenant_id` | String(255) | Tenant scope, indexed |
| `status` | String(20) | `pending` / `processing` / `completed` / `failed` |
| `request_payload` | JSONB | Full request body for reproducibility |
| `manifest` | JSONB | Generated manifest on completion |
| `pptx_storage_path` | String(500) | Path to stored `.pptx` file |
| `error` | Text | Error message on failure |
| `created_at` | DateTime(tz) | server_default=func.now() |
| `updated_at` | DateTime(tz) | onupdate=func.now() |

### Alembic Migrations

- Migration directory: `src/database/migrations/`
- One migration per table group: initial schema creates all three tables
- Follows the same Alembic + async pattern as other services

## LLM Content Shaping

The `ContentShaper` service optionally polishes raw content before rendering. It uses the same LLM stack as all other services: `openai.AsyncOpenAI` with `deepseek-v4-flash`.

### Capabilities

| Function | Description |
|----------|-------------|
| Summarize | Truncate/summarize raw content to fit slot `max_length` |
| Headlines | Generate concise slide titles from verbose input |
| Speaker notes | Generate presenter notes per slide |
| Transitions | Generate natural transition text between slides |

### Activation

Content shaping is **optional per-request** via the `content_shaper` field in the deck creation request:

```json
{
  "content_shaper": {
    "enabled": true,
    "summarize": true,
    "generate_speaker_notes": true,
    "generate_transitions": true,
    "tone": "professional"
  }
}
```

Callers with pre-polished content set `enabled: false` (or omit the field) and skip all LLM calls.

### Prompt Templates

Jinja2 templates in `src/templates/content_shaper/`:

- `deck_shaping.md` -- Main prompt for content shaping. Receives the full slide list, slot definitions, brand context, and tone. Returns structured JSON with polished content per slide.

### Configuration

Uses `LLMSettings` (env prefix `LLM_`), identical to `narrative_service/src/config.py`:

| Var | Default | Description |
|-----|---------|-------------|
| `LLM_API_KEY` | `""` | API key |
| `LLM_BASE_URL` | `""` | OpenAI-compatible base URL |
| `LLM_MODEL` | `deepseek-v4-flash` | Model identifier |
| `LLM_MAX_TOKENS` | `4000` | Max response tokens |
| `LLM_TEMPERATURE` | `0.7` | Temperature |
| `LLM_TIMEOUT_SECONDS` | `90` | Request timeout |

## Async Job Pattern

Deck generation follows the same background task + Redis lock pattern as `narrative_service/src/api/routes.py`:

1. `POST /api/decks` creates a `deck_jobs` row with `status=processing`, returns 202 with `job_id`
2. `asyncio.create_task()` spawns background work
3. Background task acquires Redis job lock via `acquire_job_lock(redis, job_id)` (prevents duplicate processing across pods)
4. On completion: updates DB row with manifest + pptx path, writes to Redis cache (fail-open)
5. On failure: marks job as `failed` with error message
6. Always releases lock in `finally` block
7. `GET /api/decks/{job_id}` polls status -- checks Redis first (fail-open), falls back to DB

Reference implementation: `narrative_service/src/api/routes.py` lines 105-240.

## Middleware Stack

### Auth Middleware

License token validation, following the same pattern as other services:

- Validates `Authorization: Bearer <token>` header
- Extracts `tenant_id` into `request.state.tenant_id`
- Exempts health endpoints: `/health`, `/health/ready`, `/health/live`
- Configurable via `AuthSettings` (env prefix `AUTH_`)

### CORS

Standard CORS middleware via `APISettings.cors_*` fields.

### Request ID

Propagate or generate `X-Request-ID` header for tracing.

## Observability

- **Structured logging** via `logging_config.py` (JSON logs in production, human-readable in dev)
- **Job ID context var** (`job_id_var`) for correlating log lines within a background task
- **Request ID** propagation via middleware
- **Health endpoints** check DB connectivity, Redis connectivity, and storage availability

## Configuration

Pydantic Settings hierarchy, following `narrative_service/src/config.py` exactly:

```python
class Settings(BaseSettings):
    app_name: str = "Slide Service"
    app_version: str = "0.1.0"
    environment: str = "development"

    database: DatabaseSettings     # DATABASE_ prefix, url default: postgresql+asyncpg://postgres:postgres@localhost:5462/slide_service
    auth: AuthSettings             # AUTH_ prefix
    redis: RedisSettings           # REDIS_ prefix, url default: redis://localhost:6396/1
    api: APISettings               # API_ prefix, port default: 6312
    storage: StorageSettings       # STORAGE_ prefix, bucket: slide-service
    logging: LoggingSettings       # LOG_ prefix
    llm: LLMSettings               # LLM_ prefix
    presentation: PresentationSettings  # PRESENTATION_ prefix (see below)
    cache: CacheSettings           # CACHE_ prefix
```

### PresentationSettings (service-specific)

| Var | Default | Description |
|-----|---------|-------------|
| `PRESENTATION_DEFAULT_THEME` | `modern` | Default deck theme |
| `PRESENTATION_DEFAULT_ASPECT_RATIO` | `16:9` | Default aspect ratio |
| `PRESENTATION_MAX_SLIDES_PER_DECK` | `50` | Hard limit on slides |
| `PRESENTATION_NOTES_ENABLED` | `true` | Include speaker notes |

## Storage

- **PostgreSQL** for job metadata and template definitions
- **Redis** for async job state, job locks, and result caching (fail-open)
- **Local filesystem / MinIO** for `.pptx` files, manifests, and exported assets

Storage backend is selected via `STORAGE_BACKEND` (`local` or `minio`). MinIO bucket: `slide-service`.

File layout within storage:

```
decks/{job_id}/deck.pptx
decks/{job_id}/manifest.json
decks/{job_id}/exports/       # Phase 2: PNG/PDF
```

## Service Directory Structure

```
slide_service/
  src/
    __init__.py
    asgi.py                          # FastAPI app + lifespan + service init
    config.py                        # Pydantic Settings hierarchy
    llm_credentials.py               # LLM client factory
    logging_config.py                # Structured logging + job_id context var
    api/
      routes.py                      # All API route handlers
      schemas.py                     # Pydantic request/response models
      health.py                      # Health check endpoints
    auth/
      middleware.py                   # License token auth middleware
      models.py                      # Auth-related models
    cache/
      redis_client.py                # Redis client wrapper
      cache_keys.py                  # Cache key construction + TTLs
      job_lock.py                    # Redis job lock (acquire/release)
    database/
      connection.py                  # Engine + pool setup
      session.py                     # Session factory + DI dependency
      models.py                      # ORM models (slide_templates, template_versions, deck_jobs)
      migrations/                    # Alembic migrations
    services/
      deck_builder.py                # Orchestrates deck generation pipeline
      content_shaper.py              # LLM content polishing
      manifest_builder.py            # Generates manifest JSON
      design_helpers.py              # Refactored from pptx_design_helpers.py
      animation_manifest.py          # Refactored from animation_transition_helpers.py
      storage_service.py             # Local/MinIO file storage
      template_renderer.py           # Dispatches to per-template renderers
      templates/
        base.py                      # Base template renderer class
        title.py
        agenda.py
        scorecard.py
        finding_detail.py
        comparison.py
        feature_spotlight.py
        process_flow.py
        metric_summary.py
        roadmap.py
        cta.py
        tutorial_step.py
    seed/
      seed_templates.py              # Seeds 11 system templates on startup
    templates/
      content_shaper/
        deck_shaping.md              # Jinja2 LLM prompt template
  tests/
    conftest.py
    unit/
    integration/
  examples/                          # Moved from root
    pptx_creator.py
    enhanced_pptx_example.py
    enhanced_animated_example.py
    run_examples.py
  pyproject.toml
  Dockerfile
  docker-compose.yml
  CLAUDE.md
```

## Existing Code Disposition

| Current File | Action | Destination |
|-------------|--------|-------------|
| `pptx_design_helpers.py` (1031 lines) | Refactor + move | `src/services/design_helpers.py` |
| `animation_transition_helpers.py` (461 lines) | Refactor + move | `src/services/animation_manifest.py` |
| `pptx_creator.py` | Move to examples | `examples/pptx_creator.py` |
| `enhanced_pptx_example.py` | Move to examples | `examples/enhanced_pptx_example.py` |
| `enhanced_animated_example.py` | Move to examples | `examples/enhanced_animated_example.py` |
| `run_examples.py` | Move to examples | `examples/run_examples.py` |
| `test_pptx_creator.py` | Move to tests | `tests/unit/test_pptx_creator.py` |
| `test_pptx_design_helpers.py` | Move to tests | `tests/unit/test_pptx_design_helpers.py` |
| `docx_creator.py` | Remove | Out of scope for slide service |
| `explore_shapes.py` | Remove | Development utility, not needed |
| `output_slides/` | Keep as test fixtures | `examples/output_slides/` or gitignore |
| `README.md` | Replace | Superseded by `CLAUDE.md` and this doc |
| `requirements.txt` | Replace | Superseded by `pyproject.toml` |

## Deck Generation Pipeline

```
POST /api/decks
  |
  v
[Validate request + resolve template_ids]
  |
  v
[Create deck_jobs row (status=processing)]
  |
  v
[Return 202 with job_id]
  |
  v  (background task)
[Acquire Redis job lock]
  |
  v
[Freeze template versions (snapshot if not already frozen)]
  |
  v
[Content shaping (if enabled)]
  |  - Summarize content to slot max_length
  |  - Generate headlines, speaker notes, transitions
  |
  v
[Render slides via template_renderer]
  |  - Dispatch each slide to its template's Python renderer
  |  - Apply brand colors, fonts, theme
  |  - Attach notes, timing hints, transition metadata
  |
  v
[Build manifest JSON]
  |
  v
[Write .pptx + manifest to storage]
  |
  v
[Update deck_jobs row (status=completed, manifest, pptx_storage_path)]
  |
  v
[Write to Redis cache (fail-open)]
  |
  v
[Release job lock]
```

## Authoring vs Rendering

### Authoring (MVP)

Handled by `python-pptx`:
- Deck structure and slide layouts
- Text and image placement in template slots
- Speaker notes
- Metadata (title, author, timestamps)

### Rendering (Phase 2)

Handled separately:
- Export `.pptx` to PDF or slide images via headless LibreOffice
- Video-ready asset generation

Animation and transitions are **metadata only** in MVP -- stored in the manifest for downstream consumers to interpret. `python-pptx` does not support native PowerPoint animation playback.

### PNG render resolution (2K / 4K)

Slide PNGs render at a configurable pixel scale so they match the resolution
tier of the video they go into (sharp at the source, not upscaled). The scale is
applied as the Chromium **`device_scale_factor`** at capture, which keeps the
1920×1080 CSS layout identical and multiplies the output pixels:

- Service default: `RENDER_DEVICE_SCALE_FACTOR` (config `device_scale_factor`,
  default **`2.0` = 3840×2160 / 4K**).
- Per-request override: `render_scale` on the deck/render request
  (`1.0` → 1080p, `1.333` → 2K/2560×1440, `2.0` → 4K/3840×2160). An explicit
  value always wins over the service default.

The default is **2.0, not 1.0**, and that is a delivered-quality decision. The
video compiler's ken_burns stage zooms *into* the slide PNG, so a 1.0 render meant
it cropped a sub-region of a 1920×1080 bitmap and upscaled it back to a 1080p
video frame — every zoomed slide shipped soft and effectively below 1080p. At 2.0
the zoom has 4× the pixel area to crop from and the delivered frame stays true
1080p across the whole zoom range. The identical defect was fixed the same way in
the sibling `html_render_service`. Cost is bounded because the capture is a
fixed-viewport `full_page=False` shot: peak bitmap 3840×2160×4B ≈ 33MB vs ≈8MB
(≈+25MB per concurrent render, ≈+50MB across the default 2-slot pool, against a
4Gi pod limit), PNG bytes ≈2.2× (58KB → 130KB measured), wall-clock flat. `1.333`
was rejected as the alternative because it rounds to an **odd** width
(2559×1440), which is hostile to the yuv420p encoders downstream.

The w2v_orchestrator passes `render_scale` from the run's resolution tier
(`quality_tier` → `tier_scale`) so slide frames line up with the motion clips and
the terminal compile canvas. Code: `src/services/html_slide_renderer.py`
(`device_scale_factor` on capture), `src/api/schemas.py` (`render_scale`),
`src/config.py` (`RENDER_DEVICE_SCALE_FACTOR`).

## Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install uv && uv sync --frozen
COPY src/ src/
EXPOSE 6312
CMD ["uvicorn", "src.asgi:app", "--host", "0.0.0.0", "--port", "6312"]
```

## CI/CD

Follows the standard build flow from `CLAUDE.md`:

1. **Pre-commit:** ruff format, ruff check, security scan
2. **Pre-push:** pytest, ty
3. **GitHub Actions:** Build on self-hosted runners, push to `air.local:5000` with `:sha-<commit>` tag
4. **Smoke tests:** In-cluster tests in `test-<sha>` namespace
5. **Promote:** Tag `:latest` on success, Flux auto-deploys

## Implementation Sequence

### Phase 1: Service Shell
1. Create `pyproject.toml` with dependencies (`fastapi`, `uvicorn`, `python-pptx`, `sqlalchemy[asyncio]`, `asyncpg`, `redis`, `pydantic-settings`, `openai`, `jinja2`, `uuid-v7`)
2. Create `src/config.py` (copy pattern from `narrative_service/src/config.py`)
3. Create `src/asgi.py` with lifespan, DB init, Redis init
4. Create `src/api/health.py`
5. Create `src/database/models.py` (3 tables)
6. Create `src/database/session.py` and `src/database/connection.py`
7. Create Alembic migration for initial schema
8. Create `Dockerfile` and `docker-compose.yml`

### Phase 2: Templates
1. Create `src/services/templates/base.py` (base renderer class)
2. Move and refactor `pptx_design_helpers.py` -> `src/services/design_helpers.py`
3. Move and refactor `animation_transition_helpers.py` -> `src/services/animation_manifest.py`
4. Implement seed templates and `src/seed/seed_templates.py`
5. Create template CRUD routes + schemas
6. Implement `src/services/template_renderer.py`

### Phase 3: Deck Generation
1. Create `src/services/deck_builder.py` (orchestrator)
2. Create `src/services/manifest_builder.py`
3. Create `src/services/storage_service.py`
4. Create `src/cache/job_lock.py`, `redis_client.py`, `cache_keys.py`
5. Implement deck generation routes (POST + GET + download)
6. Implement background task with Redis lock pattern

### Phase 4: Content Shaping
1. Create `src/services/content_shaper.py`
2. Create `src/templates/content_shaper/deck_shaping.md`
3. Create `src/llm_credentials.py`
4. Wire content shaping into deck generation pipeline

### Phase 5: Auth + Observability
1. Create `src/auth/middleware.py`
2. Create `src/logging_config.py`
3. Wire middleware into `src/asgi.py`

### Phase 6: Tests + CI
1. Move existing test files to `tests/unit/`
2. Write unit tests for template CRUD, deck builder, content shaper, manifest builder
3. Write integration tests for async job flow
4. Set up pre-commit hooks, CI workflow
