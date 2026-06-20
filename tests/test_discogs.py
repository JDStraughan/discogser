import httpx
import pytest

from discogser import discogs
from discogser.discogs import DiscogsClient, DiscogsError


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # Throttle/backoff sleeps would make these tests crawl; make them instant.
    monkeypatch.setattr(discogs.time, "sleep", lambda *_: None)


def _client(handler, tmp_path):
    client = DiscogsClient("t", "u", "ua/1.0", cache_dir=tmp_path)
    client._client = httpx.Client(
        base_url=discogs.BASE_URL,
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Discogs token=t", "User-Agent": "ua"},
    )
    return client


def test_retries_5xx_then_succeeds(tmp_path):
    n = {"calls": 0}

    def handler(request):
        n["calls"] += 1
        if n["calls"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"results": [{"id": 1}]})

    client = _client(handler, tmp_path)
    assert client.search(artist="x") == [{"id": 1}]
    assert n["calls"] == 3
    client.close()


def test_429_respects_retry_after(tmp_path):
    n = {"calls": 0}

    def handler(request):
        n["calls"] += 1
        if n["calls"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"results": []})

    client = _client(handler, tmp_path)
    assert client.search(catno="x") == []
    assert n["calls"] == 2
    client.close()


def test_exhausted_retries_raise(tmp_path):
    client = _client(lambda r: httpx.Response(500), tmp_path)
    with pytest.raises(DiscogsError):
        client.search(artist="x")
    client.close()


def test_404_raises(tmp_path):
    client = _client(lambda r: httpx.Response(404), tmp_path)
    with pytest.raises(DiscogsError):
        client.get_release(999)
    client.close()


def test_release_is_cached(tmp_path):
    n = {"calls": 0}

    def handler(request):
        n["calls"] += 1
        return httpx.Response(200, json={"id": 5, "title": "X"})

    client = _client(handler, tmp_path)
    first = client.get_release(5)
    second = client.get_release(5)
    assert first == second and n["calls"] == 1  # second served from disk cache
    client.close()


def test_collection_pagination(tmp_path):
    def handler(request):
        page = int(dict(request.url.params).get("page", "1"))
        if page == 1:
            return httpx.Response(200, json={"releases": [{"id": 1}, {"id": 2}], "pagination": {"pages": 2}})
        return httpx.Response(200, json={"releases": [{"id": 3}], "pagination": {"pages": 2}})

    client = _client(handler, tmp_path)
    assert client.get_collection_release_ids() == {1, 2, 3}
    client.close()


def test_resolve_folder_id_and_fallback(tmp_path):
    folders = {
        "folders": [
            {"id": 0, "name": "All"},
            {"id": 1, "name": "Uncategorized"},
            {"id": 5, "name": "New Arrivals"},
        ]
    }
    client = _client(lambda r: httpx.Response(200, json=folders), tmp_path)
    assert client.resolve_folder_id("New Arrivals") == 5
    assert client.resolve_folder_id("Nonexistent") == 1  # falls back to Uncategorized
    client.close()


def test_search_malformed_id_does_not_crash(tmp_path):
    # safe_int guards downstream; the client itself just returns the raw results.
    client = _client(lambda r: httpx.Response(200, json={"results": [{"id": None}]}), tmp_path)
    assert client.search(artist="x") == [{"id": None}]
    client.close()
