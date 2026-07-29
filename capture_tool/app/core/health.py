"""
Subsystem health + end-of-session self-check.

Fixes issues B2 (no detection of a missing input modality), E1 (background
threads swallow errors and the orchestrator never checks), and E2 (no
end-of-session self-check gating "ready").

Before this module existed, `session_engine.run` never read
`RawMouseCapture.last_error` after starting it (B1's second root cause) and
had no equivalent check for any other subsystem, so a dead capture thread
produced a silent zero-modality session that looked identical to a good one
downstream. `SubsystemMonitor` centralizes the "did this thing actually
start, and is it still alive" question so `SessionEngine.run` (and the P0
finalize gate) has one place to ask it, and `SelfCheckResult` is the single
gate `ready_for_upload` is computed from.

Every subsystem this module polls must expose:
    - `last_error: str | None` — set (never raised across the thread
      boundary) on internal failure.
    - `is_alive() -> bool` — True while the subsystem's worker thread/task is
      running normally.
Optional:
    - `event_count: int` — used for the B1 mouse-motion liveness check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# Control bytes (Ctrl+letter artifacts, B5) and raw vk_### tokens (B4) must
# never reach the input stream if capture-time normalization worked.
_BAD_TOKEN_RE = re.compile(r"^vk_\d+$")


@runtime_checkable
class MonitoredSubsystem(Protocol):
    last_error: str | None

    def is_alive(self) -> bool: ...


@dataclass
class SubsystemIssue:
    name: str
    error: str


@dataclass
class SubsystemMonitor:
    """Tracks named subsystems started during a session and reports the
    first (and every) failure any of them records, instead of the old
    behavior of only ever checking `RawMouseCapture` at the very end (and in
    practice, not even that — see B1)."""

    subsystems: dict[str, MonitoredSubsystem] = field(default_factory=dict)
    _reported: set[str] = field(default_factory=set)

    def register(self, name: str, subsystem: MonitoredSubsystem) -> None:
        self.subsystems[name] = subsystem

    def poll(self) -> list[SubsystemIssue]:
        """Call periodically during recording (and once right after start).
        Returns only *new* issues since the last poll that hasn't already
        been surfaced, so callers can log/warn once per failure rather than
        every tick."""
        issues = []
        for name, sub in self.subsystems.items():
            err = getattr(sub, "last_error", None)
            if err and name not in self._reported:
                self._reported.add(name)
                issues.append(SubsystemIssue(name=name, error=err))
            elif not err and not sub.is_alive() and name not in self._reported:
                self._reported.add(name)
                issues.append(SubsystemIssue(
                    name=name, error="subsystem thread/task exited unexpectedly"))
        return issues

    def any_fatal(self) -> list[SubsystemIssue]:
        """Full current-state snapshot (not just new-since-last-poll) —
        used at start-of-recording and at finalize."""
        out = []
        for name, sub in self.subsystems.items():
            err = getattr(sub, "last_error", None)
            if err:
                out.append(SubsystemIssue(name=name, error=err))
        return out


@dataclass
class SelfCheckResult:
    ready_for_upload: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ready_for_upload = False
        self.failures.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


# Expected modalities per input scheme. kbd_mouse covers every game we ship
# keybinds for today (Kamla, Outer Wilds); a controller-only game would need
# its own scheme, added here rather than silently passing with zero keyboard
# events.
EXPECTED_MODALITIES_KBD_MOUSE = ("keyboard", "mouse_motion", "mouse_buttons")

# frames_dropped / frame_count above this fraction fails the self-check
# outright (well beyond A1's "12-20%" baseline, i.e. something is very
# wrong, not just the known drop-rate problem A1 is tracked to fix).
MAX_ACCEPTABLE_DROP_FRACTION = 0.05


def check_modalities(events_by_type: dict[str, int],
                      expected: tuple[str, ...] = EXPECTED_MODALITIES_KBD_MOUSE
                      ) -> dict[str, bool]:
    """B2: which expected modalities actually produced >0 events."""
    key_count = events_by_type.get("key", 0)
    mouse_raw_count = events_by_type.get("mouse_raw", 0)
    mouse_button_count = events_by_type.get("mouse_button", 0)
    have = {
        "keyboard": key_count > 0,
        "mouse_motion": mouse_raw_count > 0,
        "mouse_buttons": mouse_button_count > 0,
    }
    return {name: have.get(name, False) for name in expected}


def find_bad_tokens(all_keys_seen: set[str]) -> set[str]:
    """B4/B5 regression guard: raw vk_### codes or control bytes must never
    have survived capture-time normalization."""
    bad = set()
    for tok in all_keys_seen:
        if _BAD_TOKEN_RE.match(tok):
            bad.add(tok)
        elif len(tok) == 1 and ord(tok) < 32:
            bad.add(tok)
    return bad


def run_self_check(
    *,
    events_by_type: dict[str, int],
    all_keys_seen: set[str],
    frame_count: int,
    frames_dropped: int,
    game_slug: str,
    game_slug_is_known: bool,
    video_readable: bool,
    subsystem_issues: list[SubsystemIssue],
    sync_status: str | None,
    require_audio: bool = False,
    has_audio: bool = False,
    expected_modalities: tuple[str, ...] = EXPECTED_MODALITIES_KBD_MOUSE,
) -> SelfCheckResult:
    """E2: the single gate `ready_for_upload` is computed from.

    Every check here corresponds 1:1 to a fix in
    HumynCapture_Capture_Tool_Issues.md's "Capture self-check & observability"
    section. Called once at the end of `SessionEngine.run`, after finalize
    (trim -> re-anchor/bin -> write v2 -> this).
    """
    result = SelfCheckResult(ready_for_upload=True)

    for issue in subsystem_issues:
        result.fail(f"subsystem '{issue.name}' failed: {issue.error}")

    modalities = check_modalities(events_by_type, expected_modalities)
    for name, present in modalities.items():
        if not present:
            result.fail(f"missing input modality: {name} (0 events captured)")

    if not video_readable:
        result.fail("video.mp4 failed to decode end-to-end")

    if frame_count > 0:
        drop_fraction = frames_dropped / frame_count
        if drop_fraction > MAX_ACCEPTABLE_DROP_FRACTION:
            result.fail(
                f"frames_dropped fraction {drop_fraction:.1%} exceeds "
                f"{MAX_ACCEPTABLE_DROP_FRACTION:.0%} threshold "
                f"({frames_dropped}/{frame_count})")
        elif drop_fraction > 0:
            result.warn(f"frames_dropped {frames_dropped}/{frame_count} "
                        f"({drop_fraction:.1%}) — see issue A1")
    else:
        result.fail("frame_count is 0 — video appears empty")

    if not game_slug_is_known:
        result.warn(
            f"game_slug '{game_slug}' is not in the known-titles registry "
            f"(app.core.games) — recorded anyway, but verify the exe/title")

    bad_tokens = find_bad_tokens(all_keys_seen)
    if bad_tokens:
        result.fail(f"unnormalized key tokens leaked into capture: "
                    f"{sorted(bad_tokens)}")

    if sync_status == "FAIL":
        result.fail("controls-to-video sync self-test FAILED "
                     "(|lag| > 50ms target — see §3 of the v2-compliance handoff)")
    elif sync_status == "WARN":
        result.warn("controls-to-video sync self-test unverifiable "
                     "(mouse signal too weak/inactive to measure)")
    elif sync_status is None:
        result.warn("controls-to-video sync self-test did not run "
                     "(numpy/opencv unavailable in this build)")

    if require_audio and not has_audio:
        result.fail("audio required but no audio track captured (see issue C1)")

    return result
