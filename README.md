# Slide Service

Generates branded presentation decks — PPTX, PDF, per-slide PNG previews, and a consumer-agnostic JSON manifest — from structured slide requests. Port 6312.

## What Slide Service Does

When an orchestrator or application needs a presentation, it POSTs a deck request describing a title, a theme, and a list of slides — each slide naming a template (title, agenda, scorecard, comparison, roadmap, and so on) plus the content to drop into that template's slots. The request is accepted immediately with a `202` and a job ID; the actual generation runs in a detached background task so the caller never blocks on a multi-second render. A row is written to PostgreSQL in the `processing` state, a Redis job lock is taken so duplicate pods can't render the same job twice, and the caller polls the job ID until it reports `completed` or `failed`.

Generation walks a pipeline. The DeckBuilder first resolves each slide's template and freezes an immutable version snapshot so the deck stays reproducible even if the template is later edited. Content can optionally be polished by an LLM: the ContentShaper routes a shaping prompt through `worker_management` (the sole LLM gateway for w2v services) using the `slide-bullet-extractor` worker, tightening bullets and headings before layout. The PPTX is assembled with python-pptx and branded styling, and — this is the distinctive part — each slide's visual preview is produced by rendering HTML/CSS through a pool of long-lived headless Chromium browsers, not by drawing with PIL. The HTML uses `clamp()`, `text-wrap: balance`, and `overflow-wrap: anywhere` so long titles and URLs wrap by construction rather than truncating, and every slide is screenshotted at a fixed 1920×1080 viewport with `full_page=False` to avoid the tall-page bitmap OOM that plagues naive HTML rasterisers.

What comes out is a set of artifacts persisted to storage (local disk or MinIO): the `.pptx` deck, a `.pdf` conversion, one PNG per slide, and a JSON manifest that indexes every asset with its storage path. Consumers download these through dedicated endpoints — the manifest lets a video compiler, a frontend, or any downstream stage locate exactly the asset it needs without knowing the internal storage layout. The service is deliberately consumer-agnostic: it holds no product-specific logic and can be driven by any caller.

The service is stateful, backed by PostgreSQL 18 over psycopg 3 (through PgBouncer in-cluster) with Redis for job-status caching and cross-pod job locks. Redis is treated as fail-open — every cache path is wrapped so a Redis outage degrades to a slower DB-only path rather than an error. The Chromium browser pool is started eagerly in the ASGI lifespan so the first deck render doesn't pay a cold-start cost, and each pooled browser is relaunched after a bounded number of leases to cap the memory ratchet. Output resolution is tunable from 1× (1920×1080) up to 4× (3840×2160) by raising the Chromium device pixel ratio while keeping the identical CSS layout, so previews sharpen without re-wrapping.

## Features

- **Async deck generation** — accepts a deck request with `202` and renders in a background task so callers poll a job ID instead of holding a long HTTP connection open through a multi-second PPTX/PDF/PNG render
- **HTML/Chromium slide previews** — rasterises per-slide PNGs from HTML/CSS through a headless Chromium pool (not PIL) so CSS wrapping rules keep long titles and URLs on-slide by construction and previews match real typographic layout
- **Bounded browser pool** — a fixed-size pool of long-lived Chromium browsers, each relaunched after a lease budget, screenshots at a fixed 1920×1080 viewport with `full_page=False` so memory stays bounded and tall-page OOMs are impossible
- **2K/4K output scaling** — a device-pixel-ratio knob (1.0–4.0) raises PNG resolution up to 3840×2160 while keeping the identical CSS layout, so decks can target high-DPI displays without re-flowing content
- **Template library with versioning** — 11 seeded system templates plus tenant-owned custom templates, with automatic immutable version snapshots on edit so a deck rendered against a template stays reproducible
- **Multi-format artifacts** — every job yields a `.pptx`, a `.pdf`, per-slide PNGs, and a JSON manifest so downstream stages can consume whichever representation they need
- **Consumer-agnostic manifest** — a JSON index of every generated asset with its storage path so any caller (video compiler, frontend) can locate artifacts without knowing the storage layout
- **Optional LLM content shaping** — per-request bullet/heading polishing routed through `worker_management` so content quality improves without pulling an LLM dependency into this service
- **Redis job locks** — a per-job distributed lock so multiple pods never render the same deck twice
- **Fail-open caching** — job-status reads and writes go through Redis wrapped in try/except so a Redis outage degrades to a DB-only path instead of failing requests
- **Tenant isolation + RLS** — templates and jobs are scoped to the caller's tenant (own-or-`system` for templates); Phase D Postgres row-level-security policies enforce isolation at the database layer when the app connects as the restricted role

