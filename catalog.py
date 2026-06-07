#!/usr/bin/env python3
"""discogser — catalog vinyl records into Discogs from phone photos.

Usage:
    python catalog.py ./photos [--dry-run] [--commit] [--folder NAME]

Defaults to --dry-run: everything is processed and reported, but nothing is
written to your collection. Pass --commit to actually add releases.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from config import Config, ConfigError
from main import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="catalog.py",
        description="Catalog vinyl records into Discogs from phone photos.",
    )
    parser.add_argument("photos", type=Path, help="Folder of photos (3 per album).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Process and report only; make no writes (default).",
    )
    mode.add_argument(
        "--commit",
        action="store_true",
        help="Actually add HIGH/MEDIUM-confidence albums to your collection.",
    )
    parser.add_argument(
        "--folder",
        default=None,
        help="Discogs folder name to add to (overrides DISCOGS_FOLDER).",
    )
    parser.add_argument(
        "--no-cover",
        action="store_true",
        help="Disable visual cover-art confirmation (saves a vision call per "
        "unconfirmed album, but catalogs fewer records by default).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()

    try:
        config = Config.load()
    except ConfigError as exc:
        console.print(f"[red]Config error:[/red] {exc}")
        return 2

    # Default is dry-run; --commit is the only way to write.
    commit = bool(args.commit)
    return run(
        args.photos,
        config=config,
        commit=commit,
        folder_name=args.folder,
        cover_match=not args.no_cover,
        console=console,
    )


if __name__ == "__main__":
    sys.exit(main())
