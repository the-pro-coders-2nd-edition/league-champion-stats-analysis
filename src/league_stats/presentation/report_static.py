"""Publish and refresh report CSS/JS assets under ``output/``.

Reports historically copied stylesheets into each build folder
(``reports/{player}/{build}/static/``). That drifts whenever a build is
skipped after a template change, and browsers happily keep serving the old
CSS against freshly rewritten HTML — which is why filter/tab buttons
sometimes render as unstyled system widgets.

This module:
1. Publishes a single shared copy under ``output/assets/report-ui/``.
2. Refreshes every on-disk per-build ``static/`` folder so older reports
   that still link to ``static/report.css`` pick up current styles.
3. Exposes a short content hash for cache-busting query strings.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

REPORT_UI_DIRNAME = "report-ui"
REPORT_STATIC_FILES = ("report.css", "chatbot.css", "index.css", "design-tokens.css")


def _static_src(template_dir: Path) -> Path:
    return template_dir / "static"


def _shared_dir(output_dir: Path) -> Path:
    return output_dir / "assets" / REPORT_UI_DIRNAME


def report_static_version(template_dir: Path) -> str:
    """Short content hash of packaged report stylesheets (cache buster)."""
    digest = hashlib.sha256()
    src = _static_src(template_dir)
    for name in REPORT_STATIC_FILES:
        path = src / name
        if not path.is_file():
            continue
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def _copy_static_files(src_dir: Path, dest_dir: Path) -> None:
    """Copy known stylesheet files from ``src_dir`` into ``dest_dir``."""
    if not src_dir.is_dir():
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name in REPORT_STATIC_FILES:
        src = src_dir / name
        if src.is_file():
            shutil.copy2(src, dest_dir / name)


def sync_report_static_dirs(output_dir: Path, template_dir: Path) -> int:
    """Refresh ``static/`` under every saved build report.

    Returns:
        Number of build directories updated.
    """
    src = _static_src(template_dir)
    if not src.is_dir():
        return 0
    reports_root = output_dir / "reports"
    if not reports_root.is_dir():
        return 0
    updated = 0
    for report_html in reports_root.glob("*/*/report.html"):
        _copy_static_files(src, report_html.parent / "static")
        updated += 1
    return updated


def ensure_report_static_assets(
    output_dir: Path,
    template_dir: Path,
    *,
    sync_existing: bool = True,
) -> dict[str, Any]:
    """Publish shared report UI assets and optionally refresh saved copies.

    Args:
        output_dir: Root output directory (``output/``).
        template_dir: Jinja template dir containing ``static/``.
        sync_existing: When true, also overwrite per-build ``static/`` folders.

    Returns:
        Dict with ``version`` and the shared directory path.
    """
    src = _static_src(template_dir)
    shared = _shared_dir(output_dir)
    _copy_static_files(src, shared)
    if sync_existing:
        sync_report_static_dirs(output_dir, template_dir)
    return {
        "version": report_static_version(template_dir),
        "shared_dir": shared,
    }


def report_stylesheet_hrefs(
    *,
    from_dir: Path,
    output_dir: Path,
    template_dir: Path,
) -> dict[str, str]:
    """Relative stylesheet URLs (with cache-busting) for a report directory.

    Falls back to the local ``static/`` copy when the shared asset is missing
    so ``file://`` opens of partially generated trees still work.
    """
    ensure_report_static_assets(output_dir, template_dir, sync_existing=False)
    version = report_static_version(template_dir)
    shared = _shared_dir(output_dir)
    hrefs: dict[str, str] = {}
    for name, key in (
        ("report.css", "report_css_href"),
        ("chatbot.css", "chatbot_css_href"),
        ("design-tokens.css", "design_tokens_css_href"),
    ):
        shared_path = shared / name
        if shared_path.is_file():
            rel = Path(os.path.relpath(shared_path.resolve(), from_dir.resolve())).as_posix()
        else:
            rel = f"static/{name}"
        hrefs[key] = f"{rel}?v={version}"
    hrefs["report_static_version"] = version
    return hrefs
