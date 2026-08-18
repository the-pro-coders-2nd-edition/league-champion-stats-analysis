"""Guardrail: reportNav.js's SECTION_CATEGORIES map stays in sync with the ids it must resolve.

RFC-001 step 13 ("SectionHeader.svelte") calls for `SECTION_CATEGORIES` keys to equal the set of
scroll-target ids the Svelte sections actually render, and for an unknown id to fail loudly rather
than silently degrading to no category (the exact bug class that produced 18 inert
`.section-title--*` modifier classes historically — see the RFC's Motivation section). There is no
frontend test runner yet, so this is the Python-side static check: it parses the plain-text source
of reportNav.js and the section components rather than executing Svelte.
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND_SRC = Path(__file__).resolve().parents[1] / "frontend" / "src"
REPORT_NAV_JS = FRONTEND_SRC / "lib" / "reportNav.js"
SECTIONS_DIR = FRONTEND_SRC / "sections"

_MAP_KEY_RE = re.compile(r"""^\s*(?:'([^']+)'|"([^"]+)"|([A-Za-z0-9_-]+))\s*:\s*'[^']+'\s*,?\s*$""")
_SECTION_ID_RE = re.compile(r"<section\b[^>]*\bid=\"([^\"]+)\"")


def _section_categories_keys() -> set[str]:
    text = REPORT_NAV_JS.read_text(encoding="utf-8")
    start = text.index("const SECTION_CATEGORIES = {")
    end = text.index("};", start)
    body = text[start:end]
    keys: set[str] = set()
    for line in body.splitlines():
        match = _MAP_KEY_RE.match(line)
        if match:
            keys.add(next(group for group in match.groups() if group))
    return keys


def _rendered_section_ids() -> set[str]:
    ids: set[str] = set()
    for path in SECTIONS_DIR.glob("*.svelte"):
        ids.update(_SECTION_ID_RE.findall(path.read_text(encoding="utf-8")))
    return ids


def _all_rendered_ids() -> set[str]:
    """Every `id="..."` literal in the sections directory, not just on `<section>` tags.

    `score-breakdown` is a real scroll target (Overview.svelte's `<h2 id="score-breakdown">`)
    that is not itself a `<section>` wrapper, so the full id set is what SECTION_CATEGORIES
    must be checked against, not only `<section id>`s.
    """
    ids: set[str] = set()
    for path in SECTIONS_DIR.glob("*.svelte"):
        ids.update(re.findall(r'\bid="([^"]+)"', path.read_text(encoding="utf-8")))
    return ids


def test_every_rendered_section_has_a_category() -> None:
    categories = _section_categories_keys()
    section_ids = _rendered_section_ids()
    missing = section_ids - categories
    assert not missing, (
        f"Section id(s) {sorted(missing)} render a <section id=...> with no entry in "
        "reportNav.js's SECTION_CATEGORIES map — SectionHeader would resolve no category for it."
    )


def test_every_category_key_is_a_real_scroll_target() -> None:
    categories = _section_categories_keys()
    all_ids = _all_rendered_ids()
    dead_keys = categories - all_ids
    assert not dead_keys, (
        f"SECTION_CATEGORIES key(s) {sorted(dead_keys)} do not match any rendered id in "
        "frontend/src/sections — they are dead map entries."
    )


def test_category_for_unknown_section_id_fails_loudly() -> None:
    """categoryForSection must throw (not silently return null) for an id outside the map."""
    text = REPORT_NAV_JS.read_text(encoding="utf-8")
    start = text.index("export function categoryForSection")
    end = text.index("\n}", start)
    body = text[start:end]
    assert "throw" in body, (
        "categoryForSection no longer fails loudly on an unknown section id — this is the "
        "silent-degradation bug class RFC-001 step 13 exists to close."
    )
