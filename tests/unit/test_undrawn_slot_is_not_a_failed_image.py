"""An UNDRAWN image slot is a template defect, not a failed image load.

``_unrenderable_slot_report`` counts slots that no renderer in the layout family
draws. It used to count each one ``requested=1, failed=1``, which fed
``deck_builder._guard_image_slots`` a fact that was never established:
``load_image_bytes`` is not called on that path, so nothing is known about
whether the ref loads. The guard then hard-failed decks whose imagery was fine.

Two facts must stay distinguishable, and every test here asserts the
DISTINCTION rather than any call count:

* ``failed``  — the image was fetched and produced no bytes ("your URL is dead");
* ``undrawn`` — nothing in the family draws this slot ("the template is wrong").
"""

from __future__ import annotations

import pytest

from src.services.deck_builder import DeckBuilder
from src.services.html_slide_renderer import (
    SlideImageReport,
    _unrenderable_slot_report,
)
from src.services.image_layout import ImageSlotKind, classify_image_slot

LIVE_LOGO = "https://minio.test:8389/w2v/logo.png"


def _guard(report: SlideImageReport) -> None:
    """Invoke the REAL deck guard on a real DeckBuilder.

    ``__new__`` skips ``__init__`` (which wants Settings and a storage client);
    the guard reads nothing off ``self``, so this exercises the shipped code
    path rather than a reimplementation of it.
    """
    DeckBuilder.__new__(DeckBuilder)._guard_image_slots("job-123", report)


def _deck_verdict(report: SlideImageReport) -> str:
    """Run the REAL guard and return its verdict AS A VALUE.

    ``_guard`` communicates "the deck ships" by not raising, which a test can
    only observe by asserting nothing at all — invisible to the vacuous-test
    detector and, worse, unable to tell "the deck shipped" from "the deck
    shipped carrying the wrong counters". This turns the two outcomes into two
    distinguishable strings so a test can assert the one it names.
    """
    try:
        _guard(report)
    except ValueError as exc:
        return f"vetoed: {exc}"
    return "ships"


def _manifest_image_counters(report: SlideImageReport) -> dict[str, int]:
    """The four ``image_slots_*`` fields ``DeckBuilder`` publishes on the manifest.

    Mirrors the assignments in ``deck_builder.build_deck`` (steps 7-8), so a
    test asserts what an OPERATOR will actually read back off a finished deck,
    not merely an internal dataclass.
    """
    return {
        "image_slots_requested": report.requested,
        "image_slots_failed": report.failed,
        "image_slots_unclassified": report.unclassified,
        "image_slots_undrawn": report.undrawn,
    }


def _report(family: str, content: dict[str, object]) -> SlideImageReport:
    return _unrenderable_slot_report(family, content)


# --------------------------------------------------------------------------
# Finding 1 — the seeded ``title`` template's own logo slot.
# --------------------------------------------------------------------------


def test_live_logo_on_title_slide_is_undrawn_not_failed():
    """The reported case, verbatim: a title slide carrying a live brand logo.

    Pre-fix this returned ``requested=1, failed=1``. The ref is a perfectly good
    https URL that was never even fetched, so calling it a failed load is a
    false claim: the fix must report it as undrawn and leave failed at zero.
    """
    content: dict[str, object] = {
        "title": "Q3 Review",
        "subtitle": "Ops",
        "logo": LIVE_LOGO,
    }

    report = _report("title", content)

    assert report.failed == 0, "an unfetched slot must never be reported as a failed load"
    assert report.requested == 0, "nothing was requested of the image loader"
    assert report.undrawn == 1
    assert report.undrawn_slots == ("logo",)
    # The loss stays expressible: it is NOT byte-identical to a text-only slide.
    assert report != SlideImageReport()


