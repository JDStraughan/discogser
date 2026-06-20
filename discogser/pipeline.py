"""Pipeline: discover photos -> group into albums -> extract -> resolve on Discogs.

`run()` is the entry point used by the CLI. The matching ladder lives in
`Resolver`: tight signals first (barcode, runout matrix), then visual cover-art
confirmation, then a text-only guess that is flagged rather than added. Per-album
orchestration (ledger, dedupe, write, reporting) lives in `_Cataloguer`; all
console rendering is delegated to `ui.RunUI`.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from PIL import Image
from rich.console import Console

from .config import Config
from .discogs import DiscogsClient, DiscogsError, have_count
from .ledger import Ledger, album_key
from .matching import agrees, best_runout_match, front_back_agreement, is_runout_hit
from .ui import RunUI
from .vision import (
    AlbumExtraction,
    VisionExtractor,
    prepare_cover,
    prepare_cover_bytes,
    validate_group_roles,
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp"}

# Per query, only deep-fetch (release detail) this many candidates, to bound
# rate-limit spend on noisy searches.
MAX_CANDIDATES = 10
HOME_COUNTRY = "US"
# A broad-search candidate whose title agrees with the front below this (0-100)
# is dropped before guessing, to avoid wild picks.
PLAUSIBLE_TITLE_THRESHOLD = 45


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ---------------------------------------------------------------------------
# Image discovery, ordering, grouping
# ---------------------------------------------------------------------------


def _exif_capture_time(path: Path) -> float:
    """EXIF capture time (epoch seconds) for tie-breaking the filename sort;
    falls back to file mtime when EXIF is absent."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            for tag in (36867, 306):  # DateTimeOriginal, DateTime
                raw = exif.get(tag) if exif else None
                if raw:
                    try:
                        return time.mktime(time.strptime(str(raw), "%Y:%m:%d %H:%M:%S"))
                    except ValueError:
                        pass
    except Exception:
        pass
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def discover_images(folder: Path) -> list[Path]:
    return [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]


def sort_images(paths: list[Path]) -> list[Path]:
    """Sort by filename, then EXIF capture time as a tie-breaker."""
    return sorted(paths, key=lambda p: (p.name.lower(), _exif_capture_time(p)))


def group_images(
    paths: list[Path],
) -> tuple[list[tuple[Path, Path, Path]], list[Path]]:
    """Split sorted images into consecutive (front, back, runout) triples.
    Returns (triples, leftovers); leftovers are trailing images that don't
    complete a set of three."""
    full = len(paths) - len(paths) % 3
    triples = [
        (paths[i], paths[i + 1], paths[i + 2]) for i in range(0, full, 3)
    ]
    return triples, paths[full:]


# ---------------------------------------------------------------------------
# Resolution + small formatting helpers
# ---------------------------------------------------------------------------


@dataclass
class Resolution:
    confidence: Confidence
    signal: str
    release_id: int | None
    title: str | None
    discogs_url: str | None
    alternates: list[dict] = field(default_factory=list)
    is_guess: bool = False
    cover_confirmed: bool = False
    note: str = ""
    # Enriched from the chosen release detail (for display + reports).
    year: str = ""
    fmt: str = ""
    lowest_price: float | None = None
    num_for_sale: int | None = None


def format_price(price: float | None) -> str:
    """Compact money for the value column; '—' when nothing is for sale."""
    if price is None:
        return "—"
    return f"${price:,.0f}" if price >= 100 else f"${price:,.2f}"


def _release_url(release_id: int) -> str:
    return f"https://www.discogs.com/release/{release_id}"


def _candidate_url(result: dict) -> str:
    rid = result.get("id")
    return _release_url(int(rid)) if rid else ""


def _title_of(result: dict) -> str:
    return result.get("title", "") or ""


def _release_formats(release: dict) -> str:
    parts = []
    for f in release.get("formats") or []:
        name = f.get("name") or ""
        descs = ", ".join(f.get("descriptions") or [])
        seg = f"{name} ({descs})" if descs else name
        if seg:
            parts.append(seg)
    return "; ".join(parts)


