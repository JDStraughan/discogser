"""Shared test doubles: in-memory Discogs/vision mocks and payload builders."""

from __future__ import annotations

import io

from PIL import Image

from vision import _parse_extraction


def jpeg_bytes(color: tuple[int, int, int] = (10, 20, 30), size=(80, 80)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def mkext(*, artist="Artist", title="Title", barcode="", catno="", matrix="ZZZZZZ",
          roles=("front", "back", "runout")):
    """Build an AlbumExtraction via the real parser (keeps tests honest)."""
    return _parse_extraction({
        "image_roles": list(roles),
        "front": {"artist": artist, "title": title},
        "back": {
            "label": "", "catalog_number": catno, "barcode": barcode,
            "format": "LP", "country": "US", "year": "", "pressing_notes": "",
        },
        "runout": {"matrix": matrix, "confidence": "low", "illegible": ""},
    })


def rel(rid, *, have=10, identifiers=None, cover=None, master=None,
        country="US", title="Artist - Title"):
    """A Discogs release/search payload shaped like the real API."""
    d = {
        "id": rid, "title": title, "community": {"have": have},
        "identifiers": identifiers or [], "year": 1970, "lowest_price": 9.0,
        "num_for_sale": 1, "formats": [{"name": "Vinyl", "descriptions": ["LP"]}],
        "country": country,
    }
    if cover:
        d["cover_image"] = cover
    if master:
        d["master_id"] = master
    return d


class MockClient:
    def __init__(self, search_map=None, releases=None, versions=None):
        self.search_map = search_map or {}
        self.releases = releases or {}
        self.versions = versions or []
        self.added: list[int] = []

    def search(self, **p):
        if p.get("barcode"):
            return self.search_map.get("barcode", [])
        if p.get("catno") and p.get("artist"):
            return self.search_map.get("catno_artist", [])
        if p.get("catno"):
            return self.search_map.get("catno", [])
        if "q" in p or p.get("release_title"):
            return self.search_map.get("broad", [])
        return []

    def get_release(self, rid, currency="USD"):
        return self.releases[rid]

    def get_master_versions(self, master_id):
        return self.versions

    def fetch_image(self, url):
        return jpeg_bytes()

    def add_to_collection(self, folder_id, release_id):
        self.added.append(release_id)
        return {}


class MockExtractor:
    def __init__(self, ext=None, cover_indices=(), boom=False):
        self._ext = ext
        self._idx = cover_indices
        self._boom = boom

    def extract(self, *paths):
        if self._boom:
            raise RuntimeError("vision boom")
        return self._ext

    def match_covers(self, front_b64, candidate_b64):
        return self._idx
