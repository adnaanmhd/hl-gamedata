import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline import config as C          # noqa: E402
from pipeline import run as _runmod       # noqa: E402
from pipeline import vlm as _vlmmod       # noqa: E402
from pipeline.ledger import Ledger        # noqa: E402


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