# Guest-credit separators that bloat an artist string and break an exact artist
# search. Deliberately conservative: cutting on "/", ",", or "and the" would
# wreck real names (AC/DC; Earth, Wind & Fire; Sly and the Family Stone), so
# we only strip unambiguous "featuring"-style credits. Compound classical
# credits ("Composer / Orchestra") are instead handled by the title/cover tiers.
_ARTIST_CUTS = (" with ", " feat. ", " feat ", " featuring ")


def primary_artist(artist: str) -> str:
    """Reduce a printed credit to the searchable primary artist, e.g.
    'Norman Brooks with Al Goodman and His Orchestra' -> 'Norman Brooks',
    'The Swingle Singers' -> 'Swingle Singers'. Left intact when there is no
    clear guest-credit boundary."""
    a = artist.strip()
    if a.lower().startswith("the "):
        a = a[4:]
    low = a.lower()
    cut = len(a)
    for sep in _ARTIST_CUTS:
        i = low.find(sep)
        if i != -1:
            cut = min(cut, i)
    return a[:cut].strip() or artist.strip()


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class Resolver:
    """Matches an extracted album to a Discogs release, tight-to-loose:
    barcode/runout (exact pressing) -> cover-art confirmation (right album) ->
    text-only guess (flagged, not added)."""

    MAX_COVER_CANDIDATES = 8   # covers compared per vision call
    MAX_COVER_BATCHES = 2      # vision calls before conceding to a guess
    MAX_POOL = 40              # candidate releases pooled across search angles

    def __init__(
        self,
        client: DiscogsClient,
        extractor: VisionExtractor | None = None,
        cover_match: bool = True,
    ) -> None:
        self._client = client
        self._extractor = extractor
        self._cover_match = cover_match and extractor is not None
        self._front_path: Path | None = None

    def resolve(self, ext: AlbumExtraction, front_path: Path | None = None) -> Resolution:
        self._front_path = front_path
        return self._enrich(self._resolve(ext))

    def _resolve(self, ext: AlbumExtraction) -> Resolution:
        back = ext.back
        pa = primary_artist(ext.front.artist)

        # Barcode is near-unique: a single hit (or a runout match) is exact.
        if back.barcode:
            results = self._client.search(barcode=back.barcode)
            if results:
                return self._from_barcode(results, ext)

        # A single agreeing catno+artist hit is strong on its own; otherwise its
        # results seed the broader cover/guess pool below.
        seeds: list[dict] = []
        if back.catalog_number and pa:
            results = self._client.search(catno=back.catalog_number, artist=pa)
            if results:
                strong = self._from_catno(results, ext)
                if strong is not None:
                    return strong
                seeds = results

        return self._confirm_or_guess(ext, seeds)

    def _from_barcode(self, results: list[dict], ext: AlbumExtraction) -> Resolution:
        hit = self._runout_match(results, ext)
        if hit is not None:
            return hit
        if len(results) == 1:
            r = results[0]
            return Resolution(
                Confidence.HIGH, "barcode exact", int(r["id"]),
                _title_of(r), _candidate_url(r),
            )
        chosen = self._highest_have(results)
        return Resolution(
            Confidence.MEDIUM, "barcode (multiple)", int(chosen["id"]),
            _title_of(chosen), _candidate_url(chosen),
            alternates=_alternates(results, chosen),
            note="Barcode matched several releases; chose the most-held.",
        )

    def _from_catno(self, results: list[dict], ext: AlbumExtraction) -> Resolution | None:
        """Strong catno outcomes only (runout match, or a single agreeing hit).
        Returns None when ambiguous, to fall through to cover/guess."""
        hit = self._runout_match(results, ext)
        if hit is not None:
            return hit
        if len(results) == 1 and agrees(
            ext.front.artist, ext.front.title, _title_of(results[0])
        ):
            r = results[0]
            return Resolution(
                Confidence.MEDIUM, "catno + artist (single)", int(r["id"]),
                _title_of(r), _candidate_url(r),
                note="Single catno candidate; front/back agree.",
            )
        return None

    def _confirm_or_guess(self, ext: AlbumExtraction, seeds: list[dict]) -> Resolution:
        """Pool candidates across every search angle, then resolve them
        tight-to-loose: runout match -> cover-art confirmation -> text guess."""
        pool = self._pool_candidates(seeds, ext)
        if not pool:
            return _not_found(ext)

        hit = self._runout_match(pool, ext)
        if hit is not None:
            return hit

        confirmed = self._cover_confirm(pool, ext)
        if confirmed:
            chosen = self._highest_have(confirmed)
            return Resolution(
                Confidence.MEDIUM, "cover match", int(chosen["id"]),
                _title_of(chosen), _candidate_url(chosen),
                alternates=_alternates(pool, chosen), cover_confirmed=True,
                note="Front cover visually confirmed; exact pressing may differ.",
            )
        return self._guess(pool, ext)

    # -- candidate gathering -----------------------------------------------

    def _pool_candidates(self, seeds: list[dict], ext: AlbumExtraction) -> list[dict]:
        """Union of `seeds` and several broad searches, deduped by release id,
        so the cover-matcher sees the right artwork even when one query missed."""
        pa = primary_artist(ext.front.artist)
        title = ext.front.title
        queries: list[dict[str, str]] = []
        if ext.back.catalog_number:
            queries.append({"catno": ext.back.catalog_number})
        if pa and title:
            queries.append({"artist": pa, "release_title": title})
        if title:
            queries.append({"release_title": title, "format": "Vinyl"})
            queries.append({"release_title": title})
            queries.append({"q": f"{pa} {title}".strip()})
            queries.append({"q": f"{ext.front.artist} {title}".strip()})
            queries.append({"q": title})

        pool = list(seeds)
        seen = {r.get("id") for r in pool}
        for params in queries:
            if not any(params.values()):
                continue
            try:
                results = self._client.search(**params)
            except DiscogsError:
                continue
            for r in results:
                rid = r.get("id")
                if rid is not None and rid not in seen:
                    seen.add(rid)
                    pool.append(r)
            if len(pool) >= self.MAX_POOL:
                break
        return pool[: self.MAX_POOL]

    # -- runout matching ----------------------------------------------------

    def _runout_match(self, results: list[dict], ext: AlbumExtraction) -> Resolution | None:
        """HIGH resolution if any candidate's Matrix/Runout identifiers match the
        transcribed dead-wax above threshold."""
        best = None  # (RunoutMatch, result) — kept together so they can't diverge
        for result in results[:MAX_CANDIDATES]:
            rid = result.get("id")
            if rid is None:
                continue
            try:
                release = self._client.get_release(int(rid))
            except DiscogsError:
                continue
            match = best_runout_match(ext.runout.matrix, release.get("identifiers", []))
            if match is not None and (best is None or match.score > best[0].score):
                best = (match, result)
        if best is None or not is_runout_hit(best[0]):
            return None
        match, result = best
        return Resolution(
            Confidence.HIGH, f"runout match ({match.score:.0f})",
            int(result["id"]), _title_of(result),
            _candidate_url(result), alternates=_alternates(results, result),
        )

    # -- cover matching -----------------------------------------------------

    def _cover_confirm(self, pool: list[dict], ext: AlbumExtraction) -> list[dict]:
        """Show the model distinct candidate covers in batches (most title-likely
        first; the vision is the filter, not the text) and return every pooled
        release sharing a confirmed cover. Best-effort: any failure -> []."""
        if not self._cover_match or self._front_path is None:
            return []
        assert self._extractor is not None  # implied by self._cover_match

        ranked = []
        seen_urls: set[str] = set()
        for r in pool:
            url = _cover_url(r)
            if url and url not in seen_urls:
                seen_urls.add(url)
                score = front_back_agreement(ext.front.artist, ext.front.title, _title_of(r))
                ranked.append((score, r, url))
        ranked.sort(key=lambda t: t[0], reverse=True)
        if not ranked:
            return []

        try:
            front_b64 = prepare_cover(self._front_path)
        except Exception:
            return []

        n = self.MAX_COVER_CANDIDATES
        for start in range(0, min(len(ranked), n * self.MAX_COVER_BATCHES), n):
            reps, thumbs = [], []
            for _score, r, url in ranked[start : start + n]:
                data = self._client.fetch_image(url)
                if not data:
                    continue
                try:
                    thumbs.append(prepare_cover_bytes(data))
                    reps.append(r)
                except Exception:
                    continue
            if not thumbs:
                continue
            try:
                indices = self._extractor.match_covers(front_b64, thumbs)
            except Exception:
                continue
            matched = [reps[i] for i in indices if 0 <= i < len(reps)]
            if matched:
                matched_urls = {_cover_url(m) for m in matched}
                return [r for r in pool if _cover_url(r) in matched_urls] or matched
        return []

    # -- guessing -----------------------------------------------------------

    def _guess(self, pool: list[dict], ext: AlbumExtraction) -> Resolution:
        """Text-only best guess (LOW, flagged not added): the master's most-held
        US version if available, else the most-held plausible candidate."""
        plausible = self._plausible(pool, ext)
        master_id = next((r.get("master_id") for r in plausible if r.get("master_id")), None)
        version = None
        if master_id:
            try:
                version = _pick_version(self._client.get_master_versions(int(master_id)))
            except DiscogsError:
                version = None

        if version is not None:
            rid = int(version["id"])
            return Resolution(
                Confidence.LOW, "master versions fallback", rid,
                _title_of(version) or _title_of(plausible[0]), _release_url(rid),
                alternates=_alternates(plausible, plausible[0]), is_guess=True,
                note="Most-held version (US-biased); unverified by runout or cover.",
            )

        chosen = self._highest_have(plausible)
        return Resolution(
            Confidence.LOW, "ambiguous (best guess)", int(chosen["id"]),
            _title_of(chosen), _candidate_url(chosen),
            alternates=_alternates(plausible, chosen), is_guess=True,
            note="Multiple candidates; none confirmed by runout or cover.",
        )

    def _plausible(self, results: list[dict], ext: AlbumExtraction) -> list[dict]:
        """Drop obviously-unrelated hits before guessing; keep all if that would
        empty the list."""
        keep = [
            r for r in results
            if front_back_agreement(ext.front.artist, ext.front.title, _title_of(r))
            >= PLAUSIBLE_TITLE_THRESHOLD
        ]
        return keep or results

    def _highest_have(self, results: list[dict]) -> dict:
        """The most-held release among the top candidates (search hits don't
        carry 'have', so fetch detail for a bounded few)."""
        best, best_have = results[0], -1
        for result in results[:MAX_CANDIDATES]:
            rid = result.get("id")
            if rid is None:
                continue
            try:
                have = have_count(self._client.get_release(int(rid)))
            except DiscogsError:
                continue
            if have > best_have:
                best, best_have = result, have
        return best

    def _enrich(self, res: Resolution) -> Resolution:
        """Attach year, format, and marketplace value from the chosen release
        detail (cached — usually already fetched during resolution)."""
        if res.release_id is None:
            return res
        try:
            rel = self._client.get_release(res.release_id)
        except DiscogsError:
            return res
        res.year = str(rel["year"]) if rel.get("year") else ""
        res.fmt = _release_formats(rel)
        res.lowest_price = rel.get("lowest_price")
        res.num_for_sale = rel.get("num_for_sale")
        return res


