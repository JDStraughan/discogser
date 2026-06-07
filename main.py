"""Pipeline orchestration: discover -> group -> extract -> search -> resolve.

Public entry point is `run(...)`, called by catalog.py. All console rendering is
delegated to ui.RunUI so this module stays focused on the pipeline.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from rich.console import Console

from config import Config
from discogs import DiscogsClient, DiscogsError, have_count
from ledger import Ledger, album_key
from matching import agrees, best_runout_match, front_back_agreement, is_runout_hit
from ui import RunUI
from vision import (
    AlbumExtraction,
    VisionExtractor,
    prepare_cover,
    prepare_cover_bytes,
    validate_group_roles,
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp"}

# Cap how many search candidates we deep-fetch for runout disambiguation, to
# avoid burning the rate limit on a noisy query.
MAX_CANDIDATES = 10
# US bias for the master-versions fallback.
HOME_COUNTRY = "US"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ---------------------------------------------------------------------------
# Image discovery, ordering, grouping
# ---------------------------------------------------------------------------


def _exif_capture_time(path: Path) -> float:
    """Best-effort EXIF capture timestamp (seconds) for tiebreaking; falls back
    to file mtime when EXIF is absent."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            exif = img.getexif()
            if exif:
                # 36867 = DateTimeOriginal, 306 = DateTime
                for tag in (36867, 306):
                    raw = exif.get(tag)
                    if raw:
                        import time as _time

                        try:
                            parsed = _time.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
                            return _time.mktime(parsed)
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
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]


def sort_images(paths: list[Path]) -> list[Path]:
    """Sort by filename, then EXIF capture time as a tiebreaker."""
    return sorted(paths, key=lambda p: (p.name.lower(), _exif_capture_time(p)))


def group_images(paths: list[Path]) -> tuple[list[tuple[Path, Path, Path]], list[Path]]:
    """Group consecutive sorted images into sets of 3. Returns (groups,
    leftovers) where leftovers is any trailing 1-2 images that don't complete a
    set."""
    groups: list[tuple[Path, Path, Path]] = []
    full = len(paths) - (len(paths) % 3)
    for i in range(0, full, 3):
        groups.append((paths[i], paths[i + 1], paths[i + 2]))
    leftovers = list(paths[full:])
    return groups, leftovers


# ---------------------------------------------------------------------------
# Resolution
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
    """Compact money for the value column. '—' when nothing is for sale."""
    if price is None:
        return "—"
    try:
        p = float(price)
    except (TypeError, ValueError):
        return "—"
    return f"${p:,.0f}" if p >= 100 else f"${p:,.2f}"


def _format_release_formats(release: dict) -> str:
    parts: list[str] = []
    for f in release.get("formats", []) or []:
        name = f.get("name") or ""
        descs = ", ".join(f.get("descriptions") or [])
        seg = f"{name} ({descs})" if descs else name
        if seg:
            parts.append(seg)
    return "; ".join(parts)


def _release_url(release_id: int) -> str:
    return f"https://www.discogs.com/release/{release_id}"


def _candidate_url(result: dict) -> str:
    rid = result.get("id")
    return _release_url(int(rid)) if rid else ""


def _title_of(result: dict) -> str:
    return result.get("title", "") or ""


# Credit phrases that bloat an artist string and break an exact artist search.
_ARTIST_CUTS = (
    " with ", " feat. ", " feat ", " featuring ",
    " and his ", " and her ", " and the ",
)


def primary_artist(artist: str) -> str:
    """Reduce a printed credit to the searchable primary artist.

    'Norman Brooks with Al Goodman and His Orchestra' -> 'Norman Brooks'
    'Rimsky-Korsakov / L'Orchestre de la ...'         -> 'Rimsky-Korsakov'
    'The Swingle Singers'                              -> 'Swingle Singers'
    """
    a = artist.strip()
    if a.lower().startswith("the "):
        a = a[4:]
    low = a.lower()
    cut = len(a)
    for sep in _ARTIST_CUTS:
        i = low.find(sep)
        if i != -1:
            cut = min(cut, i)
    for ch in ("/", ","):  # keep '&' — Discogs uses it (e.g. Simon & Garfunkel)
        i = a.find(ch)
        if i > 0:
            cut = min(cut, i)
    return a[:cut].strip() or artist.strip()


