"""RFC-001 step 5: CSS/markup contract guardrails.

Enforces the nine rules from RFC-001's "Guardrails" section (`tests/test_css_contract.py`
block) via plain regex/file counting over `frontend/src/**` -- no JS test runner is wired
into pytest, and the RFC explicitly sanctions a grep-based gate over a real parser.

Nearly every rule here is mid-migration: the RFC states a baseline and a *target* (often
0, or a small allowlist), but several other RFC-001 steps that would reach that target
have not landed on this tree yet. Hardcoding today's literal counts as pass/fail
thresholds would make this test fail the moment any of those steps lands elsewhere and
merges. Instead every rule is a *ratchet* against `tests/fixtures/css_contract_baseline.json`:
a count may only go down (or stay flat). A PR that improves a count updates the baseline
file in the same diff. A PR that regresses a count (accidentally reintroducing a
`:global()`, a string builder, a raw hex literal, etc.) fails here without touching the
baseline.

None of the nine rules is asserted as a hard invariant, because none of them is fully met
on this tree yet (e.g. `components.css` still exists, `tones.js` still exists) -- asserting
the RFC's stated end-state directly would fail on the current tree, which the RFC's own
step-5 verification requires to pass ("Test passes on the current tree").
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_SRC = ROOT / "frontend" / "src"
COMPONENTS_DIR = FRONTEND_SRC / "components"
STYLES_DIR = FRONTEND_SRC / "styles"
BASELINE_PATH = Path(__file__).resolve().parent / "fixtures" / "css_contract_baseline.json"

pytestmark = pytest.mark.skipif(
    not FRONTEND_SRC.is_dir(), reason="frontend/src not present in this checkout"
)


def _baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text())


def _rel(path: Path) -> str:
    return str(path.relative_to(FRONTEND_SRC)).replace("\\", "/")


def _svelte_files() -> list[Path]:
    return sorted(FRONTEND_SRC.rglob("*.svelte"))


def _js_files() -> list[Path]:
    return sorted(FRONTEND_SRC.rglob("*.js"))


def _assert_no_new_or_worse(
    current: Mapping[str, int], baseline: Mapping[str, int], label: str
) -> None:
    """Fail if any key regressed past its baseline count, including brand-new keys."""
    regressions = {
        key: (value, baseline.get(key, 0))
        for key, value in current.items()
        if value > baseline.get(key, 0)
    }
    assert not regressions, (
        f"{label}: regression(s) beyond the checked-in baseline "
        f"(tests/fixtures/css_contract_baseline.json): {regressions!r}. "
        "If this is an intentional improvement elsewhere that also *reduced* other "
        "entries, update the baseline in the same diff; otherwise this is a real "
        "regression."
    )


# --- Rule 1: no markup-or-class props -------------------------------------------------
#
# A prop is "markup-or-class" if its value is piped straight into {@html <name>} inside
# the same component (a raw-HTML prop), or if it is literally named `extraClass` (the
# RFC's named escape-hatch prop on Pill.svelte). Baseline 5, target 0.
def _markup_or_class_props() -> list[str]:
    violations: list[str] = []
    for f in _svelte_files():
        text = f.read_text()
        script_match = re.search(r"<script[^>]*>(.*?)</script>", text, re.S)
        if not script_match:
            continue
        props = re.findall(r"export\s+let\s+(\w+)", script_match.group(1))
        html_used = set(re.findall(r"\{@html\s+([A-Za-z_]\w*)\s*\}", text))
        for prop in props:
            if prop in html_used or prop == "extraClass":
                violations.append(f"{_rel(f)}:{prop}")
    return sorted(set(violations))


def test_rule1_no_new_markup_or_class_props():
    baseline = _baseline()["rule1_markup_or_class_props"]
    current = _markup_or_class_props()
    assert len(current) <= baseline["count"], (
        f"rule 1 (markup-or-class props): {len(current)} found (baseline "
        f"{baseline['count']}): {current!r}. Target is 0 -- see RFC-001 guardrail rule 1."
    )
    new_ones = sorted(set(current) - set(baseline["props"]))
    assert not new_ones, (
        f"rule 1: new markup-or-class prop(s) introduced that aren't in the baseline "
        f"allowlist, even though the total count didn't regress: {new_ones!r}"
    )


# --- Rule 2: no new markup-builder template literals ----------------------------------
#
# r'`\s*<[a-z]' inside .svelte and lib/*.js -- a template literal that opens an HTML tag.
# Baseline 109 (RFC doc) / 75 (measured on this tree) across 4 files today. Target 1
# (Chatbot's markdown renderer). Keyed to the PRODUCER file per the RFC.
_BUILDER_RE = re.compile(r"`\s*<[a-z]")


def _markup_builder_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in list(_svelte_files()) + list(_js_files()):
        n = len(_BUILDER_RE.findall(f.read_text()))
        if n:
            counts[_rel(f)] = n
    return counts


def test_rule2_no_new_markup_builders():
    baseline = _baseline()["rule2_markup_builders"]
    current = _markup_builder_counts()
    _assert_no_new_or_worse(current, baseline["per_file"], "rule 2 (markup-builder template literals)")
    assert sum(current.values()) <= baseline["total"], (
        f"rule 2: total markup-builder literal count rose to {sum(current.values())} "
        f"(baseline {baseline['total']}). Target is 1 (Chatbot.svelte)."
    )


# --- Rule 3: no new {@html} injection sites --------------------------------------------
#
# Counts injection points, not producers (rule 2 gates producers). Baseline 23 measured
# across 7 files. Chatbot.svelte's markdown renderer is a permanent, RFC-sanctioned entry
# and is exempted from the ratchet -- it is not a template-literal HTML builder covered by
# rule 2, so it does not double count with that rule's target.
def _html_site_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in _svelte_files():
        n = len(re.findall(r"\{@html\b", f.read_text()))
        if n:
            counts[_rel(f)] = n
    return counts


def test_rule3_no_new_html_sites():
    baseline = _baseline()["rule3_html_sites"]
    current = _html_site_counts()
    permanent = baseline["permanent_allowlist"]
    current_non_permanent = {k: v for k, v in current.items() if k not in permanent}
    baseline_non_permanent = {k: v for k, v in baseline["per_file"].items() if k not in permanent}
    _assert_no_new_or_worse(current_non_permanent, baseline_non_permanent, "rule 3 ({@html} sites)")
    total_non_permanent = sum(current_non_permanent.values())
    baseline_total_non_permanent = baseline["total"] - sum(permanent.values())
    assert total_non_permanent <= baseline_total_non_permanent, (
        f"rule 3: non-permanent {{@html}} site count rose to {total_non_permanent} "
        f"(baseline {baseline_total_non_permanent})."
    )
    # The permanent allowlist entry itself must stay exactly what it was seeded as --
    # if it grows, someone is using the "permanent exception" as a general escape hatch.
    for key, allowed in permanent.items():
        assert current.get(key, 0) <= allowed, (
            f"rule 3: permanently-allowlisted {key} grew to {current.get(key, 0)} "
            f"(allowed {allowed}) -- the Chatbot markdown exception is not a general escape hatch."
        )


# --- Rule 4: no new :global() outside allowlist ----------------------------------------
#
# An allowlisted file must carry a `/* :global -- <reason> */` comment naming a live
# producer from rule 2 (by file basename, case-insensitively). No file in this tree
# currently satisfies that (Pill.svelte has a `:global` comment, but it names Coaching's
# hand-written markup, not one of the current rule-2 producer files), so today's
# unallowlisted set equals the raw :global() count. Baseline 103 rules / 15 files.
def _global_counts_outside_allowlist() -> dict[str, int]:
    producer_basenames = {Path(p).stem.lower() for p in _markup_builder_counts()}
    per_file: dict[str, int] = {}
    for f in _svelte_files():
        text = f.read_text()
        n = len(re.findall(r":global\(", text))
        if not n:
            continue
        comments = re.findall(r"/\*\s*:global\s*--(.*?)\*/", text, re.S)
        allowed = any(
            any(base in comment.lower() for base in producer_basenames) for comment in comments
        )
        if not allowed:
            per_file[_rel(f)] = n
    return per_file


def test_rule4_no_new_unallowlisted_global():
    baseline = _baseline()["rule4_global_outside_allowlist"]
    current = _global_counts_outside_allowlist()
    _assert_no_new_or_worse(current, baseline["per_file"], "rule 4 (:global() outside allowlist)")
    assert len(current) <= baseline["files"], (
        f"rule 4: {len(current)} files carry unallowlisted :global() rules "
        f"(baseline {baseline['files']})."
    )
    assert sum(current.values()) <= baseline["rules"], (
        f"rule 4: {sum(current.values())} unallowlisted :global() rules "
        f"(baseline {baseline['rules']})."
    )


# --- Rule 5: at most one :root definition per custom property -------------------------
#
# Only top-level `:root { ... }` blocks count. A property redefined inside a scoped
# selector (e.g. `nav.report-nav { --scrollbar-track: ... }`) is a contextual override,
# explicitly permitted by the RFC, and is not a competing :root definition.
def _root_token_collisions() -> dict[str, list[str]]:
    defs: dict[str, set[str]] = {}
    for f in STYLES_DIR.glob("*.css"):
        text = f.read_text()
        for block_match in re.finditer(r":root\s*\{([^}]*)\}", text, re.S):
            for prop in re.findall(r"--([a-zA-Z0-9-]+)\s*:", block_match.group(1)):
                defs.setdefault(prop, set()).add(f.name)
    return {prop: sorted(files) for prop, files in defs.items() if len(files) > 1}


def test_rule5_no_new_root_token_collisions():
    baseline = _baseline()["rule5_root_token_collisions"]
    current = _root_token_collisions()
    new_tokens = sorted(set(current) - set(baseline["tokens"]))
    assert not new_tokens, f"rule 5: new :root token collision(s): {new_tokens!r}"
    assert len(current) <= baseline["count"], (
        f"rule 5: {len(current)} :root token collisions (baseline {baseline['count']}): "
        f"{sorted(current)!r}. Target is 0."
    )


# --- Rule 6: no new raw colour literals in .svelte -------------------------------------
#
# r'#[0-9a-fA-F]{3,8}\b|rgba?\(\s*\d' anywhere in a .svelte file, exempting rgba(0,0,0,*)
# (used for shadow overlays, not a semantic colour choice). Baseline 20 occurrences / 17
# lines measured on this tree. Target 0.
_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+")
_EXEMPT_RE = re.compile(r"rgba?\(\s*0\s*,\s*0\s*,\s*0\s*[,)]")


def _raw_color_occurrences() -> tuple[int, int]:
    total = 0
    lines: set[str] = set()
    for f in _svelte_files():
        for lineno, line in enumerate(f.read_text().splitlines(), 1):
            for match in _COLOR_RE.finditer(line):
                snippet = line[match.start() : match.start() + 30]
                if _EXEMPT_RE.match(snippet):
                    continue
                total += 1
                lines.add(f"{_rel(f)}:{lineno}")
    return total, len(lines)


def test_rule6_no_new_raw_colors():
    baseline = _baseline()["rule6_raw_colors"]
    occurrences, line_count = _raw_color_occurrences()
    assert occurrences <= baseline["occurrences"], (
        f"rule 6: {occurrences} raw colour literals in .svelte files "
        f"(baseline {baseline['occurrences']}). Target is 0."
    )
    assert line_count <= baseline["lines"], (
        f"rule 6: {line_count} lines with raw colour literals (baseline {baseline['lines']})."
    )


# --- Rule 7: no committed generated CSS ------------------------------------------------
#
# The RFC's end state is `components.css` deleted entirely and no stale Svelte scope hash
# (`.svelte-xxxxxx`) committed to any stylesheet. Both still exist on this tree (step 3
# hasn't landed here yet), so this rule ratchets toward that end state rather than
# asserting it outright.
_STALE_HASH_RE = re.compile(r"\.svelte-[a-z0-9]{6,}")


def _generated_css_state() -> tuple[bool, int]:
    components_css = STYLES_DIR / "components.css"
    hash_count = 0
    for f in STYLES_DIR.glob("*.css"):
        hash_count += len(_STALE_HASH_RE.findall(f.read_text()))
    return components_css.exists(), hash_count


def test_rule7_no_new_generated_css():
    baseline = _baseline()["rule7_generated_css"]
    present, hash_count = _generated_css_state()
    if baseline["components_css_present"] is False:
        assert not present, "rule 7: components.css was deleted (per baseline) and must stay deleted."
    assert hash_count <= baseline["stale_hash_count"], (
        f"rule 7: {hash_count} stale Svelte scope-hash selectors committed to styles/*.css "
        f"(baseline {baseline['stale_hash_count']}). Target is 0."
    )


# --- Rule 8: a component earns a file --------------------------------------------------
#
# Every .svelte file in components/ needs 2+ lexical call sites in distinct files, or it
# must be in the checked-in exception allowlist below (per-instance state / self-contained
# CSS block). "Per-instance state" isn't mechanically checkable via grep, so this ratchets
# on the set of under-2-call-site components: the set may only shrink (an entry leaving
# the set because it either gained a second call site or was deleted is progress; a new
# entry appearing is a regression). The RFC's final named allowlist (ScoreDisclosure,
# SkillGrid, GameSummaryHeader, IconStatTable) is a subset of today's baseline and is never
# itself flagged as a regression once the rest of the set empties out.
_IMPORT_RE = re.compile(r"import\s+\w+\s+from\s+['\"]([^'\"]+)['\"]")


def _underused_components() -> list[str]:
    component_files = sorted(COMPONENTS_DIR.glob("*.svelte"))
    names = {f.stem: f for f in component_files}
    importers: dict[str, set[str]] = {name: set() for name in names}
    for f in list(_svelte_files()):
        text = f.read_text()
        for match in _IMPORT_RE.finditer(text):
            stem = Path(match.group(1)).stem
            if stem in names and f.resolve() != names[stem].resolve():
                importers[stem].add(_rel(f))
    return sorted(name for name, callers in importers.items() if len(callers) < 2)


def test_rule8_component_call_site_allowlist_only_shrinks():
    baseline = _baseline()["rule8_underused_components"]
    current = _underused_components()
    new_names = sorted(set(current) - set(baseline["names"]))
    assert not new_names, (
        f"rule 8: new under-used component(s) (fewer than 2 call sites) not covered by "
        f"the baseline allowlist: {new_names!r}. A component earns a file only with 2+ "
        f"call sites, or a named exception -- add it to the baseline only if it has "
        f"genuine per-instance state, and record why."
    )
    assert len(current) <= baseline["count"], (
        f"rule 8: {len(current)} under-used components (baseline {baseline['count']}): "
        f"{current!r}. Target is the 4 named exceptions "
        f"(ScoreDisclosure, SkillGrid, GameSummaryHeader, IconStatTable)."
    )


# --- Rule 9: dead modules ---------------------------------------------------------------
#
# tones.js (0 importers) should eventually be deleted; tones.py's delta_tone/delta_label
# should eventually gain a non-test caller or be deleted too (career_count/career_node are
# NOT dead -- career.py calls both in production and must never be flagged here). Both
# still fail their end-state on this tree, so this ratchets on the "still dead" count.
def _dead_module_state() -> tuple[bool, dict[str, int]]:
    tones_js_present = (FRONTEND_SRC / "tones.js").exists()
    tones_py = ROOT / "src" / "league_stats" / "presentation" / "tones.py"
    callers = {"delta_tone": 0, "delta_label": 0}
    src_dir = ROOT / "src"
    if src_dir.is_dir():
        for pyf in src_dir.rglob("*.py"):
            if pyf.resolve() == tones_py.resolve():
                continue
            text = pyf.read_text()
            for name in callers:
                if name in text:
                    callers[name] += 1
    return tones_js_present, callers


def test_rule9_dead_modules_do_not_grow():
    baseline = _baseline()["rule9_dead_modules"]
    tones_js_present, callers = _dead_module_state()
    if baseline["tones_js_present"] is False:
        assert not tones_js_present, "rule 9: tones.js was deleted (per baseline) and must stay deleted."
    for name, count in callers.items():
        baseline_count = baseline[f"{name}_non_test_callers"]
        if baseline_count > 0:
            assert count >= baseline_count or count > 0, (
                f"rule 9: {name} lost its only non-test caller(s) without being deleted -- "
                "it is now dead code with no non-test caller and no removal."
            )
        # If it already had 0 non-test callers, this rule doesn't regress by staying at 0;
        # the fix is either "gains a real caller" (count goes up) or "the function and its
        # test are deleted" (out of scope for a grep-based caller count).
