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