## Authentication

Auth enabled by default (`AUTH_ENABLED=true`). Inbound requests carry a license token via `X-Tenant-Id: Bearer <license_token>` — **not** the `Authorization` header, which is reserved for the SPA's Keycloak JWT. The `AuthMiddleware` (from `w2v_common`) introspects the token against the licenses service and caches the result for ~5 minutes (`AUTH_CACHE_TTL=300`). Requests originating from a trusted CIDR are assigned the default tenant without a token.

| Priority | Method | Details |
|----------|--------|---------|
| 1 | License token | `X-Tenant-Id: Bearer <token>` introspected against the licenses service, cached ~5 min |
| 2 | Trusted network | Bypass → default tenant for private CIDRs (`127.0.0.1/32`, `::1/128`, `172.16.0.0/12`, `10.0.0.0/8`) |

Exempt (public) paths from the auth middleware: `/`, `/health`, `/health/live`, `/health/ready`, `/healthz`, `/readyz`, `/ready`, `/docs`, `/redoc`, `/openapi.json`, `/favicon.ico`, `/metrics`, plus anything under the `/docs`, `/redoc`, `/health/`, and `/api/health/` prefixes.

Outbound service-to-service calls (e.g. to `worker_management` for LLM shaping) forward `X-Tenant-Id`, `X-License-Id`, and `X-Subject-Id` as plain headers with no `Bearer` prefix. Phase D RLS policies (`src/database/rls_policies.py`) filter `slide_templates` to own/system and join `template_versions`/`deck_jobs` through it; they activate when the runtime connects as the restricted `w2v_slide_app` role with `TENANT_RLS_MODE=enforce`.

## Quick Start

```bash
uv sync
uv run uvicorn src.asgi:app --host 0.0.0.0 --port 6312
uv run pytest tests/ -v --cov=src
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
```

## API Endpoints

### Templates `/api/templates`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/templates` | List active templates (own + system), paginated with category/layout filters |
| POST | `/templates` | Create a custom template |
| GET | `/templates/{template_id}` | Get a single template |
| PUT | `/templates/{template_id}` | Update a template (auto-versions if it has been used) |
| DELETE | `/templates/{template_id}` | Soft-delete a template |
| GET | `/templates/{template_id}/versions` | List frozen versions of a template |

### Decks `/api/decks`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/decks` | Start a deck generation job (returns `202`) |
| GET | `/decks/{job_id}` | Poll deck job status |
| GET | `/decks/{job_id}/pptx` | Download the generated `.pptx` |
| GET | `/decks/{job_id}/pdf` | Download the generated `.pdf` |
| GET | `/decks/{job_id}/slides/{slide_index}.png` | Download a slide preview PNG |
| GET | `/decks/{job_id}/manifest` | Download the deck manifest JSON |

### Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Service info and endpoint listing |
| GET | `/health` | Combined health (database + Redis) |
| GET | `/health/live` | Liveness probe |
| GET | `/health/ready` | Readiness probe (checks DB) |
| GET | `/healthz` | Liveness alias |
| GET | `/readyz` | Readiness alias |

## Configuration

