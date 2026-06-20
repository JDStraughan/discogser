from main import Confidence, Resolver
from tests.helpers import MockClient, MockExtractor, mkext, rel

MATRIX = [{"type": "Matrix / Runout", "value": "ABCDEF123 RL"}]


def test_barcode_plus_runout_is_high(front_path):
    client = MockClient(
        {"barcode": [rel(1, identifiers=MATRIX), rel(2)]},
        {1: rel(1, identifiers=MATRIX), 2: rel(2)},
    )
    res = Resolver(client).resolve(mkext(barcode="X", matrix="ABCDEF123 RL"), front_path)
    assert res.confidence is Confidence.HIGH and res.release_id == 1 and "runout" in res.signal


def test_single_barcode_is_high_exact(front_path):
    client = MockClient({"barcode": [rel(7)]}, {7: rel(7)})
    res = Resolver(client).resolve(mkext(barcode="X"), front_path)
    assert res.confidence is Confidence.HIGH and "barcode exact" in res.signal


def test_multiple_barcode_is_medium_most_held(front_path):
    client = MockClient(
        {"barcode": [rel(8, have=3), rel(9, have=99)]},
        {8: rel(8, have=3), 9: rel(9, have=99)},
    )
    res = Resolver(client).resolve(mkext(barcode="X"), front_path)
    assert res.confidence is Confidence.MEDIUM and res.release_id == 9


def test_single_catno_artist_is_medium(front_path):
    client = MockClient({"catno_artist": [rel(11)]}, {11: rel(11)})
    res = Resolver(client).resolve(mkext(catno="ML-1", artist="Artist", title="Title"), front_path)
    assert res.confidence is Confidence.MEDIUM and "catno" in res.signal


def test_cover_match_confirms_and_picks_most_held(front_path):
    client = MockClient(
        {"broad": [rel(20, have=300, cover="covX"), rel(21, have=80, cover="covX")]},
        {20: rel(20, have=300, cover="covX"), 21: rel(21, have=80, cover="covX")},
    )
    res = Resolver(client, extractor=MockExtractor(cover_indices=(0,)), cover_match=True).resolve(
        mkext(artist="Artist", title="Title"), front_path
    )
    assert res.confidence is Confidence.MEDIUM and res.cover_confirmed and res.release_id == 20


def test_guess_when_cover_finds_nothing(front_path):
    client = MockClient(
        {"broad": [rel(30, have=5, master=900, cover="c")]},
        {30: rel(30, have=5, master=900, cover="c")},
        versions=[{"id": 30, "country": "US", "stats": {"community": {"in_collection": 5}}}],
    )
    res = Resolver(client, extractor=MockExtractor(cover_indices=()), cover_match=True).resolve(
        mkext(artist="Artist", title="Title"), front_path
    )
    assert res.confidence is Confidence.LOW and res.is_guess and res.release_id == 30


def test_not_found(front_path):
    res = Resolver(MockClient()).resolve(mkext(), front_path)
    assert res.confidence is Confidence.LOW and res.release_id is None and res.signal == "not found"