class Resolver:
    """Runs the Discogs search priority ladder and disambiguates to an exact
    pressing using the runout, falling back to master versions."""

    # How many distinct candidate covers to send to the cover-matcher.
    MAX_COVER_CANDIDATES = 4

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
        res = self._resolve(ext)
        return self._enrich(res)

    def _enrich(self, res: Resolution) -> Resolution:
        """Pull year, format, and marketplace value from the chosen release
        detail (cached — usually already fetched during disambiguation)."""
        if res.release_id is None:
            return res
        try:
            rel = self._client.get_release(res.release_id)
        except DiscogsError:
            return res
        year = rel.get("year")
        res.year = str(year) if year else ""
        res.fmt = _format_release_formats(rel)
        res.lowest_price = rel.get("lowest_price")
        res.num_for_sale = rel.get("num_for_sale")
        return res

    def _resolve(self, ext: AlbumExtraction) -> Resolution:
        artist = ext.front.artist
        title = ext.front.title
        back = ext.back
        pa = primary_artist(artist)

        # (a) barcode exact
        if back.barcode:
            results = self._client.search(barcode=back.barcode)
            if results:
                return self._from_barcode(results, ext)

        # (b) catno + artist
        if back.catalog_number and pa:
            results = self._client.search(catno=back.catalog_number, artist=pa)
            if results:
                resolved = self._from_catno(results, ext)
                if resolved is not None:
                    return resolved

        # (c) artist + release_title + format=Vinyl
        if pa and title:
            results = self._client.search(
                artist=pa, release_title=title, format="Vinyl"
            )
            if results:
                resolved = self._from_title(results, ext)
                if resolved is not None:
                    return resolved

        # (d) progressively looser fallbacks — recall over precision. Each feeds
        # the same runout-then-guess machinery, so a hit here is a HIGH if the
        # runout confirms it, otherwise a LOW guess (added only in --guess mode).
        fallbacks: list[dict[str, str]] = []
        if back.catalog_number:
            fallbacks.append({"catno": back.catalog_number})          # catno alone
        if pa and title:
            fallbacks.append({"artist": pa, "release_title": title})  # drop format filter
        if title:
            fallbacks.append({"release_title": title, "format": "Vinyl"})  # title only
            fallbacks.append({"q": f"{pa} {title}".strip()})          # broad full-text
            fallbacks.append({"q": f"{artist} {title}".strip()})      # broad, raw artist
        for params in fallbacks:
            results = self._client.search(**params)
            if results:
                resolved = self._from_title(results, ext)
                if resolved is not None:
                    return resolved

        # nothing found
        return Resolution(
            confidence=Confidence.LOW,
            signal="not found",
            release_id=None,
            title=f"{artist} – {title}".strip(" –"),
            discogs_url=None,
            note="No Discogs candidates matched barcode, catno, artist/title, or broad search.",
        )

    # -- per-strategy resolution -------------------------------------------

    def _disambiguate_by_runout(self, results: list[dict], ext: AlbumExtraction):
        """Fetch candidate release details and find the best runout match.
        Returns (best_result, best_match) or (None, None)."""
        best_result = None
        best_match = None
        for result in results[:MAX_CANDIDATES]:
            rid = result.get("id")
            if rid is None:
                continue
            try:
                release = self._client.get_release(int(rid))
            except DiscogsError:
                continue
            match = best_runout_match(
                ext.runout.matrix, release.get("identifiers", [])
            )
            if match is None:
                continue
            if best_match is None or match.score > best_match.score:
                best_match = match
                best_result = result
        return best_result, best_match

    def _from_barcode(self, results: list[dict], ext: AlbumExtraction) -> Resolution:
        # Barcode is already a strong, near-unique signal -> HIGH.
        best_result, best_match = self._disambiguate_by_runout(results, ext)
        if is_runout_hit(best_match):
            chosen = best_result
            return Resolution(
                confidence=Confidence.HIGH,
                signal=f"barcode + runout match ({best_match.score:.0f})",
                release_id=int(chosen["id"]),
                title=_title_of(chosen),
                discogs_url=_candidate_url(chosen),
                alternates=_alternates(results, chosen),
            )
        if len(results) == 1:
            chosen = results[0]
            return Resolution(
                confidence=Confidence.HIGH,
                signal="barcode exact",
                release_id=int(chosen["id"]),
                title=_title_of(chosen),
                discogs_url=_candidate_url(chosen),
            )
        # Multiple barcode hits, runout didn't resolve: usually format/edition
        # variants of the same release. Pick highest community 'have'; MEDIUM.
        chosen = self._highest_have(results)
        return Resolution(
            confidence=Confidence.MEDIUM,
            signal="barcode (multiple, runout unresolved)",
            release_id=int(chosen["id"]),
            title=_title_of(chosen),
            discogs_url=_candidate_url(chosen),
            alternates=_alternates(results, chosen),
            note="Barcode matched several releases; chose highest 'have' count.",
        )

    def _from_catno(self, results: list[dict], ext: AlbumExtraction) -> Resolution | None:
        best_result, best_match = self._disambiguate_by_runout(results, ext)
        if is_runout_hit(best_match):
            chosen = best_result
            return Resolution(
                confidence=Confidence.HIGH,
                signal=f"catno + runout match ({best_match.score:.0f})",
                release_id=int(chosen["id"]),
                title=_title_of(chosen),
                discogs_url=_candidate_url(chosen),
                alternates=_alternates(results, chosen),
            )
        if len(results) == 1 and agrees(
            ext.front.artist, ext.front.title, _title_of(results[0])
        ):
            chosen = results[0]
            return Resolution(
                confidence=Confidence.MEDIUM,
                signal="catno + artist (single, runout unread)",
                release_id=int(chosen["id"]),
                title=_title_of(chosen),
                discogs_url=_candidate_url(chosen),
                note="Single strong catno candidate; front/back agree.",
            )
        # Ambiguous — confirm by cover art, else guess.
        return self._confirm_or_guess(results, ext)

    def _from_title(self, results: list[dict], ext: AlbumExtraction) -> Resolution | None:
        best_result, best_match = self._disambiguate_by_runout(results, ext)
        if is_runout_hit(best_match):
            chosen = best_result
            return Resolution(
                confidence=Confidence.HIGH,
                signal=f"artist/title + runout match ({best_match.score:.0f})",
                release_id=int(chosen["id"]),
                title=_title_of(chosen),
                discogs_url=_candidate_url(chosen),
                alternates=_alternates(results, chosen),
            )
        return self._confirm_or_guess(results, ext)

    def _confirm_or_guess(
        self, results: list[dict], ext: AlbumExtraction
    ) -> Resolution | None:
        """Tier 2/3: the runout didn't confirm the pressing. Try to confirm the
        *album* visually by cover art (-> MEDIUM, right album). Failing that,
        fall back to a text-only best guess (-> LOW)."""
        plausible = self._plausible(results, ext)
        confirmed = self._cover_confirm(plausible, ext)
        if confirmed:
            chosen = self._highest_have(confirmed)  # best pressing among matches
            return Resolution(
                confidence=Confidence.MEDIUM,
                signal="cover match",
                release_id=int(chosen["id"]),
                title=_title_of(chosen),
                discogs_url=_candidate_url(chosen),
                alternates=_alternates(results, chosen),
                cover_confirmed=True,
                note="Front cover visually confirmed; exact pressing may differ.",
            )
        return self._versions_fallback(results, ext)

    def _cover_confirm(
        self, results: list[dict], ext: AlbumExtraction
    ) -> list[dict]:
        """Return the subset of candidates whose cover art the model confirms
        matches the photographed front. Best-effort: any failure -> []."""
        if not self._cover_match or self._front_path is None or self._extractor is None:
            return []
        # Collect up to N distinct candidate covers.
        picked: list[dict] = []
        thumbs: list[str] = []
        seen: set[str] = set()
        for r in results:
            url = r.get("cover_image") or r.get("thumb") or ""
            if not url or url in seen:
                continue
            data = self._client.fetch_image(url)
            if not data:
                continue
            try:
                thumbs.append(prepare_cover_bytes(data))
            except Exception:
                continue
            seen.add(url)
            picked.append(r)
            if len(picked) >= self.MAX_COVER_CANDIDATES:
                break
        if not picked:
            return []
        try:
            front_b64 = prepare_cover(self._front_path)
            verdict = self._extractor.match_covers(front_b64, thumbs)
        except Exception:
            return []
        return [picked[i] for i in verdict.matches if 0 <= i < len(picked)]

    def _plausible(self, results: list[dict], ext: AlbumExtraction) -> list[dict]:
        """Drop obviously-unrelated broad-search hits before guessing: keep only
        candidates whose title is at least loosely consistent with the front."""
        keep = [
            r
            for r in results
            if front_back_agreement(ext.front.artist, ext.front.title, _title_of(r)) >= 45
        ]
        return keep or results

    def _versions_fallback(
        self, results: list[dict], ext: AlbumExtraction
    ) -> Resolution | None:
        """No runout resolution: pick the master's most-common version, US-
        biased, and mark it a guess (LOW — only added in --guess mode)."""
        plausible = self._plausible(results, ext)
        master_id = next(
            (r.get("master_id") for r in plausible if r.get("master_id")), None
        )
        candidate = None
        if master_id:
            try:
                versions = self._client.get_master_versions(int(master_id))
            except DiscogsError:
                versions = []
            candidate = _pick_version(versions)

        if candidate is None:
            # Fall back to the highest-have plausible result as the best guess.
            chosen = self._highest_have(plausible)
            return Resolution(
                confidence=Confidence.LOW,
                signal="ambiguous (best guess)",
                release_id=int(chosen["id"]),
                title=_title_of(chosen),
                discogs_url=_candidate_url(chosen),
                alternates=_alternates(results, chosen),
                is_guess=True,
                note="Runout did not resolve; multiple candidates remain.",
            )

        rid = int(candidate["id"])
        return Resolution(
            confidence=Confidence.LOW,
            signal="master versions fallback (guess)",
            release_id=rid,
            title=_title_of(candidate) or _title_of(results[0]),
            discogs_url=_release_url(rid),
            alternates=_alternates(results, results[0]),
            is_guess=True,
            note="Picked highest-'have' version (US-biased); unverified by runout.",
        )

    def _highest_have(self, results: list[dict]) -> dict:
        # Search results don't carry 'have'; fetch details for the top few.
        best = results[0]
        best_have = -1
        for result in results[:MAX_CANDIDATES]:
            rid = result.get("id")
            if rid is None:
                continue
            try:
                release = self._client.get_release(int(rid))
            except DiscogsError:
                continue
            h = have_count(release)
            if h > best_have:
                best_have = h
                best = result
        return best


