"""`discogser --doctor`: preflight checks so the first real run isn't a leap of
faith. It confirms your config is present, that both API keys actually work, and
(optionally) that a photo folder groups cleanly, before you spend a cent.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.markup import escape

from .config import DEFAULT_FOLDER, DEFAULT_MODEL
from .discogs import DiscogsClient
from .pipeline import (
    HEIC_HELP,
    discover_images,
    group_images,
    heic_unsupported_count,
    sort_images,
)


def _short(exc: object) -> str:
    s = str(exc).strip().replace("\n", " ")
    return s if len(s) <= 150 else s[:147] + "..."


def doctor(
    console: Console,
    photos_dir: Path | None = None,
    env_file: str | os.PathLike[str] | None = None,
) -> int:
    """Run all checks; return 0 if everything passed, 1 otherwise."""
    load_dotenv(env_file if env_file is not None else Path.cwd() / ".env", override=False)
    env = os.environ
    ok = True

    def good(name: str, shown: str) -> None:
        console.print(f"  [green]✓[/green] {name:<20} {escape(shown)}")

    def bad(name: str, why: str) -> None:
        nonlocal ok
        ok = False
        console.print(f"  [red]✗[/red] {name:<20} [red]{escape(why)}[/red]")

    api_key = env.get("ANTHROPIC_API_KEY", "").strip()
    model = env.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL
    token = env.get("DISCOGS_TOKEN", "").strip()
    username = env.get("DISCOGS_USERNAME", "").strip()
    folder = env.get("DISCOGS_FOLDER", "").strip() or DEFAULT_FOLDER
    user_agent = env.get("USER_AGENT", "").strip()

    console.print("[bold]Config[/bold]")
    good("ANTHROPIC_API_KEY", "set") if api_key else bad("ANTHROPIC_API_KEY", "missing")
    good("ANTHROPIC_MODEL", model)
    good("DISCOGS_TOKEN", "set") if token else bad("DISCOGS_TOKEN", "missing")
    good("DISCOGS_USERNAME", username) if username else bad("DISCOGS_USERNAME", "missing")
    good("DISCOGS_FOLDER", folder)
    good("USER_AGENT", user_agent) if user_agent else bad("USER_AGENT", "missing")

    console.print("\n[bold]Connectivity[/bold]")
    if api_key:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key, timeout=30.0, max_retries=1)
            client.messages.create(
                model=model, max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            good("Anthropic", f"key valid, {model} responded")
        except Exception as exc:
            bad("Anthropic", _short(exc))
    else:
        console.print("  [yellow]-[/yellow] Anthropic            skipped (no key)")

    if token and username and user_agent:
        try:
            with DiscogsClient(token=token, username=username, user_agent=user_agent) as discogs:
                who = discogs.whoami()
            good("Discogs", f"authenticated as {who.get('username', username)}")
        except Exception as exc:
            bad("Discogs", _short(exc))
    else:
        console.print("  [yellow]-[/yellow] Discogs              skipped (missing token/username/user-agent)")

    if photos_dir is not None:
        console.print(f"\n[bold]Photos[/bold] ({photos_dir})")
        if not photos_dir.is_dir():
            bad("folder", "not a directory")
        else:
            images = sort_images(discover_images(photos_dir))
            if heic_unsupported_count(images):
                bad("HEIC", HEIC_HELP)
            groups, leftovers = group_images(images)
            if not images:
                console.print("  [yellow]-[/yellow] no images found")
            elif leftovers:
                bad("grouping", f"{len(groups)} albums, but {len(leftovers)} leftover photo(s): "
                    + ", ".join(p.name for p in leftovers))
            else:
                good("grouping", f"{len(groups)} albums, no leftovers")

    console.print()
    if ok:
        console.print("[green]All good. You're ready to run.[/green]")
    else:
        console.print("[red]Some checks failed. Fix the above, then re-run [bold]discogser --doctor[/bold].[/red]")
    return 0 if ok else 1
