from matching import (
    agrees,
    best_runout_match,
    is_runout_hit,
    normalize_matrix,
)
from main import primary_artist
from vision import validate_group_roles

RUNOUT_IDS = [
    {"type": "Barcode", "value": "5099902987612"},
    {"type": "Matrix / Runout", "value": "ST-A-771263-A1 MASTERDISK"},
]


def test_normalize_matrix():
    assert normalize_matrix("  ST-A 123 /B ") == "ST-A123/B"
    assert normalize_matrix("") == ""


def test_runout_match_hit_and_miss():
    assert is_runout_hit(best_runout_match("ST-A 771263 A1 MASTERDISK RL", RUNOUT_IDS))
    assert not is_runout_hit(best_runout_match("ZZZZ9999 NOPE", RUNOUT_IDS))


def test_runout_too_short_is_no_match():
    assert best_runout_match("AB", RUNOUT_IDS) is None


def test_primary_artist_strips_guest_credits():
    assert primary_artist("Norman Brooks with Al Goodman and His Orchestra") == "Norman Brooks"
    assert primary_artist("The Swingle Singers") == "Swingle Singers"
    assert primary_artist("Miles Davis feat. John Coltrane") == "Miles Davis"


def test_primary_artist_preserves_real_names():
    # Regression: separators in real band names must NOT be truncated.
    for name in [
        "AC/DC", "Earth, Wind & Fire", "Crosby, Stills & Nash",
        "Sly and the Family Stone", "Simon & Garfunkel",
    ]:
        assert primary_artist(name) == name


def test_front_back_agreement():
    assert agrees("Pink Floyd", "The Dark Side of the Moon",
                  "Pink Floyd - The Dark Side Of The Moon")
    assert not agrees("Pink Floyd", "Animals", "Miles Davis - Kind of Blue")


def test_role_validation_runout_anchored():
    assert validate_group_roles(("front", "back", "runout"))
    assert validate_group_roles(("back", "back", "runout"))   # front/back confusion ok
    assert not validate_group_roles(("front", "runout", "back"))  # drift
    assert not validate_group_roles(("runout", "back", "runout"))  # extra runout