def _pick_version(versions: list[dict]) -> dict | None:
    if not versions:
        return None

    def key(v: dict) -> tuple[int, int]:
        country = (v.get("country") or "")
        home = 1 if country.strip().upper() in (HOME_COUNTRY, "US", "USA") else 0
        return (home, have_count(v))

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
# Run
# ---------------------------------------------------------------------------


def run(
    photos_dir: Path,
    *,
    config: Config,
    commit: bool,
    folder_name: str | None,
    guess: bool = False,
    cover_match: bool = True,
    console: Console | None = None,
) -> int:
    """Process a folder of photos. Returns a process exit code (0 ok)."""
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

    results_rows: list[dict] = []
    review_rows: list[dict] = []

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
        resolver = Resolver(client, extractor=extractor, cover_match=cover_match)

        with ui:
            ui.header(target_folder, folder_id, len(owned))

            for group in groups:
                key = album_key(group)

                # Idempotent: skip albums we already committed.
                if ledger.is_committed(key):
                    existing = ledger.get(key)
                    ui.album(
                        status="skipped",
                        artist="",
                        title=existing.title or "(already added)",
                        release_id=existing.release_id,
                        signal="already added",
                        committed=True,
                    )
                    continue

                try:
                    ext = extractor.extract(*group)
                except Exception as exc:  # vision/network failure for one album
                    ui.album(
                        status="error",
                        artist=f"{group[0].stem}..{group[2].stem}",
                        title="",
                        release_id=None,
                        signal=f"vision failed: {exc}",
                        committed=False,
                    )
                    ledger.record(
                        key, status="error", release_id=None, title=None,
                        confidence=None, signal=str(exc), committed=False,
                        data={"images": [p.name for p in group]},
                    )
                    continue

                # Sequence-integrity gate: a group MUST be one front, one back,
                # one runout. A mismatch means a missed/extra shot drifted the
                # grouping — stop rather than silently cataloguing wrong records.
                if not validate_group_roles(ext.image_roles):
                    ui.drift_halt(
                        (group[0].name, group[1].name, group[2].name),
                        ext.image_roles,
                    )
                    return 1

                try:
                    # group[0] is the physical front shot (per the capture
                    # contract), even if vision labeled it a back — use it for
                    # cover-art confirmation.
                    res = resolver.resolve(ext, front_path=group[0])
                except DiscogsError as exc:
                    ui.album(
                        status="error",
                        artist=ext.front.artist or "Unknown",
                        title=ext.front.title,
                        release_id=None,
                        signal=f"discogs failed: {exc}",
                        committed=False,
                    )
                    ledger.record(
                        key, status="error", release_id=None,
                        title=f"{ext.front.artist} – {ext.front.title}",
                        confidence=None, signal=str(exc), committed=False,
                        data=_result_data(ext, group, None),
                    )
                    continue

                # Dedupe against existing collection.
                if res.release_id is not None and res.release_id in owned:
                    ui.album(
                        status="skipped",
                        artist=ext.front.artist,
                        title=res.title or ext.front.title,
                        release_id=res.release_id,
                        signal="already in collection",
                        committed=False,
                        value=format_price(res.lowest_price),
                    )
                    ledger.record(
                        key, status="skipped", release_id=res.release_id,
                        title=res.title, confidence=res.confidence.value,
                        signal="already in collection", committed=False,
                        data=_result_data(ext, group, res),
                    )
                    continue

                auto_add = res.confidence in (Confidence.HIGH, Confidence.MEDIUM)
                # In --guess mode, a LOW result that still pinned a release id is
                # added as an explicit guess rather than parked in review.
                guess_add = guess and not auto_add and res.release_id is not None
                will_add = auto_add or guess_add

                # Attempt the write first (in commit mode) so a failure surfaces
                # as an error row rather than a misleading tick.
                committed = False
                add_error: str | None = None
                if will_add and commit and res.release_id is not None:
                    try:
                        client.add_to_collection(folder_id, res.release_id)
                        owned.add(res.release_id)
                        committed = True
                    except DiscogsError as exc:
                        add_error = str(exc)

                if add_error:
                    ui.album(
                        status="error",
                        artist=ext.front.artist or "Unknown",
                        title=res.title or ext.front.title,
                        release_id=res.release_id,
                        signal=f"add failed: {add_error}",
                        committed=False,
                    )
                    ledger.record(
                        key, status="error", release_id=res.release_id,
                        title=res.title, confidence=res.confidence.value,
                        signal=f"add failed: {add_error}", committed=False,
                        data=_result_data(ext, group, res),
                    )
                    continue

                if res.confidence == Confidence.HIGH:
                    status = "high"
                elif res.cover_confirmed:
                    status = "cover"
                elif res.confidence == Confidence.MEDIUM:
                    status = "medium"
                elif guess_add:
                    status = "guess"
                else:
                    status = "review"

                ui.album(
                    status=status,
                    artist=ext.front.artist or "Unknown",
                    title=res.title or ext.front.title,
                    release_id=res.release_id,
                    signal=res.signal + ("" if not res.is_guess else " · guess"),
                    committed=committed,
                    value=format_price(res.lowest_price),
                )

                results_rows.append(_results_row(ext, res, group))
                # Only things we did NOT add go to the review queue.
                if not will_add:
                    review_rows.append(_review_row(ext, res, group))

                ledger.record(
                    key,
                    status=status,
                    release_id=res.release_id, title=res.title,
                    confidence=res.confidence.value, signal=res.signal,
                    committed=committed, data=_result_data(ext, group, res),
                )

            # Reports + summary while the bar is still on screen.
            _write_results_csv(photos_dir, results_rows)
            _write_review_csv(photos_dir, review_rows)
            ui.summary()

    return 0


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------


