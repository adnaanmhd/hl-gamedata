"""r-loop 3 — flip-tool regressions (tools/recal_*.py).

These tools run once, at the flip, against the ledger the continuous driver
inherits, and two of them touch the payment sheets. They had no tests.
"""
import importlib.util
import json
import sys
from pathlib import Path

from pipeline.ledger import Ledger

REPO = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_tool_{name}", REPO / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _root(led, sid, *, ctime, raw_s, delivered_s, player="p1@x.com"):
    led.insert_session(session_id=sid, game="kamla",
                       operator_email="op@x.com", player_email=player,
                       drive_path=f"kamla/op@x.com/{player}/{sid}",
                       drive_ctime=ctime, md5_video="a" * 32, bytes_=10,
                       state="DISCOVERED")
    led.update(sid, duration_raw_s=raw_s, duration_delivered_s=delivered_s)
    led.set_state(sid, "DELIVERED")
    led.update(sid, delivered_at=ctime)


def test_regen_preview_does_not_double_count_or_touch_real_sheets(
        cfg, monkeypatch, capsys):
    """--send stamps between the two days; preview did not. So every root
    the 08-15 sheet counted was still unstamped when 08-16 was built and
    re-entered through the LATE-ARRIVAL guard — the 08-16 preview carried
    the whole 08-15 cohort a second time. And because write_payment_sheet
    writes the REAL path first and only then copies to preview-*, that
    inflated sheet overwrote the sheet of record (which hl-backup mirrors to
    GCS). FLIP_RUNBOOK 7.2 is "preview -> sanity-read both sheets -> --send",
    so the human gate was reading fiction (r-loop 3)."""
    regen = _load("recal_regen_sheets")
    led = Ledger(cfg.ledger_path)
    try:
        # one root in each of the tool's hard-coded windows
        _root(led, "r15", ctime="2026-08-14T10:00:00+00:00",
              raw_s=3600.0, delivered_s=3600.0)
        _root(led, "r16", ctime="2026-08-15T10:00:00+00:00",
              raw_s=7200.0, delivered_s=7200.0)
    finally:
        led.close()

    monkeypatch.setattr(sys, "argv", ["recal_regen_sheets.py"])   # preview
    assert regen._locked_main(cfg) == 0
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{"):])
    assert payload["mode"] == "preview"
    days = {d["day"]: d for d in payload["days"]}

    assert days["2026-08-15"]["counted"] == 1
    assert days["2026-08-16"]["counted"] == 1, (
        "08-16 preview re-counted the 08-15 cohort — the inter-day stamp "
        "is missing again")

    # a preview must not write the sheets of record
    for day in ("2026-08-15", "2026-08-16"):
        d = cfg.reports_dir / day
        assert not (d / f"payment-{day}.csv").exists(), \
            "preview overwrote the real payment sheet"
        assert not (d / f"payment-{day}.md").exists()
        assert (d / f"preview-payment-{day}.csv").exists()
        assert (d / f"preview-payment-{day}.md").exists()

    # ...nor stamp the real ledger: only its scratch copy is stamped
    led = Ledger(cfg.ledger_path)
    try:
        for sid in ("r15", "r16"):
            assert led.get(sid)["uploaded_reported_at"] is None
    finally:
        led.close()


def test_refix_reset_discards_split_artifacts(cfg):
    """work/<sid>.split-manifest.json and the rowless work/<sid>-p<N>
    segment dirs must go BEFORE the rows are deleted — afterwards nothing
    can ever reclaim them, because every sweep branch looks the sid up in
    the ledger (r-loop 3)."""
    reset = _load("recal_refix_reset")
    sid = "child-1"
    cfg.work.mkdir(parents=True, exist_ok=True)
    (cfg.work / f"{sid}.split-manifest.json").write_text("[]")
    for n in (1, 2, 10):
        seg = cfg.work / f"{sid}-p{n}"
        seg.mkdir()
        (seg / "video.mp4").write_bytes(b"x" * 32)
    # a similarly-prefixed directory that is NOT a segment must survive
    (cfg.work / f"{sid}-pending").mkdir()
    (cfg.work / f"{sid}-panalysis").mkdir()

    reset.discard_split_artifacts(cfg.work, sid)

    assert not (cfg.work / f"{sid}.split-manifest.json").exists()
    for n in (1, 2, 10):
        assert not (cfg.work / f"{sid}-p{n}").exists()
    assert (cfg.work / f"{sid}-pending").exists(), \
        "the -p<digits> guard must not eat unrelated directories"
    assert (cfg.work / f"{sid}-panalysis").exists()
