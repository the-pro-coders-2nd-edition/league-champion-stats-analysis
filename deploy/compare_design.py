"""Screenshot the design reference and the generated preview report, tab by tab.

Usage:
    /Users/brice.parent/dd/poc/short-schema/.venv/bin/python deploy/compare_design.py

Requires the poc's existing playwright venv (already has Chromium installed).
Run this OUTSIDE the sandboxed Claude Code session -- Playwright can't launch
a browser inside it (Shadowfax blocks the mach port rendezvous the browser
needs).

For each page, this clicks through every top-level category tab and takes a
screenshot of each state. Output goes to /tmp/design_compare/:
    reference_<n>_<label>.png   -- the target (Aatrox Report.html)
    actual_<n>_<label>.png      -- what the app currently generates

Notes on why this version differs from the first attempt:
- full_page=True screenshots in headless Chromium mishandle
  `position: sticky` elements (our report has a sticky left nav), producing
  a blank/garbled capture. Fixed by using a very tall fixed viewport and a
  plain (non-full-page) screenshot instead.
- The reference file's tabs are plain text elements, not `role="tab"` --
  matching by role picked up the queue/window filter buttons instead. Fixed
  by matching on the exact known tab labels.
- Console/page errors and failed network requests are now printed so a
  genuinely broken page (missing CSS, JS exception) is visible instead of
  silently producing a blank screenshot.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent

REFERENCE_PATH = Path("/Users/brice.parent/Downloads/Aatrox Report.html")
ACTUAL_PATH = REPO_ROOT / "output" / "reports" / "preview_euw" / "thresh_utility" / "report.html"

OUT_DIR = Path("/tmp/design_compare")

# Tall fixed viewport + non-full-page screenshot avoids the sticky-element
# full-page screenshot bug in headless Chromium.
VIEWPORT = {"width": 1600, "height": 6000}

# Our own app's real tab ids (from generated/tab_bar_report_category.html) --
# clicking by id is reliable since we know the exact generated markup.
ACTUAL_TABS = [
    ("tab-summary", "summary"),
    ("tab-performance", "performance"),
    ("tab-games", "game-review"),
    ("tab-champion", "champion"),
    ("tab-deepdive", "deep-dive"),
]

# The reference's category tabs are plain text, not role="tab" elements --
# matched by exact visible label instead. "Summary" is the default state
# (shot 0), so it's not clicked again here.
REFERENCE_TAB_LABELS = ["Career", "Performance", "Game Review", "Champion", "Deep Dive"]


def slugify(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-") or "tab"


def wire_diagnostics(page: Page, tag: str) -> None:
    page.on("console", lambda m: print(f"  [{tag} console:{m.type}] {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: print(f"  [{tag} pageerror] {e}"))
    page.on("requestfailed", lambda r: print(f"  [{tag} requestfailed] {r.url} -- {r.failure}"))


def shoot(page: Page, out_path: Path) -> None:
    page.wait_for_timeout(600)  # let any in-flight layout/animation settle
    page.screenshot(path=str(out_path))
    print(f"saved {out_path.name}")


def shoot_actual_tabs(page: Page, url: str) -> None:
    wire_diagnostics(page, "actual")
    page.goto(url, wait_until="load")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)
    shoot(page, OUT_DIR / "actual_0_summary.png")

    for i, (tab_id, slug) in enumerate(ACTUAL_TABS[1:], start=1):
        locator = page.locator(f"#{tab_id}")
        if locator.count() == 0:
            print(f"  [warn] tab id #{tab_id} not found, skipping")
            continue
        locator.click()
        page.wait_for_timeout(1500)
        shoot(page, OUT_DIR / f"actual_{i}_{slug}.png")


def shoot_reference_tabs(page: Page, url: str) -> None:
    wire_diagnostics(page, "reference")
    page.goto(url, wait_until="load")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(6000)  # bundled/self-extracting page needs time to reconstruct DOM
    shoot(page, OUT_DIR / "reference_0_summary.png")

    for i, label in enumerate(REFERENCE_TAB_LABELS, start=1):
        locator = page.get_by_text(label, exact=True).first
        if locator.count() == 0:
            print(f"  [warn] reference tab '{label}' not found, skipping")
            continue
        try:
            locator.click(timeout=3000)
        except Exception as e:
            print(f"  [warn] could not click '{label}': {e}")
            continue
        page.wait_for_timeout(1500)
        shoot(page, OUT_DIR / f"reference_{i}_{slugify(label)}.png")


def main() -> int:
    if not REFERENCE_PATH.exists():
        print(f"missing reference file: {REFERENCE_PATH}", file=sys.stderr)
        return 1
    if not ACTUAL_PATH.exists():
        print(
            f"missing generated report: {ACTUAL_PATH}\n"
            "Build it first with:\n"
            "  rm -rf output && MPLCONFIGDIR=$(mktemp -d) uv run python deploy/build_preview_report.py",
            file=sys.stderr,
        )
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()

        page = browser.new_context(viewport=VIEWPORT).new_page()
        print("=== reference (Aatrox Report.html) ===")
        shoot_reference_tabs(page, f"file://{REFERENCE_PATH}")
        page.close()

        page = browser.new_context(viewport=VIEWPORT).new_page()
        print("\n=== actual (generated preview report) ===")
        shoot_actual_tabs(page, f"file://{ACTUAL_PATH}")
        page.close()

        browser.close()

    print(f"\nDone. All screenshots in {OUT_DIR}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
