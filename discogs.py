"""Discogs API client: searching, release/master fetches, collection writes.

Designed for the platform's constraints:
  * 60 requests/minute (authenticated). We proactively pace requests and read
    the X-Discogs-Ratelimit-Remaining header to back off before getting 429'd.
  * Exponential backoff on 429 and 5xx.
  * On-disk caching of release and master responses so reruns and multi-
    candidate disambiguation never re-query the same id.
  * A unique, descriptive User-Agent (required, or you get throttled hard).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://api.discogs.com"

# Stay comfortably under 60/min. ~1.1s between calls => ~54/min steady state.
_MIN_INTERVAL = 1.1
# When the rate-limit window is nearly exhausted, sleep this long to let it roll.
_LOW_REMAINING = 3
_BACKOFF_BASE = 2.0
_BACKOFF_MAX = 60.0
_MAX_RETRIES = 5


class DiscogsError(RuntimeError):
    pass


class DiscogsClient:
    def __init__(
        self,
        token: str,
        username: str,
        user_agent: str,
        cache_dir: str | Path = ".cache",
        timeout: float = 30.0,
    ) -> None:
        self.username = username
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._client = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout,
            headers={
                "Authorization": f"Discogs token={token}",
                "User-Agent": user_agent,
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DiscogsClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low-level request with pacing, backoff, and rate-limit awareness ----

    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_request
            if elapsed < _MIN_INTERVAL:
                time.sleep(_MIN_INTERVAL - elapsed)
            self._last_request = time.monotonic()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        for attempt in range(_MAX_RETRIES):
            self._throttle()
            try:
                resp = self._client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if attempt == _MAX_RETRIES - 1:
                    raise DiscogsError(f"Network error calling {path}: {exc}") from exc
                time.sleep(min(_BACKOFF_BASE ** attempt, _BACKOFF_MAX))
                continue

            remaining = resp.headers.get("X-Discogs-Ratelimit-Remaining")
            if remaining is not None:
                try:
                    if int(remaining) <= _LOW_REMAINING:
                        time.sleep(_MIN_INTERVAL * 2)
                except ValueError:
                    pass

            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == _MAX_RETRIES - 1:
                    raise DiscogsError(
                        f"{resp.status_code} from {path} after {_MAX_RETRIES} retries"
                    )
                retry_after = resp.headers.get("Retry-After")
                delay = (
                    float(retry_after)
                    if retry_after and retry_after.isdigit()
                    else min(_BACKOFF_BASE ** attempt, _BACKOFF_MAX)
                )
                time.sleep(delay)
                continue

            if resp.status_code == 404:
                raise DiscogsError(f"404 Not Found: {path}")
            if resp.status_code >= 400:
                raise DiscogsError(f"{resp.status_code} from {path}: {resp.text[:200]}")

            return resp.json()

        raise DiscogsError(f"Exhausted retries for {path}")

    # -- disk cache ---------------------------------------------------------

    def _cache_path(self, kind: str, key: str) -> Path:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return self._cache_dir / f"{kind}_{digest}.json"

    def _cached(self, kind: str, key: str) -> dict | None:
        path = self._cache_path(kind, key)
        if path.exists():
            try:
                return json.loads(path.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
        return None

    def _store(self, kind: str, key: str, value: dict) -> None:
        try:
            self._cache_path(kind, key).write_text(
                json.dumps(value), encoding="utf-8"
            )
        except OSError:
            pass

    # -- public endpoints ---------------------------------------------------

    def search(self, **params: str) -> list[dict]:
        """GET /database/search. Always scopes to type=release. Returns the
        results list (possibly empty)."""
        query = {k: v for k, v in params.items() if v}
        query["type"] = "release"
        data = self._request("GET", "/database/search", params=query)
        return data.get("results", [])

    def get_release(self, release_id: int, currency: str = "USD") -> dict:
        # curr_abbr makes Discogs return `lowest_price` (the cheapest current
        # marketplace listing) in a known currency rather than the account
        # default. Cache key includes currency so a currency change can't serve
        # a stale price.
        key = f"{release_id}:{currency}"
        cached = self._cached("release", key)
        if cached is not None:
            return cached
        data = self._request(
            "GET", f"/releases/{release_id}", params={"curr_abbr": currency}
        )
        self._store("release", key, data)
        return data

    def get_master_versions(self, master_id: int, per_page: int = 100) -> list[dict]:
        cache_key = f"{master_id}:{per_page}"
        cached = self._cached("master_versions", cache_key)
        if cached is not None:
            return cached.get("versions", [])
        data = self._request(
            "GET",
            f"/masters/{master_id}/versions",
            params={"per_page": str(per_page)},
        )
        self._store("master_versions", cache_key, data)
        return data.get("versions", [])

    # -- collection ---------------------------------------------------------

    def resolve_folder_id(self, folder_name: str) -> int:
        """Map a folder name to its id. 'Uncategorized' is the default folder
        (id 1). Folder id 0 ('All') is read-only and cannot be written to."""
        data = self._request(
            "GET", f"/users/{self.username}/collection/folders"
        )
        folders = data.get("folders", [])
        for folder in folders:
            if (folder.get("name") or "").strip().lower() == folder_name.strip().lower():
                return int(folder["id"])
        # Fall back to Uncategorized (id 1) if the requested name is unknown.
        for folder in folders:
            if (folder.get("name") or "") == "Uncategorized":
                return int(folder["id"])
        raise DiscogsError(
            f"Folder {folder_name!r} not found and no Uncategorized folder exists"
        )

    def get_collection_release_ids(self) -> set[int]:
        """Pull every release_id in the user's collection (folder 0 = all),
        paginated, for dedupe at start of a run."""
        ids: set[int] = set()
        page = 1
        while True:
            data = self._request(
                "GET",
                f"/users/{self.username}/collection/folders/0/releases",
                params={"page": str(page), "per_page": "100"},
            )
            for item in data.get("releases", []):
                rid = item.get("id") or item.get("basic_information", {}).get("id")
                if rid is not None:
                    ids.add(int(rid))
            pagination = data.get("pagination", {})
            if page >= int(pagination.get("pages", 1)):
                break
            page += 1
        return ids

    def add_to_collection(self, folder_id: int, release_id: int) -> dict:
        return self._request(
            "POST",
            f"/users/{self.username}/collection/folders/{folder_id}/releases/{release_id}",
        )


# ---------------------------------------------------------------------------
# Helpers for reading community "have" counts off heterogeneous payloads.
# ---------------------------------------------------------------------------


def have_count(payload: dict) -> int:
    """Extract a community 'have' count from a release detail or a master
    version entry, which use different shapes."""
    community = payload.get("community")
    if isinstance(community, dict) and "have" in community:
        return int(community.get("have") or 0)
    stats = payload.get("stats", {})
    if isinstance(stats, dict):
        comm = stats.get("community", {})
        if isinstance(comm, dict):
            for key in ("in_collection", "have"):
                if key in comm:
                    return int(comm.get(key) or 0)
    return 0