All configuration via environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `API_HOST` | `0.0.0.0` | Listen host |
| `API_PORT` | `8080` | Listen port — the w2v_common base default; the deployment (Dockerfile `EXPOSE` + gitops env) sets `6312` |
| `API_DEBUG` | `false` | Enable `/docs` and `/redoc` debug behaviour |
| `AUTH_ENABLED` | `true` | Toggle license-token auth middleware |
| `AUTH_DEFAULT_TENANT_ID` | `<unattributed UUID>` | Tenant assigned to trusted-network requests |
| `AUTH_CACHE_TTL` | `300` | License introspection cache TTL (seconds) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_JSON_LOGS` | `false` | Emit structured JSON logs |
| `DATABASE_URL` | `postgresql+psycopg://localhost:5432/postgres` | PostgreSQL connection string (psycopg 3) |
| `DATABASE_POOL_SIZE` | `10` | SQLAlchemy connection pool size |
| `DATABASE_POOL_MIN_SIZE` | `2` | Minimum connections in pool |
| `DATABASE_MAX_OVERFLOW` | `20` | Max connections beyond pool size |
| `REDIS_URL` | `redis://localhost:6396/1` | Redis connection URL (job status + locks) |
| `CACHE_ENABLED` | `true` | Toggle Redis result caching |
| `STORAGE_BACKEND` | `local` | Artifact storage backend (`local` or `minio`) |
| `STORAGE_LOCAL_PATH` | `/app/data` | Local storage root when backend is `local` |
| `STORAGE_MINIO_BUCKET` | `w2v-data` | MinIO bucket for artifacts (deployment sets `slide-service`) |
| `RENDER_MAX_CONCURRENCY` | `2` | Chromium pool size = max concurrent renders |
| `RENDER_BROWSER_MAX_RENDERS` | `200` | Leases before a pooled Chromium is relaunched |
| `RENDER_PAGE_TIMEOUT_MS` | `15000` | Per-slide page load + render timeout (ms) |
| `RENDER_DEVICE_SCALE_FACTOR` | `1.0` | Chromium device pixel ratio (1.0–4.0); 1.0 = 1920×1080, 2.0 = 3840×2160 |
| `PRESENTATION_DEFAULT_THEME` | `modern` | Default deck theme |
| `PRESENTATION_MAX_SLIDES_PER_DECK` | `50` | Hard limit on slides per deck |
| `WORKER_MANAGEMENT_URL` | `worker-management.workers:8887` | LLM gateway for content shaping |

## Architecture

```
src/
├── asgi.py                          # FastAPI app, lifespan, browser-pool start/stop, health root, OpenAPI
├── config.py                        # Pydantic Settings (Database, Auth, Redis, API, Storage, Logging, Presentation, Cache, Render)
├── logging_config.py                # Structured logging + job_id context var
├── api/
│   ├── routes.py                    # Template CRUD/versioning + async deck generation endpoints
│   ├── health.py                    # Health / liveness / readiness probes
│   └── schemas.py                   # Request/response Pydantic models
├── auth/
│   ├── middleware.py                # License-token AuthMiddleware (re-exported from w2v_common)
│   └── models.py                    # Auth data models
├── cache/
│   ├── redis_client.py              # Redis client wrapper (init/close)
│   ├── cache_keys.py                # Cache key construction + TTLs
│   └── job_lock.py                  # Redis job lock (acquire/release)
├── database/
│   ├── connection.py                # Async engine lifecycle
│   ├── session.py                   # DB session + dependency injection
│   ├── models.py                    # SlideTemplate, TemplateVersion, DeckJob ORM models
│   ├── rls_policies.py              # Phase D row-level-security policy DDL
│   └── migrations/versions/         # Alembic migrations (initial, prod-align, RLS)
├── middleware/
│   └── request_id.py                # Request-ID binding middleware (outermost)
├── seed/
│   └── seed_templates.py            # Seeds 11 system templates on startup
└── services/
    ├── deck_builder.py              # Orchestrates the deck generation pipeline
    ├── content_shaper.py            # Optional LLM content polishing via worker_management
    ├── manifest_builder.py          # Builds the consumer-agnostic JSON manifest
    ├── design_helpers.py            # PPTX styling, colors, fonts
    ├── animation_manifest.py        # Transition/animation metadata
    ├── html_slide_renderer.py       # build_slide_html (pure) + render_slide_png (Chromium)
    ├── browser_pool.py              # Bounded pool of long-lived Chromium (fixed 1920×1080)
    ├── pdf_converter.py             # Deck → PDF
    ├── image_layout.py              # Image placement/layout helpers
    ├── image_resolver.py            # Resolves image references for rendering
    ├── storage_service.py           # Local/MinIO artifact storage
    ├── template_renderer.py         # Dispatches to per-template renderers
    └── templates/                   # 11 template renderers (title, agenda, scorecard,
                                     #   finding_detail, comparison, feature_spotlight,
                                     #   process_flow, metric_summary, roadmap, cta, tutorial_step)
```

