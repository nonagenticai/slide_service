"""The one definition of a template's snapshot hash.

``template_versions`` freezes what a deck was rendered against, keyed by a
sha256 over the four fields that decide how a template draws. TWO call sites
need that hash and they MUST agree byte for byte:

* :meth:`src.services.deck_builder.DeckBuilder._freeze_versions` — the request
  path, which looks for an existing row with this hash and inserts one when it
  finds none.
* :func:`src.seed.seed_templates.freeze_system_template_versions` — the startup
  path, which pre-freezes every platform template so the request path always
  finds a row and never has to insert one.

If the two ever computed the hash differently, the startup path would freeze a
snapshot the request path does not recognise, the request path would try to
INSERT, and the ``tenant_isolation_ins`` policy on ``template_versions`` would
refuse it (a platform template is not owned by the calling tenant) — which is
exactly the outage this module exists to make unrepeatable. So the computation
lives here once and both callers import it; neither may re-implement it.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.database.models import SlideTemplate, TemplateVersion


def template_snapshot_hash(
    *,
    slot_definitions: object,
    styling_rules: object,
    default_transition: object,
    default_timing_hint_seconds: object,
) -> str:
    """sha256 over the four fields a frozen version records.

    ``sort_keys=True`` makes the digest independent of dict ordering, so a value
    that has round-tripped through JSONB hashes the same as the literal it was
    written from.
    """
    snapshot_data = json.dumps(
        {
            "slot_definitions": slot_definitions,
            "styling_rules": styling_rules,
            "default_transition": default_transition,
            "default_timing_hint_seconds": default_timing_hint_seconds,
        },
        sort_keys=True,
    )
    return hashlib.sha256(snapshot_data.encode()).hexdigest()


def snapshot_hash_for(template: SlideTemplate | TemplateVersion) -> str:
    """:func:`template_snapshot_hash` over a persisted row's current values."""
    return template_snapshot_hash(
        slot_definitions=template.slot_definitions,
        styling_rules=template.styling_rules,
        default_transition=template.default_transition,
        default_timing_hint_seconds=template.default_timing_hint_seconds,
    )
