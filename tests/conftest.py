import pytest

from tests.helpers import jpeg_bytes


@pytest.fixture
def front_path(tmp_path):
    """A real (tiny) JPEG on disk, usable by the cover-matching code path."""
    p = tmp_path / "front.jpg"
    p.write_bytes(jpeg_bytes())
    return p