## Template Renderers

11 seeded system templates, each with a dedicated renderer in `src/services/templates/`.

| Template | Use Case |
|----------|----------|
| `title` | Opening slide |
| `agenda` | Outline / table of contents |
| `scorecard` | Performance scores |
| `finding_detail` | Individual finding with severity |
| `comparison` | Before/after side-by-side |
| `feature_spotlight` | Feature highlight |
| `process_flow` | Workflow steps |
| `metric_summary` | KPI dashboard |
| `roadmap` | Phased plan timeline |
| `cta` | Call to action / next steps |
| `tutorial_step` | Tutorial step-by-step |

## Testing

```bash
uv run pytest tests/ -v --cov=src          # All tests with coverage
uv run pytest tests/unit/ -v               # Unit tests
uv run pytest tests/integration/ -v        # Integration tests
uv run ruff check src/ tests/              # Lint
uv run ruff format --check src/ tests/     # Format check
```

Coverage threshold: 80% (enforced in pyproject.toml). Unit tests cover the deck builder, HTML slide renderer, template renderers, manifest builder, PDF converter, routes, and tenant isolation; top-level tests cover OpenAPI, error-envelope, RLS isolation, and auth-middleware contracts.

## CI/CD

`.github/workflows/ci-public.yml` is self-contained and runs on GitHub-hosted runners: a betterleaks secret scan, the `no-private-refs` guard, then `ruff check`, `ruff format --check`, `pytest` against a Postgres 18 service container, and `scripts/pip-audit.sh`. No image is published from this repository.


Per-PR the archetype runs `ruff + ty + pytest + bandit + pip-audit + detect-secrets`, builds an amd64 image, deploys it to a per-commit `test-<sha>` namespace, and runs `kubectl rollout status` to confirm the pod reaches Ready. On merge to `main` the image is promoted `registry.test:5000/slide_service:sha-<commit>` → `:latest`, and Flux rolls it out to the `w2v` namespace (`imagepolicy: w2v-slide`, `deploy_name: slide-service`).

## Docker

```bash
docker build -t slide-service .
docker run -p 6312:6312 slide-service
docker run -p 6312:6312 -e LOG_LEVEL=DEBUG -e DATABASE_URL=postgresql+psycopg://host:5432/slide_service slide-service
```

## Dependencies

- **worker_management** (port 8887) — sole LLM gateway; ContentShaper routes bullet/heading polishing through it
- **licenses service** — validates inbound license tokens via introspection (through the shared auth middleware)
- **PostgreSQL 18** — persistent storage for templates, versions, and deck jobs via psycopg 3 (through PgBouncer in-cluster)
- **Redis** — job-status cache and cross-pod job locks
- **MinIO** — optional artifact storage backend (`STORAGE_BACKEND=minio`)
- **FastAPI** — web framework
- **Uvicorn** — ASGI server
- **Pydantic** / **pydantic-settings** — schema and settings validation
- **SQLAlchemy** (async) + **Alembic** — ORM and migrations
- **psycopg 3** — PostgreSQL driver
- **Playwright** — headless Chromium for HTML slide-preview rendering
- **python-pptx** — PowerPoint deck assembly
- **Jinja2** — LLM prompt and HTML templating
- **lxml** — XML processing for PPTX internals
- **miniopy-async** — async MinIO client
- **httpx** — async HTTP client for service-to-service calls
- **w2v-common** — shared config, auth middleware, clients, and error envelope
```

## License

Apache 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

## Licensing

This service contains no licence enforcement of its own and runs no licence
server. Tenant and licence identity is forwarded from the caller. It runs with
no licence switch set; unset means off.
