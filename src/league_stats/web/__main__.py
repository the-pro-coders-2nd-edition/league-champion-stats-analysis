"""Run the web app: ``python -m league_stats.web``."""

from __future__ import annotations

import argparse
import os

import uvicorn

from league_stats.core.config import load_web_config
from league_stats.presentation.brand_assets import APP_TITLE
from league_stats.utils import setup_logging
from league_stats.web.app import create_app


def main(argv: list[str] | None = None) -> None:
    """Start the FastAPI server (search UI, job queue, reports, chat proxy)."""
    parser = argparse.ArgumentParser(description=f"{APP_TITLE} web app")
    parser.add_argument("--host", default=None, help="Bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="HTTP port (default 8000)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args(argv)

    setup_logging(service="api-ui", version=os.environ.get("GIT_COMMIT", "dev"), verbose=args.verbose)
    web_config = load_web_config(host=args.host, port=args.port)
    uvicorn.run(create_app(web_config), host=web_config.host, port=web_config.port)


if __name__ == "__main__":
    main()
