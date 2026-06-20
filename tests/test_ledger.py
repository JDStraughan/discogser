from PIL import Image

from discogser.ledger import Ledger, album_key


def _images(tmp_path, colors):
    paths = []
    for i, c in enumerate(colors):
        p = tmp_path / f"img{i}.jpg"
        Image.new("RGB", (4, 4), c).save(p, "JPEG")
        paths.append(p)
    return tuple(paths)


def test_album_key_is_order_independent(tmp_path):
    a, b, c = _images(tmp_path, [(1, 1, 1), (2, 2, 2), (3, 3, 3)])
    assert album_key((a, b, c)) == album_key((c, a, b))


def test_album_key_changes_with_content(tmp_path):
    a, b, c = _images(tmp_path, [(1, 1, 1), (2, 2, 2), (3, 3, 3)])
    d = tmp_path / "other.jpg"
    Image.new("RGB", (4, 4), (9, 9, 9)).save(d, "JPEG")
    assert album_key((a, b, c)) != album_key((a, b, d))


def test_record_get_and_committed_upsert(tmp_path):
    led = Ledger(tmp_path / "l.sqlite3")
    led.record(
        "k1", status="review", release_id=1, title="X", confidence="LOW",
        signal="s", committed=False, data={"a": 1},
    )
    assert led.is_committed("k1") is False
    entry = led.get("k1")
    assert entry is not None and entry.title == "X" and entry.data == {"a": 1}

    led.record(
        "k1", status="added", release_id=1, title="X", confidence="HIGH",
        signal="s", committed=True, data={},
    )
    assert led.is_committed("k1") is True  # upsert flipped committed
    assert led.get("missing") is None
    led.close()
