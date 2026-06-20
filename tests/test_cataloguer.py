from PIL import Image

from discogser.pipeline import Confidence, Resolution, _Cataloguer
from tests.helpers import MockExtractor, mkext


class FakeUI:
    def __init__(self):
        self.statuses = []
        self.drifted = False

    def album(self, **k):
        self.statuses.append(k["status"])

    def drift_halt(self, *a):
        self.drifted = True


class FakeLedger:
    def __init__(self, committed=False):
        self._committed = committed
        self.records = []

    def is_committed(self, key):
        return self._committed

    def get(self, key):
        return None

    def record(self, key, **kw):
        self.records.append(kw["status"])


class FakeClient:
    def __init__(self):
        self.added = []

    def add_to_collection(self, folder_id, release_id):
        self.added.append(release_id)
        return {}


def _group(tmp_path):
    paths = []
    for i in range(3):
        p = tmp_path / f"IMG_{i}.jpg"
        Image.new("RGB", (8, 8), (0, 0, 0)).save(p, "JPEG")
        paths.append(p)
    return tuple(paths)


def _cataloguer(resolution, extractor, *, ui, ledger, commit=True, owned=None):
    class _R:
        def resolve(self, ext, front_path=None):
            return resolution

    client = FakeClient()
    cat = _Cataloguer(
        client=client, ledger=ledger, resolver=_R(), extractor=extractor,
        ui=ui, owned=owned if owned is not None else set(), folder_id=1,
        commit=commit, model="test-model",
    )
    return cat, client


def test_high_is_added(tmp_path):
    ui, led = FakeUI(), FakeLedger()
    res = Resolution(Confidence.HIGH, "runout match (95)", 111, "X", "url", lowest_price=12.0)
    cat, client = _cataloguer(res, MockExtractor(ext=mkext()), ui=ui, ledger=led)
    assert cat.process(_group(tmp_path)) is True
    assert ui.statuses == ["high"] and client.added == [111]
    assert len(cat.results_rows) == 1 and not cat.review_rows


def test_guess_is_flagged_not_added(tmp_path):
    ui, led = FakeUI(), FakeLedger()
    res = Resolution(Confidence.LOW, "ambiguous", 222, "X", "url", is_guess=True)
    cat, client = _cataloguer(res, MockExtractor(ext=mkext()), ui=ui, ledger=led)
    cat.process(_group(tmp_path))
    assert ui.statuses == ["guess"] and client.added == []
    assert len(cat.review_rows) == 1


def test_vision_error_is_isolated(tmp_path):
    ui, led = FakeUI(), FakeLedger()
    cat, _ = _cataloguer(None, MockExtractor(boom=True), ui=ui, ledger=led)
    assert cat.process(_group(tmp_path)) is True
    assert ui.statuses == ["error"]


def test_drift_halts_the_run(tmp_path):
    ui, led = FakeUI(), FakeLedger()
    drifted_ext = mkext(roles=("back", "runout", "front"))
    res = Resolution(Confidence.HIGH, "x", 1, "X", "u")
    cat, _ = _cataloguer(res, MockExtractor(ext=drifted_ext), ui=ui, ledger=led)
    assert cat.process(_group(tmp_path)) is False and ui.drifted


def test_owned_is_skipped(tmp_path):
    ui, led = FakeUI(), FakeLedger()
    res = Resolution(Confidence.HIGH, "barcode exact", 333, "X", "url", lowest_price=1.0)
    cat, client = _cataloguer(res, MockExtractor(ext=mkext()), ui=ui, ledger=led, owned={333})
    cat.process(_group(tmp_path))
    assert ui.statuses == ["skipped"] and client.added == [] and not cat.results_rows
