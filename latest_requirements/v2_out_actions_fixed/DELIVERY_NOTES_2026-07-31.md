# Delivery notes — humynlabs/07-31-2026 (draft for Slack message to Jack)

Batch: `humynlabs/07-31-2026/` — same three 2026-06-06 sessions as the 07-23
upload, re-issued with context-resolved `input_actions`.

## What changed since 07-23

1. **Conditional actions resolved per frame (your 07-27 feedback).** A key
   with several mode-dependent meanings now emits only the action the
   character is performing, classified from the game's own HUD state per
   video frame: Space → `movement_jump` (on foot) / `movement_jetpack_boost`
   (suited, airborne) / `flight_match_velocity` (piloting); E →
   `general_confirm` (dialogue/pause) / `general_primary_interact`
   (world); R → `flight_roll_mode` (ship) / `equipment_secondary_tool_action`
   (on foot); C, LShift, LCtrl likewise. Every ambiguous press was
   verified frame-by-frame against the video, and our QA now hard-fails any
   recurrence.
2. **Keys pressed where the game ignores them are stripped** from
   `input_keys` (same rationale as the agreed unbound-key strip): e.g. R
   mashed during a dialogue, Shift at the campfire (its prompt there is
   "Extend Stick" — no semantic in the agreed vocabulary), inputs during
   dialogue/pause screens. Mouse dx/dy always remain physical truth.
3. **Axis fix:** mouse-look actions are now per-axis (a horizontal-only
   mouse move no longer lists `movement_look_y_axis`, and vice versa).
4. **Kamla head-trim:** the clip now starts at gameplay (previous start
   included the settings/main menu, loading and the prologue cutscene;
   62.7 s cut, duration now 1053.5 s). Timestamps/actions re-based;
   controls-to-video sync re-verified (38 ms, within your 50 ms target).
5. Sync corrections are unchanged from 07-23 — all three sessions still
   measure ~0 ms with your action_video_grounding method. (Reminder: the
   negative correlation sign is expected — raw scene flow opposes camera
   rotation.)

## Current key → action mapping (v2 has no key_binding.json)

Outer Wilds (context-dependent): Space = jump | jetpack_boost |
match_velocity · E = confirm | primary_interact · R = roll_mode |
secondary_tool_action · C = landing_camera | secondary_interact ·
LShift/LCtrl = up/down thrust (ship, model ship, suit jetpack) · Esc =
pause · Tab = view_map · F = flashlight (incl. ship headlights) · Q =
cancel · Y = signalscope · Mouse L/R/Middle = primary tool / retrieve
scout / lock-on · 1/4, 2/3 = tool x/y axis · WASD = move axes · mouse =
look axes. Kamla: E = interact · Esc = pause_menu · WASD = move · mouse =
look.

## Known items we want your input on

- **Kamla LMB/RMB (open from your 07-17 report):** 2,868 frames hold mouse
  buttons that have no bound action in Kamla's controls as documented to
  us. We've kept them in `input_mouse_buttons` as physical truth rather
  than guessing a semantic. Could the recording team confirm what LMB/RMB
  do in Kamla so we can bind or strip them?
- **Vocabulary gaps (no presses affected in these samples):** model-ship
  R = "Reset" and map-view Shift/Ctrl = "Zoom" have no semantic in the
  agreed action set; such presses are treated as inactive.

## Capture-side notes (not fixable in post; raised with recording team)

- **Steam friend notifications** are burned into the Outer Wilds videos:
  16-01-50 at ~66–71 s, ~417–422 s, ~767–771 s; 18-50-54 at ~302–306 s.
  Future captures will have overlays/notifications disabled.
- **No audio track** in any capture (tool limitation).
- 18-50-54 opens with ~11 s of near-black — this is the game's interactive
  wake-up sequence (the "Wake Up [E]" prompt and its key press are real
  gameplay), not a loading screen.
- Frame spacing stays irregular (~12–20 % dropped frames, tool-side);
  per-row timestamps are real frame PTS, so sync is unaffected.
