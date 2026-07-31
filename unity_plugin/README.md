# HumynCapture Camera Logger — BepInEx plugin (fix for issue #2)

**Status: UNVERIFIED.** Written and reviewed for correctness on a machine
with no Windows, no Unity, no .NET/Mono SDK — never actually compiled,
never actually injected into a running game. Do not treat this as done
until the checklist below has been run for real against `Kamla.exe` (or
whichever title is being tested first) and confirmed working.

## Why this exists

HumynCapture (the separate Python capture tool) only ever sees the game's
composited screen output + OS-level mouse/keyboard — it has no way to read
a Unity game's actual `Camera.main` transform. Every real delivery checked
so far has had `frames.csv`'s camera columns (`c2w_m00`..`m33`,
`camera_fx/fy/cx/cy`, distortion coefficients) 100% empty, because that data
was never captured, not lost or corrupted. This plugin runs *inside* the
game process — the only place that data actually exists — and writes it to
a file HumynCapture reads after the game closes
(`app/core/finalize/camera_bridge.py` does the reading/merging side; it's
unit-tested and confirmed working — see `capture_tool/tests/test_camera_bridge.py`).

## Per-title checklist (run this for EVERY new game, not just once)

We don't own any of these games' source — only a downloaded `.exe` — so
this can't be baked into a build; it has to be injected at runtime, and
whether that's even possible varies per title's Unity build type.

1. **Check Mono vs IL2CPP.** In the game's install folder (same folder as
   its `.exe`):
   - A `MonoBleedingEdge\` folder and NO `GameAssembly.dll` → **Mono.**
     (Confirmed for `Kamla.exe` already.)
   - A `GameAssembly.dll` next to the `.exe`, and `<Game>_Data\Managed\`
     mostly just has `Metadata\global-metadata.dat` → **IL2CPP.**
   - Neither of the above / no `_Data` folder at all → **not a Unity
     game**, this whole approach doesn't apply; flag that title's camera
     data as unavailable rather than guessing.
2. **Mono →** download the matching `BepInEx_win_x64_5.x.x.x.zip` from
   github.com/BepInEx/BepInEx/releases (the 5.x **Mono** line, not
   IL2CPP/BepInEx 6), extract into the game's install folder, drop this
   plugin's compiled `HumynCapture.CameraLogger.dll` into the newly-created
   `BepInEx\plugins\` folder, launch the game once, and check
   `BepInEx\LogOutput.log` for a clean `Chainloader` startup with no crash.
3. **IL2CPP →** needs BepInEx 6.x (IL2CPP variant) AND an extra
   "unhollowing" step (generating that specific game's method signatures)
   before this same plugin's logic can even be recompiled against it — more
   setup per title, not a different design. Not attempted yet for any
   title.
4. **Confirm output.** After a short test recording, check whether
   `%LOCALAPPDATA%\HumynCapture\camera_bridge\<pid>.jsonl` was created and
   has one JSON line per frame (`pid` = the game's process id, same value
   HumynCapture already records as `metadata.json`'s `game.pid_at_capture`).

## Building (once BepInEx.Core/UnityEngine.Modules NuGet packages are
confirmed to resolve for real — untested here)

```
cd unity_plugin/CameraLogger
dotnet build -c Release
```

Output DLL lands in `bin/Release/net472/HumynCapture.CameraLogger.dll` —
copy that into the target game's `BepInEx/plugins/` folder per the
checklist above. The same compiled DLL is reused unmodified for every OTHER
Mono-build Unity title — only the injection setup (steps 1-2 above) repeats
per title, not this project or its code.

## What's actually verified vs. not

| Piece | Status |
|---|---|
| `Plugin.cs` compiles cleanly and matches BepInEx 5.x's plugin shape | **Unverified** — no toolchain here to build it |
| Camera.main sampling logic / JSON schema | Reviewed for correctness, not run against a real Unity game |
| BepInEx actually injects into `Kamla.exe` | **Not yet run** — Mono/IL2CPP diagnostic confirmed Mono; injection itself (checklist step 2) hasn't been tried |
| Python-side parsing/matrix math/frame-matching (`camera_bridge.py`) | **Unit-tested**, 17 passing tests — this half is trustworthy |
| End-to-end (real plugin output -> `patch_frames_csv`) | **Not yet run** — needs a real `<pid>.jsonl` from an actual game session |
