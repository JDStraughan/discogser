import io

from PIL import Image
from rich.console import Console

from discogser.cli import build_parser
from discogser.doctor import doctor

# Keys whose absence keeps doctor fully offline (no network, no real keys used).
_KEYS = ["ANTHROPIC_API_KEY", "DISCOGS_TOKEN", "DISCOGS_USERNAME", "USER_AGENT",
         "ANTHROPIC_MODEL", "DISCOGS_FOLDER"]


def _offline(monkeypatch):
    for k in _KEYS:
        monkeypatch.delenv(k, raising=False)


def _console():
    return Console(file=io.StringIO(), width=100, record=True)


def _albums(folder, n):
    for i in range(n):
        Image.new("RGB", (8, 8), (0, 0, 0)).save(folder / f"IMG_{i:03}.jpg", "JPEG")


def test_reports_missing_config_and_skips_connectivity(tmp_path, monkeypatch):
    _offline(monkeypatch)
    con = _console()
    rc = doctor(con, None, env_file=tmp_path / "none.env")
    out = con.export_text()
    assert rc == 1
    assert "ANTHROPIC_API_KEY" in out and "missing" in out
    assert "skipped" in out  # connectivity not attempted without keys


def test_grouping_clean(tmp_path, monkeypatch):
    _offline(monkeypatch)
    _albums(tmp_path, 3)
    con = _console()
    doctor(con, tmp_path, env_file=tmp_path / "none.env")
    assert "1 albums, no leftovers" in con.export_text()


def test_grouping_leftovers_flagged(tmp_path, monkeypatch):
    _offline(monkeypatch)
    _albums(tmp_path, 4)
    con = _console()
    rc = doctor(con, tmp_path, env_file=tmp_path / "none.env")
    assert rc == 1 and "leftover" in con.export_text()


def test_parser_doctor_makes_photos_optional():
    args = build_parser().parse_args(["--doctor"])
    assert args.doctor and args.photos is None
    normal = build_parser().parse_args(["./photos"])
    assert normal.photos is not None and not normal.doctor