def test_live_logo_on_title_slide_no_longer_fails_the_deck():
    """The guard must not veto a deck over a slot nothing draws.

    Pre-fix: ``ValueError: Deck job-123: every requested image failed to load
    (1/1 slots)``. The deck's imagery was never missing.

    Asserted as the guard's VERDICT plus the manifest counters that verdict is
    read from, not as "no exception escaped": the absence of a raise cannot
    distinguish a deck that shipped for the right reason from one that shipped
    because the undrawn slot was quietly counted as nothing at all.
    """
    report = _report(
        "title",
        {"title": "Q3 Review", "subtitle": "Ops", "logo": LIVE_LOGO},
    )

    assert _manifest_image_counters(report) == {
        "image_slots_requested": 0,
        "image_slots_failed": 0,
        "image_slots_unclassified": 0,
        "image_slots_undrawn": 1,
    }
    assert report.undrawn_slots == ("logo",)
    assert _deck_verdict(report) == "ships"


def test_seeded_title_template_declares_no_slot_that_nothing_draws():
    """Every image slot the seeded ``title`` template declares must be drawable.

    Asserts the invariant rather than the absence of one key: if a future edit
    re-adds an image slot to a family that cannot render it, this fails.
    """
    from src.seed.seed_templates import SEED_TEMPLATES

    title = next(t for t in SEED_TEMPLATES if t["name"] == "title")
    slots = title["slot_definitions"]
    assert isinstance(slots, dict)

    declared_images = [
        name
        for name, spec in slots.items()
        if isinstance(spec, dict) and "image" in str(spec.get("type", ""))
    ]
    assert declared_images == [], (
        f"the title family draws no image slots, but the seed declares {declared_images}"
    )

    # And the same fact from the renderer's side: a sample value in any declared
    # slot must not come back undrawn.
    sample: dict[str, object] = {name: LIVE_LOGO for name in slots}
    assert _report("title", sample).undrawn == 0


# --------------------------------------------------------------------------
# Finding 2 — prose containing a slash is not an image reference.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("slot", "value"),
    [
        ("image_caption", "Before/After growth"),
        ("image_credit", "Photo: Unsplash / Jane"),
    ],
)
def test_prose_with_a_slash_is_not_an_image_reference(slot: str, value: str):
    """The reported cases, verbatim.

    Pre-fix each produced ``requested=1, failed=1`` (the key matched an image
    token and ``"/" in text`` gave the value reference shape), and a deck
    carrying only such a caption was vetoed outright.
    """
    assert classify_image_slot(slot, value) is ImageSlotKind.NOT_A_SLOT

    report = _report("bullets", {"heading": "Growth", slot: value})

    assert report.failed == 0
    assert report.requested == 0
    assert report.undrawn == 0, "a caption is not a slot at all, drawn or otherwise"
    assert report == SlideImageReport()


def test_a_deck_whose_only_image_ish_key_is_a_caption_is_not_vetoed():
    """The end-to-end consequence of the above: the deck ships.

    Pre-fix the caption acquired reference shape, was counted
    ``requested=1, failed=1``, and the all-failed guard vetoed the job.

    Both halves are asserted, because either alone is satisfiable by the wrong
    code: a deck with the caption counted as an UNDRAWN slot would also "not
    raise", while counters alone would not prove the guard reads them the way
    this test's name claims. The manifest must show a slide that asked for no
    imagery at all — a caption is prose, not a slot.
    """
    report = _report(
        "bullets",
        {"heading": "Growth", "image_caption": "Before/After growth"},
    )

    assert _manifest_image_counters(report) == {
        "image_slots_requested": 0,
        "image_slots_failed": 0,
        "image_slots_unclassified": 0,
        "image_slots_undrawn": 0,
    }
    assert _deck_verdict(report) == "ships"


@pytest.mark.parametrize(
    "value",
    [
        "https://minio.test:8389/w2v/hero.png",  # scheme
        "asset://hero-1",  # scheme
        "s3://bucket/decks/hero.jpg",  # scheme
        "decks/job 1/a.png",  # spaces AND slashes, but a real extension
        "w2v/hero.png?X-Amz-Signature=abc123",  # presigned: extension before the query
        "w2v/hero.png#frag",  # extension before the fragment
        "decks/job1/hero",  # extension-less object key, no whitespace
        "hero.jpeg",  # bare filename
        "C:\\decks\\hero.png",  # windows separator
    ],
)
def test_genuine_image_references_are_still_recognised(value: str):
    """The opposite error: tightening must not silently drop a real reference.

    A demoted reference would never render AND would escape the all-failed veto,
    which is strictly worse than the bug being fixed. Each shape here is one a
    real producer emits.
    """
    assert classify_image_slot("image_url", value) is ImageSlotKind.REFERENCE


