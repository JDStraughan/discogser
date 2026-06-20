import queue

import pytest

pytest.importorskip("flask")

from discogser.web import WebReporter, create_app  # noqa: E402


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
