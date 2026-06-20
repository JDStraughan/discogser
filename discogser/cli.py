"""Command-line entry point.

Usage:
    discogser ./photos [--dry-run | --commit] [--folder NAME] [--no-cover]

Defaults to --dry-run: everything is processed and reported, but nothing is
written to your collection. Pass --commit to actually add releases.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from rich.console import Console

from .config import Config, ConfigError
from .pipeline import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discogser",
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
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose debug logging (otherwise only warnings/errors are logged).",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Also append logs to this file.",
    )
    return parser


def _setup_logging(verbose: bool, log_file: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose, args.log_file)
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