# --------------------------------------------------------------------------
# Finding 2b — the VALUE-SHAPE rule, pinned INDEPENDENTLY of the name rule.
# --------------------------------------------------------------------------
#
# ``classify_image_slot`` claims two independent guards against prose being
# promoted to an image reference:
#
#   1. a NAME rule — a key ending in an ``IMAGE_DESCRIPTIVE_SUFFIXES`` entry
#      (``_caption``/``_credit``/``_alt``/...) is prose whatever its value is;
#   2. a VALUE-SHAPE rule — the separator test in
#      ``looks_like_image_reference`` requires a path-shaped value (a ``/`` or
#      ``\`` AND no interior whitespace), so a sentence with a slash in it has
#      no reference shape.
#
# The name rule runs FIRST and returns, so it SHORT-CIRCUITS the value sniffing
# entirely. Every test above ``test_prose_with_a_slash_is_not_an_image_reference``
# uses a ``_caption``/``_credit`` key, so all of them are satisfied by guard 1
# alone: restoring the pre-fix bare ``"/" in text`` rule reds NONE of them, and
# guard 2 could be loosened back to the shape that vetoed complete decks and the
# suite would still ship green.
#
# The tests below therefore use an image-ish key that ends in NO descriptive
# suffix, so classification can only be decided by the value. They are the only
# tests in the repo that go red when the separator rule loses its
# no-interior-whitespace clause.

_PROSE_SLOT = "image_blurb"  # contains "image", ends in no descriptive suffix


def test_the_prose_slot_used_below_is_not_rescued_by_the_name_rule():
    """Guard against this section going vacuous.

    If ``_blurb`` were ever added to ``IMAGE_DESCRIPTIVE_SUFFIXES``, the two
    tests below would pass via the NAME rule and stop testing value shape at
    all — silently, and exactly the way the ``_caption`` tests already do.
    """
    from src.services.image_layout import IMAGE_DESCRIPTIVE_SUFFIXES, IMAGE_SLOT_TOKENS

    assert any(token in _PROSE_SLOT for token in IMAGE_SLOT_TOKENS), (
        "the key must reach the value check at all"
    )
    assert not _PROSE_SLOT.endswith(IMAGE_DESCRIPTIVE_SUFFIXES), (
        "the key must NOT be short-circuited by the name rule"
    )
    # And the positive half: with a reference-shaped value the very same key IS
    # a reference, so what follows is decided by the VALUE and nothing else.
    assert classify_image_slot(_PROSE_SLOT, LIVE_LOGO) is ImageSlotKind.REFERENCE


@pytest.mark.parametrize(
    "value",
    [
        "Before/After growth",
        "Photo: Unsplash / Jane",
        "Revenue up 12% quarter/quarter",
    ],
)
def test_prose_under_a_non_descriptive_key_has_no_reference_shape(value: str):
    """VALUE-SHAPE rule, with the name rule provably out of the way.

    Under the pre-fix bare ``"/" in text`` separator rule each of these is a
    REFERENCE, so this is the mutation-discriminating assertion the ``_caption``
    cases could never be.
    """
    assert classify_image_slot(_PROSE_SLOT, value) is ImageSlotKind.UNCLASSIFIED


def test_prose_under_a_non_descriptive_key_is_unclassified_not_undrawn():
    """The same fact at the counter, where the states are actually different.

    UNCLASSIFIED says "this key's value is not a ref"; UNDRAWN says "this ref is
    real and nothing draws it". Under the bare ``"/"`` rule the caption acquires
    reference shape and the report flips to ``undrawn=1`` — a template defect
    reported against a slide that has none.
    """
    report = _report("bullets", {"heading": "Growth", _PROSE_SLOT: "Before/After growth"})

    assert (report.unclassified, report.unclassified_slots) == (1, (_PROSE_SLOT,))
    assert report.undrawn == 0, "prose is not a real ref, so there is nothing undrawn"
    assert (report.requested, report.failed) == (0, 0)
    _guard(report)  # and either way it must not veto the deck


