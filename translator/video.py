"""ffprobe helpers — the video is the source of truth for the frame grid.

Everything downstream (frames.csv row count, timestamps, event binning) is
anchored to what ffprobe reports here, so CSV and video can never drift.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VideoInfo:
    width: int
    height: int
    fps: float            # exact rational fps from r_frame_rate
    frame_count: int      # authoritative row count for frames.csv
    duration_s: float
    has_audio: bool
    codec: str

    @property
    def frame_us(self) -> float:
        return 1_000_000.0 / self.fps


def _run(cmd: list[str]) -> dict:
    out = subprocess.check_output(cmd, text=True, timeout=600)
    return json.loads(out)


def probe(path: Path) -> VideoInfo:
    """Read the authoritative frame grid from the delivered video."""
    data = _run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ])
    streams = data.get("streams", [])
    v = next(s for s in streams if s.get("codec_type") == "video")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    nb = v.get("nb_frames")
    if nb and nb != "N/A":
        frame_count = int(nb)
    else:
        # Fall back to counting frames exactly (slower but exact).
        frame_count = _count_frames(path)

    duration = float(v.get("duration") or data.get("format", {}).get("duration") or 0.0)

    # Authoritative fps = real timeline (frames / wall-clock duration). ffprobe's
    # r_frame_rate is only a NOMINAL base rate (e.g. 30/1) — for gdigrab desktop
    # capture the true average is lower (~29.7 here), and using the nominal rate
    # makes timestamps drift away from the video. Using frame_count/duration
    # keeps frames.csv and the video locked together (== avg_frame_rate).
    if duration > 0 and frame_count > 0:
        fps = frame_count / duration
    else:
        num, den = (int(x) for x in v["r_frame_rate"].split("/"))
        fps = num / den if den else 0.0
    return VideoInfo(
        width=int(v["width"]),
        height=int(v["height"]),
        fps=fps,
        frame_count=frame_count,
        duration_s=duration,
        has_audio=has_audio,
        codec=v.get("codec_name", ""),
    )


def _count_frames(path: Path) -> int:
    data = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-count_frames", "-show_entries", "stream=nb_read_frames",
        "-of", "json", str(path),
    ])
    return int(data["streams"][0]["nb_read_frames"])


def frame_pts(path: Path) -> list[int]:
    """Per-frame presentation timestamps in MICROSECONDS, relative to the first
    frame (frame 0 == 0), sorted ascending.

    The binner uses these to place events on the REAL frame timeline rather than
    a synthetic constant-fps grid. The capture videos are a 30 fps grid with
    DROPPED frames (the true average drifts to ~24-26 fps), so a uniform
    `floor(t / frame_us)` grid desyncs events from the frame they belong to
    wherever drops cluster. Subtracting the first PTS also absorbs the non-zero
    container `start_time` left by the lossless trim.

    Returns [] if PTS can't be read; the binner then falls back to uniform fps.
    """
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path),
    ], text=True, timeout=600)
    times: list[float] = []
    for line in out.splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        try:
            times.append(float(line))
        except ValueError:
            pass
    if not times:
        return []
    times.sort()
    t0 = times[0]
    return [int(round((t - t0) * 1_000_000.0)) for t in times]


def first_pts_abs(path: Path) -> float:
    """Absolute pts_time (seconds) of the first video packet.

    `frame_pts` subtracts this; `keyframe_times` does NOT. Anything that
    mixes the two clocks must rebase with this value, and anything handing
    a time back to ffmpeg's `-ss`/`-to` must add it again — measured on a
    real capture, `-ss` takes an ABSOLUTE container timestamp. Real
    captures carry a start_time of ~0.035-0.048 s (1-1.5 frames at 30 fps),
    which is small enough to hide under the 50 ms sync target forever.
    Returns 0.0 when the pts cannot be read, which is the no-offset case.
    """
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=pts_time", "-of", "csv=p=0",
         "-read_intervals", "%+#1", str(path)],
        capture_output=True, text=True, timeout=120)
    for line in out.stdout.splitlines():
        line = line.strip().rstrip(",")
        if line:
            try:
                return float(line)
            except ValueError:
                pass
    return 0.0


def keyframe_times(path: Path, within_s: float | None = None) -> list[float]:
    """Return keyframe PTS (seconds), on the ABSOLUTE container clock —
    unlike `frame_pts`, which is rebased to the first frame. Callers that
    compare the two must rebase with `first_pts_abs` (see cutter.py and
    trim.py). Lossless cuts may only land on these."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-skip_frame", "nokey", "-show_entries", "frame=pts_time",
        "-of", "csv=p=0", str(path),
    ]
    if within_s is not None:
        cmd[2:2] = ["-read_intervals", f"%+{within_s}"]
    out = subprocess.check_output(cmd, text=True, timeout=600)
    times = []
    for line in out.splitlines():
        line = line.strip().rstrip(",")
        if line:
            try:
                times.append(float(line))
            except ValueError:
                pass
    return sorted(times)
