import faulthandler
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline import config as C          # noqa: E402
from pipeline import ingest as _ingest    # noqa: E402
from pipeline import run as _runmod       # noqa: E402
from pipeline import vlm as _vlmmod       # noqa: E402
from pipeline.ledger import Ledger        # noqa: E402


# Hard per-test deadline. The driver tests run real threads against fake
# clocks, so a regression can spin a loop with no exit and HANG pytest
# rather than fail it — indefinitely, in CI and in the flip's "full suite
# green" pre-arm gate (r-loop 3). There is no pytest.ini/pyproject in this
# repo and no pytest-timeout dependency; faulthandler is stdlib and turns a
# hang into a dumped traceback pointing at the stuck thread. Generous
# enough that no honest test approaches it (the whole suite is ~50s).
_TEST_TIMEOUT_S = 300


@pytest.fixture(autouse=True)
def _hang_guard():
    faulthandler.dump_traceback_later(_TEST_TIMEOUT_S, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


@pytest.fixture(autouse=True)
def _no_real_drive(monkeypatch):
    """No test may list the REAL Drive I.

    `ingest.list_drive` shells out to a full recursive `rclone lsjson -R`
    against the live collection tree. Any driver test that forgot to fake it
    silently did that on every suite run — slow, non-hermetic, and dependent
    on production state (r-loop 3 found the suite doing exactly this). It is
    read-only, so nothing was damaged, but a test must never depend on it.
    Tests that need a listing patch this themselves and win, because their
    monkeypatch is applied after this fixture."""
    def _refuse(_cfg):
        raise AssertionError(
            "test called ingest.list_drive — patch it "
            "(monkeypatch.setattr(ingest, 'list_drive', lambda _cfg: [])) "
            "instead of listing the real Drive I")
    monkeypatch.setattr(_ingest, "list_drive", _refuse)


@pytest.fixture(autouse=True)
def _daily_reports_knob_independent(monkeypatch):
    """The gate must be green regardless of the DEPLOYED CONT_DAILY_REPORTS:
    FLIP_RUNBOOK 6c ships False committed and the arming gate runs on that
    exact tree — 11 send-path tests went red on the runbook's own pinned
    invocation (r-loop 8). Tests asserting the suppression set False
    themselves and win (their monkeypatch applies after this fixture)."""
    monkeypatch.setattr(C, "CONT_DAILY_REPORTS", True)


@pytest.fixture(autouse=True)
def _module_state(monkeypatch):
    """The pre-driver suite IS the lockstep regression (§18.8): it runs
    with PIPELINE_OVERLAP=False; driver tests opt in explicitly. Also
    resets vlm endpoint/rung stickiness and the run-level R23 state so
    nothing leaks between tests."""
    monkeypatch.setattr(C, "PIPELINE_OVERLAP", False)
    monkeypatch.setattr(_vlmmod, "_which", None)
    monkeypatch.setattr(_vlmmod, "_rung", 0)
    # "" = prev-key rung unarmed; None would read the REAL secrets.env
    monkeypatch.setattr(_vlmmod, "_prev_key_cache", "")
    monkeypatch.setattr(_vlmmod, "_pressure_path", None)
    _vlmmod._session_models.clear()
    _runmod._reset_vlm_run_state()


@pytest.fixture
def cfg(tmp_path):
    c = C.Config(home=tmp_path / "hl-pipeline")
    c.ensure_dirs()
    return c


@pytest.fixture
def ledger(cfg):
    led = Ledger(cfg.ledger_path)
    yield led
    led.close()


def make_session_entries(game="kamla", op="op@x.com", player="p1@x.com",
                         sid="2026-08-14T10-00-00Z_kamla_c_0123456789abcdef",
                         md5="d41d8cd98f00b204e9800998ecf8427e",
                         ctime="2026-08-14T10:00:00.000Z",
                         files=None):
    base = f"{game}/{op}/{player}/{sid}"
    names = files if files is not None else list(C.REQUIRED_FILES)
    out = [{"Path": base, "Name": sid, "IsDir": True, "ModTime": ctime}]
    for n in names:
        out.append({"Path": f"{base}/{n}", "Name": n, "IsDir": False,
                    "Size": 100, "ModTime": ctime,
                    "Hashes": {"md5": md5 if n == "video.mp4" else "aa"}})
    return out