def _result_data(ext: AlbumExtraction, group, res: Resolution | None) -> dict:
    data = {
        "images": [p.name for p in group],
        "front": {"artist": ext.front.artist, "title": ext.front.title},
        "back": {
            "label": ext.back.label,
            "catno": ext.back.catalog_number,
            "barcode": ext.back.barcode,
            "format": ext.back.format,
            "country": ext.back.country,
            "year": ext.back.year,
        },
        "runout": {
            "matrix": ext.runout.matrix,
            "confidence": ext.runout.confidence,
            "illegible": ext.runout.illegible,
        },
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


def _results_row(ext: AlbumExtraction, res: Resolution, group) -> dict:
    return {
        "artist": ext.front.artist,
        "title": res.title or ext.front.title,
        "year": res.year,
        "format": res.fmt,
        "value_usd": "" if res.lowest_price is None else f"{float(res.lowest_price):.2f}",
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


def _review_row(ext: AlbumExtraction, res: Resolution, group) -> dict:
    return {
        "artist": ext.front.artist,
        "title": res.title or ext.front.title,
        "value_usd": "" if res.lowest_price is None else f"{float(res.lowest_price):.2f}",
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


def _write_results_csv(photos_dir: Path, rows: list[dict]) -> None:
    path = photos_dir / "results.csv"
    fields = [
        "artist", "title", "year", "format", "value_usd", "num_for_sale",
        "release_id", "confidence", "signal", "discogs_url",
        "alternates", "is_guess", "cover_confirmed", "runout_matrix",
        "runout_confidence", "images", "note",
    ]
    _write_csv(path, fields, rows)


def _write_review_csv(photos_dir: Path, rows: list[dict]) -> None:
    path = photos_dir / "review.csv"
    fields = [
        "artist", "title", "value_usd", "best_candidate_url", "release_id",
        "signal", "runout_matrix", "runout_confidence", "barcode", "catno",
        "alternates", "images", "note",
    ]
    _write_csv(path, fields, rows)


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