def _not_found(ext: AlbumExtraction) -> Resolution:
    title = f"{ext.front.artist} – {ext.front.title}".strip(" –")
    return Resolution(
        Confidence.LOW, "not found", None, title, None,
        note="No Discogs candidates from barcode, catno, artist/title, or broad search.",
    )


def _cover_url(result: dict) -> str:
    return result.get("cover_image") or result.get("thumb") or ""


def _pick_version(versions: list[dict]) -> dict | None:
    if not versions:
        return None

    def key(v: dict) -> tuple[int, int]:
        home = (v.get("country") or "").strip().upper() in (HOME_COUNTRY, "USA")
        return (int(home), have_count(v))

    return max(versions, key=key)


def _alternates(results: list[dict], chosen: dict, limit: int = 5) -> list[dict]:
    alts = []
    for r in results:
        if r.get("id") == chosen.get("id"):
            continue
        alts.append({"id": r.get("id"), "title": _title_of(r), "url": _candidate_url(r)})
        if len(alts) >= limit:
            break
    return alts


# ---------------------------------------------------------------------------
# Per-album orchestration
# ---------------------------------------------------------------------------


class _Cataloguer:
    """Runs one album at a time through extract -> validate -> resolve -> add,
    accumulating CSV rows and driving the UI/ledger. `process` returns False
    only to halt the whole run (sequence drift)."""

    def __init__(
        self,
        *,
        client: DiscogsClient,
        ledger: Ledger,
        resolver: Resolver,
        extractor: VisionExtractor,
        ui: RunUI,
        owned: set[int],
        folder_id: int,
        commit: bool,
    ) -> None:
        self._client = client
        self._ledger = ledger
        self._resolver = resolver
        self._extractor = extractor
        self._ui = ui
        self._owned = owned
        self._folder_id = folder_id
        self._commit = commit
        self.results_rows: list[dict] = []
        self.review_rows: list[dict] = []

    def process(self, group: tuple[Path, Path, Path]) -> bool:
        key = album_key(group)

        if self._ledger.is_committed(key):
            prior = self._ledger.get(key)
            self._ui.album(
                status="skipped", artist="", title=prior.title or "(already added)",
                release_id=prior.release_id, signal="already added", committed=True,
            )
            return True

        try:
            ext = self._extractor.extract(*group)
        except Exception as exc:
            self._terminal(
                key, group, None, status="error",
                artist=f"{group[0].stem}..{group[2].stem}", title="",
                release_id=None, signal=f"vision failed: {exc}",
                data={"images": [p.name for p in group]},
            )
            return True

        # Sequence-integrity gate: shot 3 must be a runout, shots 1-2 covers.
        # A miss means a dropped/extra shot drifted the grouping — halt rather
        # than catalog wrong records.
        if not validate_group_roles(ext.image_roles):
            self._ui.drift_halt(
                (group[0].name, group[1].name, group[2].name), ext.image_roles
            )
            return False

        try:
            # group[0] is the physical front shot (per the capture contract),
            # even if vision labeled it a back — use it for cover confirmation.
            res = self._resolver.resolve(ext, front_path=group[0])
        except DiscogsError as exc:
            self._terminal(
                key, group, ext, status="error", artist=ext.front.artist or "Unknown",
                title=ext.front.title, release_id=None, signal=f"discogs failed: {exc}",
            )
            return True

        if res.release_id is not None and res.release_id in self._owned:
            self._terminal(
                key, group, ext, res=res, status="skipped", artist=ext.front.artist,
                title=res.title or ext.front.title, release_id=res.release_id,
                signal="already in collection", value=format_price(res.lowest_price),
            )
            return True

        # Only exact (HIGH) and cover-confirmed/strong (MEDIUM) results are added;
        # a text-only guess is flagged for review. Write first so a failed add
        # surfaces as an error row, not a misleading tick.
        will_add = res.confidence in (Confidence.HIGH, Confidence.MEDIUM)
        committed, add_error = self._add(res, will_add)
        if add_error:
            self._terminal(
                key, group, ext, res=res, status="error",
                artist=ext.front.artist or "Unknown", title=res.title or ext.front.title,
                release_id=res.release_id, signal=f"add failed: {add_error}",
            )
            return True

        self._terminal(
            key, group, ext, res=res, status=_status_for(res),
            artist=ext.front.artist or "Unknown", title=res.title or ext.front.title,
            release_id=res.release_id,
            signal=res.signal + (" · guess" if res.is_guess else ""),
            committed=committed, value=format_price(res.lowest_price),
            results_row=_results_row(ext, res, group),
            review_row=None if will_add else _review_row(ext, res, group),
        )
        return True

    def _add(self, res: Resolution, will_add: bool) -> tuple[bool, str | None]:
        if not (will_add and self._commit and res.release_id is not None):
            return False, None
        try:
            self._client.add_to_collection(self._folder_id, res.release_id)
            self._owned.add(res.release_id)
            return True, None
        except DiscogsError as exc:
            return False, str(exc)

    def _terminal(
        self,
        key: str,
        group: tuple[Path, Path, Path],
        ext: AlbumExtraction | None,
        *,
        status: str,
        artist: str,
        title: str,
        release_id: int | None,
        signal: str,
        committed: bool = False,
        value: str = "—",
        res: Resolution | None = None,
        data: dict | None = None,
        results_row: dict | None = None,
        review_row: dict | None = None,
    ) -> None:
        self._ui.album(
            status=status, artist=artist, title=title, release_id=release_id,
            signal=signal, committed=committed, value=value,
        )
        self._ledger.record(
            key, status=status, release_id=release_id, title=title,
            confidence=res.confidence.value if res else None,
            signal=signal, committed=committed,
            data=data if data is not None else _result_data(ext, group, res),
        )
        if results_row is not None:
            self.results_rows.append(results_row)
        if review_row is not None:
            self.review_rows.append(review_row)


