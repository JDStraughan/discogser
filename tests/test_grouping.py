from PIL import Image

from discogser.pipeline import discover_images, group_images, sort_images


def _make(folder, names):
    for n in names:
        Image.new("RGB", (8, 8), (0, 0, 0)).save(folder / n, "JPEG")


def test_sort_then_group(tmp_path):
    _make(tmp_path, ["IMG_0003.jpg", "IMG_0001.jpg", "IMG_0002.jpg"])
    ordered = sort_images(discover_images(tmp_path))
    assert [p.name for p in ordered] == ["IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.jpg"]
    groups, leftovers = group_images(ordered)
    assert len(groups) == 1 and leftovers == []


def test_leftovers_flagged(tmp_path):
    _make(tmp_path, [f"IMG_{i:04}.jpg" for i in range(7)])
    groups, leftovers = group_images(sort_images(discover_images(tmp_path)))
    assert len(groups) == 2 and len(leftovers) == 1


def test_non_images_ignored(tmp_path):
    _make(tmp_path, ["IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.jpg"])
    (tmp_path / "results.csv").write_text("x")
    (tmp_path / "notes.txt").write_text("y")
    assert len(discover_images(tmp_path)) == 3


def test_empty():
    assert group_images([]) == ([], [])


def test_heic_unsupported_count(monkeypatch):
    from pathlib import Path

    import discogser.pipeline as P

    monkeypatch.setattr(P, "HEIC_AVAILABLE", False)
    assert P.heic_unsupported_count(
        [Path("a.heic"), Path("b.jpg"), Path("c.HEIF")]
    ) == 2
    monkeypatch.setattr(P, "HEIC_AVAILABLE", True)
    assert P.heic_unsupported_count([Path("a.heic")]) == 0


def test_run_halts_when_every_photo_is_unreadable_heic(tmp_path, monkeypatch):
    # The HEIC gate runs before any network/config, so this exits cleanly
    # without an API key — exactly the iPhone-user-with-no-heic-extra case.
    import io

    from rich.console import Console

    import discogser.pipeline as P

    monkeypatch.setattr(P, "HEIC_AVAILABLE", False)
    for i in range(3):
        (tmp_path / f"IMG_{i}.heic").write_bytes(b"not really an image")
    console = Console(file=io.StringIO(), record=True)
    rc = P.run(tmp_path, config=None, commit=False, folder_name=None, console=console)  # type: ignore[arg-type]
    assert rc == 2
    assert "HEIC" in console.export_text()
