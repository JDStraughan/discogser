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
    resp = client.post("/run", json={"folder": str(tmp_path / "does-not-exist")})
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
    )
    assert resp.status_code == 400


def test_run_rejects_expired_upload():
    client = create_app().test_client()
    resp = client.post("/run", json={"upload_id": "deadbeef"})
    assert resp.status_code == 400


def test_download_unknown_run_is_404():
    client = create_app().test_client()
    assert client.get("/download/deadbeef/results.csv").status_code == 404
    assert client.get("/download/deadbeef/secrets.txt").status_code == 404