def _status_for(res: Resolution) -> str:
    if res.confidence == Confidence.HIGH:
        return "high"
    if res.cover_confirmed:
        return "cover"
    if res.confidence == Confidence.MEDIUM:
        return "medium"
    if res.is_guess and res.release_id is not None:
        return "guess"
    return "review"


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def run(
    photos_dir: Path,
    *,
    config: Config,
    commit: bool,
    folder_name: str | None,
    cover_match: bool = True,
    console: Console | None = None,
) -> int:
    """Process a folder of photos. Returns a process exit code (0 = ok)."""
    console = console or Console()

    if not photos_dir.is_dir():
        console.print(f"[red]Not a directory:[/red] {photos_dir}")
        return 2

    images = sort_images(discover_images(photos_dir))
    if not images:
        console.print(f"[yellow]No images found in[/yellow] {photos_dir}")
        return 0

    groups, leftovers = group_images(images)
    ui = RunUI(console, total=len(groups), commit=commit)
    if leftovers:
        ui.leftovers([p.name for p in leftovers])
        return 1

    with DiscogsClient(
        token=config.discogs_token,
        username=config.discogs_username,
        user_agent=config.user_agent,
    ) as client, Ledger() as ledger:
        target_folder = folder_name or config.discogs_folder
        try:
            folder_id = client.resolve_folder_id(target_folder)
            owned = client.get_collection_release_ids()
        except DiscogsError as exc:
            console.print(f"[red]Discogs setup failed:[/red] {exc}")
            return 2

        extractor = VisionExtractor(
            api_key=config.anthropic_api_key, model=config.anthropic_model
        )
        cataloguer = _Cataloguer(
            client=client,
            ledger=ledger,
            resolver=Resolver(client, extractor=extractor, cover_match=cover_match),
            extractor=extractor,
            ui=ui,
            owned=owned,
            folder_id=folder_id,
            commit=commit,
        )

        with ui:
            ui.header(target_folder, folder_id, len(owned))
            drifted = False
            for group in groups:
                if not cataloguer.process(group):
                    drifted = True  # sequence drift — already reported
                    break
            # Always write what we processed, even on a halt.
            _write_results_csv(photos_dir, cataloguer.results_rows)
            _write_review_csv(photos_dir, cataloguer.review_rows)
            if drifted:
                return 1
            ui.summary()

    return 0


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def _price_field(price: float | None) -> str:
    return "" if price is None else f"{price:.2f}"


