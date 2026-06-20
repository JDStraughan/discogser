import io
import queue

import pytest
from PIL import Image

pytest.importorskip("flask")

from discogser.web import WebReporter, create_app  # noqa: E402


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def test_reporter_emits_events_and_tally():
    q: queue.Queue = queue.Queue()
    with WebReporter(total=3, commit=False, events=q) as r:
        r.header("Uncategorized", 1, 137)
        r.album(status="high", artist="Pink Floyd", title="Animals",
                release_id=5, signal="barcode exact", committed=False, value="$9.99")
        r.album(status="guess", artist="X", title="Y",
                release_id=7, signal="best guess", committed=False, value="-")
        r.summary(tokens=(100, 50))

    events = []
    while not q.empty():
        events.append(q.get())

    assert [e["type"] for e in events] == ["header", "album", "album", "summary"]
    assert events[1]["url"].endswith("/release/5")
    summary = events[-1]
    assert summary["added"] == 1
    assert summary["review"] == 1 and summary["guesses"] == 1
    assert summary["tokens"] == [100, 50]


def test_index_page_serves():
    client = create_app().test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"discog" in resp.data


def test_run_rejects_missing_folder(tmp_path):
    client = create_app().test_client()
    resp = client.post("/run", json={"folder": str(tmp_path / "does-not-exist")}, headers={"X-Requested-With": "t"})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_stream_unknown_run_is_404():
    client = create_app().test_client()
    assert client.get("/stream/deadbeef").status_code == 404


def test_upload_accepts_images_and_run_starts():
    client = create_app().test_client()
    resp = client.post(
        "/upload",
        data={"photos": [(_jpeg(), "IMG_1.jpg"), (_jpeg(), "IMG_2.jpg")]},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "t"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["count"] == 2 and body["upload_id"]


def test_upload_rejects_non_images():
    client = create_app().test_client()
    resp = client.post(
        "/upload",
        data={"photos": [(io.BytesIO(b"hello"), "notes.txt")]},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "t"},
    )
    assert resp.status_code == 400


def test_run_rejects_expired_upload():
    client = create_app().test_client()
    resp = client.post("/run", json={"upload_id": "deadbeef"}, headers={"X-Requested-With": "t"})
    assert resp.status_code == 400


def test_download_unknown_run_is_404():
    client = create_app().test_client()
    assert client.get("/download/deadbeef/results.csv").status_code == 404
    assert client.get("/download/deadbeef/secrets.txt").status_code == 404


def test_album_event_carries_resolve_candidates():
    q: queue.Queue = queue.Queue()
    with WebReporter(total=1, commit=False, events=q) as r:
        r.album(
            status="guess", artist="A", title="T", release_id=5, signal="best guess",
            committed=False, value="-",
            extra={"key": "abc123", "candidates": [{"id": 7, "title": "X"}]},
        )
    ev = q.get()
    assert ev["key"] == "abc123"
    assert ev["candidates"] == [{"id": 7, "title": "X"}]


def test_resolve_rejects_bad_release_id():
    # These fail before any config/network, so they never touch a real account.
    client = create_app().test_client()
    assert client.post("/resolve", json={}, headers={"X-Requested-With": "t"}).status_code == 400
    assert client.post("/resolve", json={"release_id": "not-an-int"}, headers={"X-Requested-With": "t"}).status_code == 400


def test_security_headers_present():
    resp = create_app().test_client().get("/")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "img-src 'self' https://*.discogs.com" in csp
    assert "connect-src 'self'" in csp


def test_post_without_csrf_header_is_blocked():
    client = create_app().test_client()
    # No X-Requested-With -> rejected before any work (CSRF defense).
    assert client.post("/run", json={"upload_id": "x"}).status_code == 403
    assert client.post("/resolve", json={"release_id": 1}).status_code == 403


def test_host_guard_blocks_foreign_allows_localhost():
    client = create_app().test_client()
    # DNS-rebinding: a foreign Host pointed at 127.0.0.1 is rejected.
    assert client.get("/", headers={"Host": "attacker.example.com"}).status_code == 403
    # localhost variants are allowed.
    assert client.get("/", headers={"Host": "127.0.0.1:8765"}).status_code == 200
    assert client.get("/", headers={"Host": "localhost"}).status_code == 200


def test_web_ui_accessibility_attributes():
    page = create_app().test_client().get("/").get_data(as_text=True)
    assert 'lang="en"' in page                                  # language declared
    assert 'role="button"' in page and 'tabindex="0"' in page   # drop zone focusable
    assert 'aria-label="Add photos' in page                     # named for SR
    assert 'for="folder"' in page and 'for="folder_name"' in page  # labelled inputs
    assert 'aria-live="polite"' in page and 'role="alert"' in page  # live regions
    assert "prefers-reduced-motion" in page                     # motion respected
    assert ":focus-visible" in page and "sr-only" in page
    assert "#6b7383" not in page  # the 3.96:1 (AA-failing) grey is gone
    # the candidate "pick" control is a real keyboard-operable button
    assert '<button type="button" class="resolve"' in page and "aria-expanded" in page
    assert 'id="srprogress"' in page  # streaming rows announced to AT


def test_is_localhost_parser():
    from discogser.web import _is_localhost
    assert _is_localhost("localhost")
    assert _is_localhost("127.0.0.1:8765")
    assert _is_localhost("[::1]:8765")
    assert _is_localhost("::1")                              # bare IPv6 accepted
    assert not _is_localhost("evil.com")
    assert not _is_localhost("evil.com:80")
    assert not _is_localhost("127.0.0.1:8765@evil.com")      # userinfo rejected
    assert not _is_localhost("127.0.0.1.evil.com")
    assert not _is_localhost("")


def test_escape_helper_neutralises_attribute_breakout():
    # The client esc() must escape quotes, or Discogs-editable titles/thumbs
    # could break out of an HTML attribute and inject a handler (CSP allows
    # inline). The quote-escape map values only appear once esc() covers " and '.
    page = create_app().test_client().get("/").get_data(as_text=True)
    assert "&quot;" in page and "&#39;" in page
