import httpx
import pytest

from discogser import discogs
from discogser.discogs import (
    DiscogsClient,
    _looks_like_image,
    _redact,
    _validate_image_url,
    safe_int,
)
from discogser.pipeline import _csv_cell

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100


def test_image_url_allowlist():
    assert _validate_image_url("https://i.discogs.com/x.jpg")
    assert _validate_image_url("https://img.discogs.com/y.jpeg")
    for bad in [
        "http://i.discogs.com/x.jpg",         # not https
        "https://169.254.169.254/latest",     # cloud metadata SSRF
        "https://localhost/x",
        "https://evil.com/x.jpg",
        "https://i.discogs.com.evil.com/x",   # suffix spoof
        "file:///etc/passwd",
        "",
    ]:
        assert not _validate_image_url(bad)


def test_magic_bytes():
    assert _looks_like_image(b"\xff\xd8\xff\xe0...")       # jpeg
    assert _looks_like_image(b"\x89PNG\r\n\x1a\n...")      # png
    assert _looks_like_image(b"RIFF1234WEBPxxxx")          # webp
    assert not _looks_like_image(b"<html>nope")
    assert not _looks_like_image(b"")


def test_safe_int():
    assert safe_int("42") == 42
    assert safe_int(None) == 0
    assert safe_int("nope", 7) == 7
    assert safe_int(3.9) == 3


def test_redact_token():
    assert _redact("calling with Discogs token=secret123 oops") == (
        "calling with Discogs token=*** oops"
    )


def test_csv_injection_neutralised_including_leading_space():
    assert _csv_cell("=HYPERLINK(0)") == "'=HYPERLINK(0)"
    assert _csv_cell(" =cmd()") == "' =cmd()"   # leading-whitespace bypass closed
    assert _csv_cell("\t@SUM(A1)") == "'\t@SUM(A1)"
    assert _csv_cell("Pink Floyd") == "Pink Floyd"
    assert _csv_cell(None) == ""


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(discogs.time, "sleep", lambda *_: None)


def _img_client(handler, tmp_path):
    client = DiscogsClient("t", "u", "ua/1.0", cache_dir=tmp_path)
    client._img = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        headers={"User-Agent": "ua"},
    )
    return client


def test_fetch_image_hardening(tmp_path):
    def handler(request):
        path = request.url.path
        if path == "/ok.jpg":
            return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=JPEG)
        if path == "/redir":
            return httpx.Response(302, headers={"location": "https://i.discogs.com/ok.jpg"})
        if path == "/evil-redir":
            return httpx.Response(302, headers={"location": "https://evil.com/x"})
        if path == "/html":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")
        if path == "/oversize":
            return httpx.Response(
                200,
                headers={"content-type": "image/jpeg", "content-length": str(99 * 1024 * 1024)},
                content=b"x",
            )
        return httpx.Response(404)

    client = _img_client(handler, tmp_path)
    assert client.fetch_image("https://i.discogs.com/ok.jpg") == JPEG
    assert client.fetch_image("https://i.discogs.com/redir") == JPEG          # allowlisted redirect
    assert client.fetch_image("https://i.discogs.com/evil-redir") is None     # redirect off-allowlist
    assert client.fetch_image("https://i.discogs.com/html") is None           # not an image
    assert client.fetch_image("https://i.discogs.com/oversize") is None       # too big
    assert client.fetch_image("https://evil.com/x.jpg") is None               # blocked pre-flight
    client.close()