def _result_data(
    ext: AlbumExtraction | None, group: tuple[Path, Path, Path], res: Resolution | None
) -> dict:
    data: dict = {"images": [p.name for p in group]}
    if ext is not None:
        data["front"] = {"artist": ext.front.artist, "title": ext.front.title}
        data["back"] = {
            "label": ext.back.label,
            "catno": ext.back.catalog_number,
            "barcode": ext.back.barcode,
            "format": ext.back.format,
            "country": ext.back.country,
            "year": ext.back.year,
        }
        data["runout"] = {
            "matrix": ext.runout.matrix,
            "confidence": ext.runout.confidence,
            "illegible": ext.runout.illegible,
        }
    if res is not None:
        data["resolution"] = {
            "confidence": res.confidence.value,
            "signal": res.signal,
            "release_id": res.release_id,
            "url": res.discogs_url,
            "is_guess": res.is_guess,
            "cover_confirmed": res.cover_confirmed,
            "note": res.note,
            "year": res.year,
            "format": res.fmt,
            "lowest_price": res.lowest_price,
            "num_for_sale": res.num_for_sale,
        }
    return data


def _results_row(ext: AlbumExtraction, res: Resolution, group: tuple[Path, Path, Path]) -> dict:
    return {
        "artist": ext.front.artist,
        "title": res.title or ext.front.title,
        "year": res.year,
        "format": res.fmt,
        "value_usd": _price_field(res.lowest_price),
        "num_for_sale": "" if res.num_for_sale is None else res.num_for_sale,
        "release_id": res.release_id or "",
        "confidence": res.confidence.value,
        "signal": res.signal,
        "discogs_url": res.discogs_url or "",
        "alternates": "; ".join(a["url"] for a in res.alternates),
        "is_guess": "yes" if res.is_guess else "",
        "cover_confirmed": "yes" if res.cover_confirmed else "",
        "runout_matrix": ext.runout.matrix,
        "runout_confidence": ext.runout.confidence,
        "images": "; ".join(p.name for p in group),
        "note": res.note,
    }