# --------------------------------------------------------------------------
# Finding 2c — the separator rule's remaining false positives are HARMLESS.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["N/A", "and/or", "TCP/IP", "24/7"])
def test_a_slashed_abbreviation_is_still_reference_shaped_and_that_is_harmless(value: str):
    """DOCUMENTED, DELIBERATE: not tightened, because the blast radius is nil.

    ``"N/A"`` has no whitespace, so it survives the separator rule and an
    ``image_status: "N/A"`` is classified REFERENCE. That was checked rather
    than assumed, and it lands in ``undrawn`` on EVERY layout family — which
    warns and never vetoes.

    It cannot land in ``failed``: ``resolve_slide_images`` only ever calls
    ``load_image_bytes`` on the fixed drawn-slot names its family branches name
    (``before_image_url``/``after_image_url``/``screenshot_url``/``image_url``/
    ``preview_image_url``), and it does so on the RAW value without consulting
    ``classify_image_slot`` at all. So the classifier cannot promote anything
    into the veto path, and ``image_url: "N/A"`` fails its load identically with
    or without this rule.

    Tightening it is therefore all cost and no benefit: the same rule is the
    ONLY thing that recognises an extension-less object key
    (``decks/job1/hero``, ``w2v/2026/09/hero-shot``), and demoting one of those
    would stop a genuine image rendering AND let it escape the all-failed veto —
    strictly worse than the false positive being removed. Pinned by the
    extension-less cases in ``test_genuine_image_references_are_still_recognised``
    and by ``test_an_extensionless_object_key_needs_the_separator_rule`` below.
    """
    assert classify_image_slot("image_status", value) is ImageSlotKind.REFERENCE

    report = _report("bullets", {"heading": "Growth", "image_status": value})

    assert (report.requested, report.failed) == (0, 0), "never reaches the loader"
    assert (report.undrawn, report.undrawn_slots) == (1, ("image_status",))
    _guard(report)  # warns, does not veto


@pytest.mark.parametrize("value", ["decks/job1/hero", "w2v/2026/09/hero-shot", "bucket\\hero"])
def test_an_extensionless_object_key_needs_the_separator_rule(value: str):
    """Why the separator rule is not simply deleted.

    These carry no scheme and no file extension, so the separator test is the
    only positive signal they have. Deleting it to be rid of ``"N/A"`` would
    demote a real, drawable reference to UNCLASSIFIED.
    """
    from src.services.image_layout import (
        _IMAGE_REFERENCE_SCHEMES,
        _IMAGE_REFERENCE_SUFFIXES,
    )

    lowered = value.lower()
    assert not lowered.startswith(_IMAGE_REFERENCE_SCHEMES), "no scheme to fall back on"
    assert not lowered.endswith(_IMAGE_REFERENCE_SUFFIXES), "no extension to fall back on"
    assert classify_image_slot("image_url", value) is ImageSlotKind.REFERENCE


def test_a_dead_reference_is_still_reported_as_a_failed_load():
    """The control: the counter that means "your URL is dead" still works.

    Without this, "nothing is ever failed" would pass every other test here.
    """
    from src.services.html_slide_renderer import _slot_report

    report = _slot_report("image_url", "https://minio.test:8389/w2v/gone.png", loaded=False)

    assert (report.requested, report.failed) == (1, 1)
    assert report.undrawn == 0

    with pytest.raises(ValueError, match="every requested image failed to load"):
        _guard(report)


def test_failed_and_undrawn_are_reported_apart_on_one_deck():
    """The distinction the operator needs, on a single mixed report.

    A deck with one genuinely dead image AND one undrawn slot must show both
    causes separately — and must not be vetoed, because not every *fetched*
    image was lost... but the undrawn slot must not be what rescues it either.
    """
    from src.services.html_slide_renderer import _slot_report

    dead = _slot_report("image_url", "https://minio.test:8389/w2v/gone.png", loaded=False)
    live = _slot_report("image_url", "https://minio.test:8389/w2v/ok.png", loaded=True)
    undrawn = _report("title", {"logo": LIVE_LOGO})

    report = dead.merge(live).merge(undrawn)

    assert (report.requested, report.failed) == (2, 1)
    assert (report.undrawn, report.undrawn_slots) == (1, ("logo",))
    _guard(report)  # 1 of 2 fetched images lost → ships
