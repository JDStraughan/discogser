"""Console UI: a persistent progress bar with a live tally, plus aligned,
color-coded per-album rows that stay scannable whether you drop in 1 LP or 1000.

Design goals:
  * One result row per album, column-aligned, never wrapping (wrapping destroys
    scannability). Long titles/signals are truncated with an ellipsis.
  * Color is an *accent*, not a wash — the status glyph, confidence badge, and
    release id carry the color; the album text stays readable so your eyes don't
    bleed over a long run.
  * A pinned bottom bar shows position, %, elapsed, ETA, and a running tally
    (added / medium / review / skipped / errors) so you always know where you
    are without scrolling.
  * The things you MUST notice — sequence drift, leftovers, the final summary —
    are full-width panels, not easy-to-miss lines.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.box import ROUNDED
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

# Status taxonomy -> (glyph, badge label, accent color).
_STATUS = {
    "high":    ("✓", "HIGH",   "bright_green"),
    "medium":  ("✓", "MEDIUM", "yellow"),
    "review":  ("⚑", "LOW",    "red"),
    "skipped": ("↻", "DUP",    "grey50"),
    "error":   ("✗", "ERR",    "bright_red"),
}
_BADGE_WIDTH = 6
_RID_WIDTH = 9   # "r1234567"
_VALUE_WIDTH = 8  # "$1,234" / "$24.99"


@dataclass
class Tally:
    added: int = 0      # HIGH + MEDIUM (added, or would-add in dry-run)
    medium: int = 0     # subset of added
    review: int = 0     # LOW -> review.csv
    skipped: int = 0    # dupes / already processed
    errors: int = 0
    extra: dict = field(default_factory=dict)


def _fit(text: str, width: int) -> str:
    if width <= 1:
        return ""
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


class RunUI:
    """Owns the live progress display and renders album rows above it."""

    def __init__(self, console: Console, total: int, commit: bool) -> None:
        self.console = console
        self.total = total
        self.commit = commit
        self.tally = Tally()
        self._done = 0
        self._counter_w = max(len(str(total)), 1)

        self._progress = Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold]cataloguing[/bold]"),
            BarColumn(bar_width=None, complete_style="cyan", finished_style="green"),
            TaskProgressColumn(),
            TextColumn("[dim]{task.completed}/{task.total}[/dim]"),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("[dim]elapsed •[/dim]"),
            TimeRemainingColumn(),
            TextColumn("[dim]left[/dim]"),
            TextColumn("{task.fields[tally]}"),
            console=console,
            transient=False,
        )
        self._task = None

    # -- lifecycle ----------------------------------------------------------

    def __enter__(self) -> "RunUI":
        self._progress.start()
        self._task = self._progress.add_task(
            "run", total=self.total, tally=self._tally_markup()
        )
        return self

    def __exit__(self, *exc: object) -> None:
        self._progress.stop()

    # -- top-of-run chrome --------------------------------------------------

    def header(self, folder_name: str, folder_id: int, owned: int) -> None:
        mode = (
            Text(" COMMIT ", style="bold white on red3")
            if self.commit
            else Text(" DRY-RUN ", style="bold black on yellow")
        )
        body = Text()
        body.append("mode    ")
        body.append_text(mode)
        body.append("\n")
        body.append("albums  ", style="dim")
        body.append(f"{self.total}", style="bold")
        body.append("\n")
        body.append("folder  ", style="dim")
        body.append(f"{folder_name} ", style="cyan")
        body.append(f"(id {folder_id})", style="dim")
        body.append("\n")
        body.append("owned   ", style="dim")
        body.append(f"{owned}", style="bold")
        body.append(" already in your collection", style="dim")
        self.console.print(
            Panel(body, title="discogser", title_align="left", box=ROUNDED, border_style="cyan")
        )
        # Faint column legend so the rows make sense at a glance.
        legend = Text("  #     ", style="dim")
        legend.append(
            f"{'·':<1}  {'conf':<{_BADGE_WIDTH}}  artist — title   release    value     signal",
            style="dim",
        )
        self._print(legend)

    # -- per-album rows -----------------------------------------------------

    def album(
        self,
        *,
        status: str,
        artist: str,
        title: str,
        release_id: int | None,
        signal: str,
        committed: bool,
        value: str = "—",
    ) -> None:
        """Render one finished album and advance the bar. `status` is one of the
        keys in _STATUS. `value` is a pre-formatted price string (or '—')."""
        self._done += 1
        self._update_tally(status)
        glyph, label, color = _STATUS[status]

        line = Text(no_wrap=True, overflow="ellipsis")
        # counter
        line.append(f"[{self._done:>{self._counter_w}}/{self.total}] ", style="dim")
        # glyph
        line.append(f"{glyph} ", style=color)
        # confidence badge
        line.append(f"{label:<{_BADGE_WIDTH}} ", style=f"bold {color}")

        # Exact column budget so the row is always a single line:
        #   prefix = counter + glyph + badge
        #   then: album_w + gap + rid + gap + value + gap + signal
        prefix = (2 * self._counter_w + 4) + 2 + (_BADGE_WIDTH + 1)
        fixed = prefix + 6 + _RID_WIDTH + _VALUE_WIDTH  # three 2-space gaps
        avail = max(self.console.width - fixed - 1, 24)
        album_w = max(int(avail * 0.62), 16)
        sig_w = max(avail - album_w, 6)
        rid_str = f"r{release_id}" if release_id else "—"
        sig = signal or ""

        artist_disp = artist.strip() or "Unknown"
        title_disp = title.strip()
        album_text = f"{artist_disp} — {title_disp}" if title_disp else artist_disp
        album_fit = _fit(album_text, album_w)
        # bold only the artist portion for readability
        if " — " in album_fit:
            a, _, rest = album_fit.partition(" — ")
            line.append(a, style="bold")
            line.append(" — ", style="dim")
            line.append(rest)
        else:
            line.append(album_fit, style="bold")
        # pad to album_w so the rid column lines up
        pad = album_w - len(album_fit)
        if pad > 0:
            line.append(" " * pad)

        line.append("  ")
        rid_style = "cyan" if release_id else "dim"
        line.append(f"{rid_str:<{_RID_WIDTH}}", style=rid_style)
        line.append("  ")
        # value column — right-aligned so prices line up; money-green when present
        value_str = value or "—"
        value_style = "green" if value_str != "—" else "dim"
        line.append(f"{_fit(value_str, _VALUE_WIDTH):>{_VALUE_WIDTH}}", style=value_style)
        line.append("  ")
        line.append(_fit(sig, sig_w), style="dim")

        # Hard safety net: never let a row wrap, regardless of glyph cell widths.
        line.truncate(self.console.width, overflow="ellipsis")
        self._print(line)

    # -- exceptional events (full-width, impossible to miss) ----------------

    def drift_halt(self, names: tuple[str, str, str], roles: tuple[str, ...]) -> None:
        body = Text()
        body.append("Sequence drift detected — a shot is missing or extra.\n\n", style="bold red")
        body.append("group   ", style="dim")
        body.append(f"{names[0]} .. {names[2]}\n")
        body.append("saw     ", style="dim")
        body.append(f"{list(roles)}", style="yellow")
        body.append("\n")
        body.append("expect  ", style="dim")
        body.append("two covers, then a runout (macro dead-wax) as shot 3", style="dim")
        body.append("\n\n")
        body.append(
            "Halting so nothing wrong gets added. Fix the folder and re-run.",
            style="bold",
        )
        self.console.print(
            Panel(body, title="✗ STOPPED", title_align="left", box=ROUNDED, border_style="red")
        )

    def leftovers(self, names: list[str]) -> None:
        body = Text()
        body.append("Trailing images don't complete a set of 3:\n\n", style="bold red")
        body.append("  " + ", ".join(names), style="yellow")
        body.append("\n\n")
        body.append("A shot is missing or extra. Fix the set and re-run.", style="bold")
        self.console.print(
            Panel(body, title="✗ INCOMPLETE SET", title_align="left", box=ROUNDED, border_style="red")
        )

    def note(self, message: str, style: str = "dim") -> None:
        self._print(Text(f"    {message}", style=style))

    # -- closing summary ----------------------------------------------------

    def summary(self) -> None:
        t = self.tally
        verb = "added" if self.commit else "would add"
        table = Table(box=ROUNDED, show_header=False, border_style="grey50", pad_edge=False)
        table.add_column(justify="right", style="bold")
        table.add_column(justify="left")

        table.add_row(Text(str(t.added), style="bright_green"), f"{verb}  [dim](incl. {t.medium} medium)[/dim]")
        table.add_row(Text(str(t.review), style="red"), "flagged for review  [dim]→ review.csv[/dim]")
        table.add_row(Text(str(t.skipped), style="grey50"), "skipped  [dim](dupes / already processed)[/dim]")
        table.add_row(Text(str(t.errors), style="bright_red" if t.errors else "grey50"), "errors")

        self.console.print()
        self.console.print(Panel(table, title="summary", title_align="left", box=ROUNDED, border_style="cyan"))
        if not self.commit:
            self.console.print(
                "[dim]Dry-run — no writes were made. Re-run with [bold]--commit[/bold] to add.[/dim]"
            )

    # -- internals ----------------------------------------------------------

    def _print(self, renderable) -> None:
        # Prints above the pinned progress bar.
        self._progress.console.print(renderable)

    def _update_tally(self, status: str) -> None:
        if status == "high":
            self.tally.added += 1
        elif status == "medium":
            self.tally.added += 1
            self.tally.medium += 1
        elif status == "review":
            self.tally.review += 1
        elif status == "skipped":
            self.tally.skipped += 1
        elif status == "error":
            self.tally.errors += 1
        if self._task is not None:
            self._progress.update(self._task, advance=1, tally=self._tally_markup())

    def _tally_markup(self) -> str:
        t = self.tally
        return (
            f"  [bright_green]✓{t.added}[/]  "
            f"[yellow]●{t.medium}[/]  "
            f"[red]⚑{t.review}[/]  "
            f"[grey50]↻{t.skipped}[/]  "
            f"[bright_red]✗{t.errors}[/]"
        )