def _review_row(ext: AlbumExtraction, res: Resolution, group: tuple[Path, Path, Path]) -> dict:
    return {
        "artist": ext.front.artist,
        "title": res.title or ext.front.title,
        "value_usd": _price_field(res.lowest_price),
        "best_candidate_url": res.discogs_url or "",
        "release_id": res.release_id or "",
        "signal": res.signal,
        "runout_matrix": ext.runout.matrix,
        "runout_confidence": ext.runout.confidence,
        "barcode": ext.back.barcode,
        "catno": ext.back.catalog_number,
        "alternates": "; ".join(a["url"] for a in res.alternates),
        "images": "; ".join(p.name for p in group),
        "note": res.note,
    }


_RESULTS_FIELDS = [
    "artist", "title", "year", "format", "value_usd", "num_for_sale",
    "release_id", "confidence", "signal", "discogs_url", "alternates",
    "is_guess", "cover_confirmed", "runout_matrix", "runout_confidence",
    "images", "note",
]
_REVIEW_FIELDS = [
    "artist", "title", "value_usd", "best_candidate_url", "release_id", "signal",
    "runout_matrix", "runout_confidence", "barcode", "catno", "alternates",
    "images", "note",
]


def _write_results_csv(photos_dir: Path, rows: list[dict]) -> None:
    _write_csv(photos_dir / "results.csv", _RESULTS_FIELDS, rows)


def _write_review_csv(photos_dir: Path, rows: list[dict]) -> None:
    _write_csv(photos_dir / "review.csv", _REVIEW_FIELDS, rows)


# Leading characters a spreadsheet may interpret as a formula (CSV injection).
_CSV_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: object) -> str:
    """Neutralise spreadsheet formula injection: a cell beginning with a
    formula trigger is prefixed with a quote so it's treated as text."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in _CSV_FORMULA_LEADS else s


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _csv_cell(row.get(k)) for k in fields})
