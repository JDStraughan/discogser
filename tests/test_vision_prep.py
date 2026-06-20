import base64
import io

from PIL import Image

from discogser.vision import (
    _normalize_barcode,
    prepare_cover,
    prepare_cover_bytes,
    prepare_runout,
)


def _decode(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64)))


def _save(tmp_path, name, size):
    p = tmp_path / name
    Image.new("RGB", size, (40, 80, 120)).save(p, "JPEG")
    return p


def test_cover_is_jpeg_and_downscaled(tmp_path):
    img = _decode(prepare_cover(_save(tmp_path, "c.jpg", (4000, 3000))))
    assert img.format == "JPEG" and max(img.size) == 1568


def test_runout_is_lossless_png(tmp_path):
    # Lossless so JPEG ringing doesn't eat hairline etched matrix strokes.
    img = _decode(prepare_runout(_save(tmp_path, "r.jpg", (3000, 2000))))
    assert img.format == "PNG"


def test_cover_bytes_roundtrip(tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (800, 800), (1, 2, 3)).save(buf, "JPEG")
    img = _decode(prepare_cover_bytes(buf.getvalue()))
    assert img.format == "JPEG" and max(img.size) == 512


def test_barcode_whitespace_normalised():
    assert _normalize_barcode("7 81759 12345 6") == "781759123456"
    assert _normalize_barcode("") == ""
