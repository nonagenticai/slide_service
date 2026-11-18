"""Give ``template_versions.template_id`` a real foreign key to ``slide_templates``.

``template_versions`` is the frozen-snapshot table: every row is supposed to be a
point-in-time copy of one ``slide_templates`` row, reachable by ``template_id``.
That relationship has been enforced by nothing since the initial schema — the
column was declared ``String(36)`` with a plain non-unique index and no
``REFERENCES``, so a version could name a template that does not exist, and a
template could be deleted out from under its own version history without a single
error. A ``git grep`` for ``ForeignKey`` or ``REFERENCES`` under
``src/database/`` returned **zero**
hits repo-wide before this revision.

That is the integrity hole under the 2026-09-07 promo incident, where the
relationship between a template and its frozen version was exactly what went
wrong. An FK would not by itself have prevented that failure, but it is the
constraint that makes the class of "a version pointing at nothing" impossible
rather than merely unobserved.

**Pre-flight, measured against the live database on 2026-09-09 before writing
this** — all three facts an FK needs were checked, not assumed:

1. **Orphans: 0.** ``select count(*) from template_versions tv
   left join slide_templates st on st.template_id = tv.template_id
   where st.template_id is null`` → ``0`` across 17 version rows / 16 templates.
   So this migration needs no cleanup step and cannot fail on existing data.
2. **The target is unique.** ``slide_templates.template_id`` is covered by
   ``ix_slide_templates_template_id``, a UNIQUE btree index (16 rows, 16 distinct
   values). Postgres accepts a unique *index* as an FK target, so no new
   constraint is required first — which matters, because adding one would have
   rewritten the table.
3. **The types match exactly.** Both sides are ``character varying(36)``. A type
   mismatch is the usual reason an FK addition fails in an init container.

``ON DELETE RESTRICT`` is deliberate and is the whole point: deleting a template
that still has frozen versions must be refused, because those versions are the
reproducibility record for decks already rendered from it. ``ON UPDATE CASCADE``
costs nothing — ``template_id`` is a generated identifier that is never edited in
place, so the clause is inert today and correct if that ever changes.

``NOT VALID`` is deliberately NOT used. It exists to skip the full-table scan on a
large table, and with 17 rows there is nothing to skip; taking the validation now
means the constraint is trustworthy the moment it lands rather than being a
promise someone has to remember to redeem.

Revision ID: 0006_template_versions_fk
Revises: 0005_tenant_id_uuid
Create Date: 2026-09-09 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_template_versions_fk"
down_revision: str | None = "0005_tenant_id_uuid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Named explicitly rather than left to alembic's naming convention: the downgrade
#: has to drop it by name, and an auto-generated name that differs between
#: environments makes the downgrade environment-dependent.
FK_NAME = "fk_template_versions_template_id"


def upgrade() -> None:
    op.create_foreign_key(
        FK_NAME,
        source_table="template_versions",
        referent_table="slide_templates",
        local_cols=["template_id"],
        remote_cols=["template_id"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(FK_NAME, "template_versions", type_="foreignkey")
