"""Image preparation and vision extraction via the Anthropic SDK.

A single model call per album does two jobs at once to save tokens:
  1. Classifies each of the 3 supplied images as front / back / runout, so the
     pipeline can confirm the group's sequence integrity before trusting it.
  2. Extracts the structured fields needed to search and disambiguate Discogs.

The model is forced to answer through a tool call, so the result is always a
schema-valid object — no prose parsing.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import anthropic
from PIL import Image, ImageOps

try:  # iPhone photos are usually HEIC; register the opener if available.
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - optional dependency
    pass


# Front/back are downscaled hard to save tokens. The runout needs to stay sharp
# so etched matrix characters remain legible, so it gets a larger cap plus a
# grayscale + contrast pass.
FRONT_BACK_MAX_DIM = 1568
RUNOUT_MAX_DIM = 2048
JPEG_QUALITY = 90
MAX_TOKENS = 2048


class Role(str, Enum):
    FRONT = "front"
    BACK = "back"
    RUNOUT = "runout"


@dataclass(frozen=True)
class FrontInfo:
    artist: str
    title: str


@dataclass(frozen=True)
class BackInfo:
    label: str
    catalog_number: str
    barcode: str
    format: str
    country: str
    year: str
    pressing_notes: str


@dataclass(frozen=True)
class RunoutInfo:
    matrix: str
    confidence: str  # "high" | "medium" | "low"
    illegible: str


@dataclass(frozen=True)
class AlbumExtraction:
    image_roles: tuple[str, str, str]
    front: FrontInfo
    back: BackInfo
    runout: RunoutInfo


# ---------------------------------------------------------------------------
# Image preparation
# ---------------------------------------------------------------------------


def _encode_jpeg(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def _load_oriented(path: Path) -> Image.Image:
    img = Image.open(path)
    # Respect EXIF orientation so the model sees the photo right-side up.
    img = ImageOps.exif_transpose(img)
    return img


def _downscale(img: Image.Image, max_dim: int) -> Image.Image:
    w, h = img.size
    longest = max(w, h)
    if longest <= max_dim:
        return img
    scale = max_dim / float(longest)
    return img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)


def prepare_cover(path: Path, max_dim: int = FRONT_BACK_MAX_DIM) -> str:
    """Downscale a front/back cover to a token-friendly size and JPEG-encode."""
    img = _load_oriented(path).convert("RGB")
    img = _downscale(img, max_dim)
    return _encode_jpeg(img)


def prepare_runout(path: Path, max_dim: int = RUNOUT_MAX_DIM) -> str:
    """Prep a dead-wax macro shot: keep resolution high, then grayscale +
    autocontrast to make etched/stamped characters easier to read."""
    img = _load_oriented(path).convert("L")
    img = _downscale(img, max_dim)
    img = ImageOps.autocontrast(img, cutoff=1)
    return _encode_jpeg(img.convert("RGB"))


def prepare_album_images(front: Path, back: Path, runout: Path) -> list[str]:
    """Return base64 JPEGs for the 3 images in [front, back, runout] order."""
    return [prepare_cover(front), prepare_cover(back), prepare_runout(runout)]


# Cover-match thumbnails are small — we only need to recognize the artwork, not
# read fine print — so keep them cheap on tokens.
COVER_THUMB_MAX_DIM = 512


def prepare_cover_bytes(data: bytes, max_dim: int = COVER_THUMB_MAX_DIM) -> str:
    """Downscale an in-memory image (e.g. a downloaded Discogs cover) for cover
    matching, JPEG + base64."""
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img = _downscale(img, max_dim)
    return _encode_jpeg(img)


# ---------------------------------------------------------------------------
# Vision extraction
# ---------------------------------------------------------------------------

_TOOL_NAME = "record_album"

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "image_roles": {
            "type": "array",
            "description": (
                "The actual role of each supplied image, in the order given. "
                "Exactly three entries. A 'front' is the album front cover; a "
                "'back' is the back cover with tracklist/credits/barcode; a "
                "'runout' is an extreme close-up (macro) of the etched or "
                "stamped dead-wax / matrix area near the label."
            ),
            "items": {"type": "string", "enum": [r.value for r in Role]},
            "minItems": 3,
            "maxItems": 3,
        },
        "front": {
            "type": "object",
            "properties": {
                "artist": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["artist", "title"],
            "additionalProperties": False,
        },
        "back": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Record label, or empty if unknown"},
                "catalog_number": {"type": "string", "description": "Catalog number, or empty"},
                "barcode": {"type": "string", "description": "Barcode digits only, or empty"},
                "format": {"type": "string", "description": "e.g. LP, 12\", 2xLP, 45 RPM"},
                "country": {"type": "string", "description": "Country of release, or empty"},
                "year": {"type": "string", "description": "4-digit year, or empty"},
                "pressing_notes": {
                    "type": "string",
                    "description": "Any reissue/remaster/edition or pressing-plant notes printed on the sleeve.",
                },
            },
            "required": [
                "label",
                "catalog_number",
                "barcode",
                "format",
                "country",
                "year",
                "pressing_notes",
            ],
            "additionalProperties": False,
        },
        "runout": {
            "type": "object",
            "properties": {
                "matrix": {
                    "type": "string",
                    "description": (
                        "Transcribe the runout/matrix string LITERALLY, character by "
                        "character, including stamped vs hand-etched marks, "
                        "pressing-plant and SID codes. Preserve order and spacing as "
                        "best you can. Empty only if nothing is legible."
                    ),
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Your confidence in the matrix transcription.",
                },
                "illegible": {
                    "type": "string",
                    "description": "Note anything you could not read, or empty.",
                },
            },
            "required": ["matrix", "confidence", "illegible"],
            "additionalProperties": False,
        },
    },
    "required": ["image_roles", "front", "back", "runout"],
    "additionalProperties": False,
}

_PROMPT = (
    "You are cataloguing a vinyl record from three photographs. They are "
    "supplied in this assumed order: (1) front cover, (2) back cover, (3) a "
    "macro shot of the side A runout / dead-wax matrix.\n\n"
    "First, independently classify what each image ACTUALLY is (front, back, or "
    "runout) — do not assume the order is correct. Then extract the requested "
    "fields. For the runout, transcribe the etched/stamped characters exactly. "
    "If a field is unknown or not visible, return an empty string. Respond only "
    "through the record_album tool."
)


_COVER_TOOL = "compare_covers"
_COVER_MAX_TOKENS = 512
_COVER_SCHEMA = {
    "type": "object",
    "properties": {
        # `reason` is required but unused by the caller — forcing the model to
        # justify its picks measurably improves multi-image discrimination.
        "reason": {"type": "string"},
        "matches": {
            "type": "array",
            "items": {"type": "integer"},
            "description": (
                "0-based indices of the candidate covers that show the SAME "
                "album as the reference photo (same artwork). Empty if none."
            ),
        },
    },
    "required": ["reason", "matches"],
    "additionalProperties": False,
}

_COVER_PROMPT = (
    "The REFERENCE image is a phone photo of a vinyl record's front cover — it "
    "may be angled, have glare, price stickers, or shelf wear. The numbered "
    "CANDIDATE images are clean cover scans from a database. Decide which "
    "candidates show the SAME album cover as the reference: same artwork, same "
    "title/artist treatment. Ignore condition, lighting, angle, stickers, and "
    "minor edition differences. If none clearly match, return an empty list."
)


def _image_block(b64: str) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
    }


class VisionExtractor:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def extract(self, front: Path, back: Path, runout: Path) -> AlbumExtraction:
        images = prepare_album_images(front, back, runout)
        content: list[dict] = [{"type": "text", "text": _PROMPT}]
        # Label each image so the model's positional roles are unambiguous.
        for idx, b64 in enumerate(images, start=1):
            content.append({"type": "text", "text": f"Image {idx}:"})
            content.append(_image_block(b64))

        message = self._client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            tools=[
                {
                    "name": _TOOL_NAME,
                    "description": "Record the structured extraction for one vinyl album.",
                    "input_schema": _TOOL_SCHEMA,
                }
            ],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": content}],
        )

        tool_use = next((b for b in message.content if b.type == "tool_use"), None)
        if tool_use is None:
            raise RuntimeError("Vision model did not return a tool_use block")
        return _parse_extraction(tool_use.input)

    def match_covers(self, front_b64: str, candidate_b64: list[str]) -> tuple[int, ...]:
        """Return the indices of the candidate cover scans that show the same
        album as the photographed front cover. Indices reference candidate_b64."""
        if not candidate_b64:
            return ()

        content: list[dict] = [
            {"type": "text", "text": _COVER_PROMPT},
            {"type": "text", "text": "REFERENCE (my record):"},
            _image_block(front_b64),
        ]
        for idx, b64 in enumerate(candidate_b64):
            content.append({"type": "text", "text": f"CANDIDATE {idx}:"})
            content.append(_image_block(b64))

        message = self._client.messages.create(
            model=self._model,
            max_tokens=_COVER_MAX_TOKENS,
            tools=[{
                "name": _COVER_TOOL,
                "description": "Report which candidate covers match the reference.",
                "input_schema": _COVER_SCHEMA,
            }],
            tool_choice={"type": "tool", "name": _COVER_TOOL},
            messages=[{"role": "user", "content": content}],
        )
        tool_use = next((b for b in message.content if b.type == "tool_use"), None)
        if tool_use is None:
            return ()
        return tuple(
            i for i in tool_use.input.get("matches", [])
            if isinstance(i, int) and 0 <= i < len(candidate_b64)
        )


def _parse_extraction(data: dict) -> AlbumExtraction:
    roles = tuple(data["image_roles"])
    if len(roles) != 3:
        raise ValueError(f"Expected 3 image roles, got {roles!r}")
    f = data["front"]
    b = data["back"]
    r = data["runout"]
    return AlbumExtraction(
        image_roles=roles,  # type: ignore[arg-type]
        front=FrontInfo(artist=f.get("artist", ""), title=f.get("title", "")),
        back=BackInfo(
            label=b.get("label", ""),
            catalog_number=b.get("catalog_number", ""),
            barcode=b.get("barcode", ""),
            format=b.get("format", ""),
            country=b.get("country", ""),
            year=b.get("year", ""),
            pressing_notes=b.get("pressing_notes", ""),
        ),
        runout=RunoutInfo(
            matrix=r.get("matrix", ""),
            confidence=r.get("confidence", "low"),
            illegible=r.get("illegible", ""),
        ),
    )


def validate_group_roles(roles: tuple[str, ...]) -> bool:
    """Pure sequence-integrity check, anchored on the runout.

    The failure we must catch is *drift* — a missing or extra shot that
    misaligns every later album. The macro dead-wax (runout) shot is the
    reliable anchor for that: a valid group is two covers followed by a runout.

    We deliberately do NOT require distinguishing front from back: on many
    records (classical, easy-listening, text-heavy sleeves) the model genuinely
    can't tell a front from a back, and that confusion is harmless to both drift
    detection and extraction (all three images are read together). Any real
    drift still shows up here as a runout landing outside shot 3, or a cover
    landing in shot 3.

    Used both after extraction and by the self-test (no API required)."""
    if len(roles) != 3:
        return False
    runout = Role.RUNOUT.value
    return roles[2] == runout and roles[0] != runout and roles[1] != runout
