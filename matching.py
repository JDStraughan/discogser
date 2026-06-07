"""Runout/matrix normalization and fuzzy matching against Discogs identifiers.

A runout match is the strongest disambiguation signal: it ties the physical disc
in your hand to one exact pressing. We normalize aggressively (strip spaces,
uppercase, drop OCR-noise characters) and score with rapidfuzz, taking the best
of token-set and partial ratios so a transcription that is a subset of the
catalogued identifier (or vice versa) still scores well.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

# Above this score (0-100) a runout transcription is considered a match.
RUNOUT_MATCH_THRESHOLD = 82.0
# Below this many normalized characters, a "match" is too flimsy to trust.
MIN_RUNOUT_CHARS = 6

# Characters that survive normalization. Matrix strings are alnum plus a few
# structural marks; everything else is treated as OCR noise / decoration.
_KEEP = re.compile(r"[A-Z0-9/\-]+")


def normalize_matrix(s: str) -> str:
    """Uppercase, drop spaces and OCR noise, keep alnum + '/' + '-'."""
    if not s:
        return ""
    upper = s.upper()
    # Common OCR confusions in etched dead-wax: a leading/standalone glyph that
    # is really a plant logo, etc. We keep it simple and only retain the
    # structural character classes; fuzzy matching absorbs the rest.
    return "".join(_KEEP.findall(upper))


@dataclass(frozen=True)
class RunoutMatch:
    score: float
    matched_value: str  # the Discogs identifier value that matched best


def score_pair(extracted: str, candidate: str) -> float:
    a = normalize_matrix(extracted)
    b = normalize_matrix(candidate)
    if not a or not b:
        return 0.0
    # token_set handles reordered/space-delimited fragments; partial handles the
    # subset case (one side transcribed only part of the dead wax).
    return max(fuzz.token_set_ratio(a, b), fuzz.partial_ratio(a, b))


def best_runout_match(extracted_matrix: str, identifiers: list[dict]) -> RunoutMatch | None:
    """Compare an extracted matrix against a release's `identifiers` list,
    looking only at entries whose type is 'Matrix / Runout'."""
    if len(normalize_matrix(extracted_matrix)) < MIN_RUNOUT_CHARS:
        return None

    best: RunoutMatch | None = None
    for ident in identifiers:
        if (ident.get("type") or "").strip().lower() != "matrix / runout":
            continue
        value = ident.get("value") or ""
        score = score_pair(extracted_matrix, value)
        if best is None or score > best.score:
            best = RunoutMatch(score=score, matched_value=value)
    return best


def is_runout_hit(match: RunoutMatch | None) -> bool:
    return match is not None and match.score >= RUNOUT_MATCH_THRESHOLD


# ---------------------------------------------------------------------------
# Front/back agreement — a cheap sanity check used for the MEDIUM tier.
# ---------------------------------------------------------------------------

FRONT_BACK_AGREE_THRESHOLD = 70.0


def front_back_agreement(artist: str, title: str, candidate_title: str) -> float:
    """How well does a Discogs candidate's title agree with what we read off the
    front cover? Discogs release titles are just the album title; artist is a
    separate field, so we score the album title and lightly reward the artist
    appearing in the combined string."""
    cand = (candidate_title or "").strip()
    if not cand:
        return 0.0
    title_score = fuzz.token_set_ratio(title, cand)
    combined = f"{artist} {title}".strip()
    combined_score = fuzz.token_set_ratio(combined, cand)
    return max(title_score, combined_score)


def agrees(artist: str, title: str, candidate_title: str) -> bool:
    return front_back_agreement(artist, title, candidate_title) >= FRONT_BACK_AGREE_THRESHOLD
