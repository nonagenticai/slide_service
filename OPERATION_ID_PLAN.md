# Operation ID Plan: slide_service

Canonical registry of `operation_id` values for every route in
`slide_service`. These IDs feed the org-wide n8n custom-node
generator and the `ultrareview` cross-service catalogue, so they must be:

- **stable** — never rename without coordinating downstream,
- **`verb_noun` snake_case** — matches the org standard (nlp_service,
  worker_management, kag, analysis_service, video_compiler_service,
  video_analyzer_service),
- **globally unique** — disambiguated against the rest of the fleet's verbs.

This service hosts two related domains:

- `templates` — slide-template CRUD + versioning,
- `decks` — async deck generation jobs + artifact downloads.

Generic verbs (`list_*`, `get_*`, `create_*`, `update_*`, `delete_*`,
`download_*`) are prefixed with the resource (`slide_template_*`,
`deck_*`) wherever they would collide across the fleet.

## Files to edit

- `src/asgi.py` (root endpoint)
- `src/api/health.py`
- `src/api/routes.py`

## Mapping Tables

### `src/asgi.py` (tag: `health`)

| Method | Function | operation_id   | Rationale                                  |
|--------|----------|----------------|--------------------------------------------|
| GET    | `root`   | `service_root` | Service banner; matches the fleet standard. |

### `src/api/health.py` (tag: `health`)

| Method | Function       | operation_id   | Rationale                                                  |
|--------|----------------|----------------|------------------------------------------------------------|
| GET    | `health_check` | `health_check` | Primary health probe; matches the fleet standard verb.     |
| GET    | `health_live`  | `health_live`  | Liveness sub-probe; one-to-one with path.                  |
| GET    | `health_ready` | `health_ready` | Readiness sub-probe; one-to-one with path.                 |

### `src/api/routes.py` — templates (tag: `slides`)

| Method | Function                  | operation_id                      | Rationale                                                                                  |
|--------|---------------------------|-----------------------------------|--------------------------------------------------------------------------------------------|
| GET    | `list_templates`          | `list_slide_templates`            | Generic `list_templates` would collide with template surfaces on other services; prefix.    |
| GET    | `get_template`            | `get_slide_template`              | Domain-prefix; `get_template` is too generic at fleet scope.                                |
| POST   | `create_template`         | `create_slide_template`           | Domain-prefix; `create_template` collides with other template-owning services.              |
| PUT    | `update_template`         | `update_slide_template`           | Domain-prefix; `update_template` collides.                                                  |
| DELETE | `delete_template`         | `delete_slide_template`           | Domain-prefix; `delete_template` collides.                                                  |
| GET    | `list_template_versions`  | `list_slide_template_versions`    | Domain-prefix; the version surface is template-scoped.                                      |

### `src/api/routes.py` — decks (tag: `slides`)

| Method | Function                | operation_id                   | Rationale                                                                                  |
|--------|-------------------------|--------------------------------|--------------------------------------------------------------------------------------------|
| POST   | `create_deck`           | `start_deck_generation`        | `create_deck` leaks the implementation; the verb is "start a generation job" — async, 202. |
| GET    | `get_deck_status`       | `get_deck_status`              | Already domain-scoped (`deck_`); no fleet collision on `deck_*`.                            |
| GET    | `download_pptx`         | `download_deck_pptx`           | Generic `download_pptx` would collide with any other PPTX-emitting service; prefix.         |
| GET    | `download_pdf`          | `download_deck_pdf`            | Generic `download_pdf` would collide with PDF emitters fleet-wide; prefix.                  |
| GET    | `download_slide_preview`| `download_deck_slide_preview`  | Generic `download_slide_preview` could collide; prefix with the resource.                   |
| GET    | `get_manifest`          | `get_deck_manifest`            | Generic `get_manifest` is far too generic at fleet scope; prefix with the resource.         |

## Verification

```bash
cd slide_service
uv run pytest tests/test_openapi.py -x
```

`tests/test_openapi.py` loads `app.openapi()` and asserts that the set of
`operationId` values equals the union of every right-hand column above.
Drift (rename, missed decorator, fallback) fails the test loudly.

## Disambiguation rationale

Concrete renames where the function name was generic enough to collide
across the fleet:

- All `*_template[s]` operations → `*_slide_template[s]` — slide-templates
  share verb-space with template surfaces on kag, worker_management, and the
  narrative/script services. Prefixing with `slide_` keeps them out of the
  generic namespace.
- `create_deck` → `start_deck_generation` — source name leaks
  implementation; the op_id describes the operation in the org's standard
  verb_noun shape (async job, returns 202).
- All artifact downloads (`download_pptx`, `download_pdf`,
  `download_slide_preview`) and `get_manifest` → domain-prefixed with
  `deck_` — generic `download_*` / `get_manifest` collide across the fleet.

## Future routes

When adding a route in this service, **always** set `operation_id="…"`
explicitly. The `generate_unique_id_function` configured in `asgi.py` is a
safety net for forgotten kwargs, never the primary identifier. New IDs go
in the table above as part of the same PR (no out-of-band updates).
