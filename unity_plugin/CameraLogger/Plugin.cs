// HumynCapture camera-data bridge — BepInEx plugin.
//
// *** UNVERIFIED — written and reviewed for correctness, NEVER compiled or
// run against a real Unity/BepInEx toolchain (none available in this
// environment: no Windows, no Unity, no .NET/Mono SDK). Treat this the same
// way as every other "best-effort, clearly marked unverified" fix in
// capture_tool/README.md — it needs a real build + real BepInEx injection
// test on the actual Kamla.exe (confirmed Mono build, see the injection
// diagnostic already run) before it can be trusted. ***
//
// WHY THIS EXISTS
// ----------------
// HumynCapture (the separate Python capture tool) only ever sees the game's
// composited screen output + OS-level mouse/keyboard — it has NO way to read
// the game engine's actual camera transform. Every delivery's frames.csv
// camera columns (c2w_m00..m33, camera_fx/fy/cx/cy, distortion coefficients)
// have been 100% empty for every session checked so far, for exactly this
// reason — the data was never captured, not lost or corrupted.
//
// This plugin runs INSIDE the game process (injected via BepInEx, since we
// only have the downloaded .exe, not the Unity project source — see the
// Mono/IL2CPP diagnostic already run against Kamla.exe) and is the only
// piece of this whole pipeline that can actually read Camera.main's real
// transform. It does exactly one job: sample Camera.main every frame and
// write it to a file HumynCapture can read AFTER the game closes.
//
// WHY THIS IS GAME-AGNOSTIC (the "build it modularly" requirement)
// ------------------------------------------------------------------
// Everything below calls only Unity's own public Camera/Transform API —
// nothing here references Kamla by name or by any game-specific class. The
// exact same compiled DLL should work, unmodified, for any OTHER Mono-build
// Unity game (drop the same .dll into that game's BepInEx/plugins folder).
// Only the INJECTION mechanism (BepInEx 5.x for Mono vs. BepInEx 6.x/
// Il2CppInterop for IL2CPP) needs to vary per title — see
// unity_plugin/README.md's per-title checklist. This plugin's own code
// never needs to change for a new Mono Unity title.
//
// WHERE THE OUTPUT GOES AND WHY
// ------------------------------
// Writes to `%LOCALAPPDATA%\HumynCapture\camera_bridge\<pid>.jsonl`, keyed
// by THIS PROCESS'S OWN pid. HumynCapture's own metadata.json already
// records `game.pid_at_capture` for every session (see
// app/core/session_engine.py) — so the Python-side finalize step
// (app/core/finalize/camera_bridge.py) can find this exact file after the
// session ends just by reading that same pid back out of its own metadata,
// with zero coordination needed between the two processes while the game
// is actually running (no shared memory, no sockets, no env var handshake —
// HumynCapture attaches to an ALREADY-RUNNING game process per
// process_watcher.py, it doesn't launch it, so it can't hand this plugin
// anything at startup time).
//
// COORDINATE CONVENTION
// ----------------------
// Unity's own convention (left-handed, X-right, Y-up, Z-forward) already
// matches the client's stated spec ("Top down view game demands for data"
// §4: "left-handed coordinate system... Right-x, Up-y, Front-z") — logged
// verbatim here with NO axis conversion. The Python-side bridge is where
// any camera-to-world MATRIX layout conversion happens, not here — this
// file's only job is to get the raw numbers out of the process alive.
using System;
using System.Globalization;
using System.IO;
using BepInEx;
using UnityEngine;

namespace HumynCapture.CameraLogger
{
    [BepInPlugin(PluginGuid, PluginName, PluginVersion)]
    public class Plugin : BaseUnityPlugin
    {
        public const string PluginGuid = "com.humynlabs.cameralogger";
        public const string PluginName = "HumynCapture Camera Logger";
        public const string PluginVersion = "1.0.0";

        private StreamWriter _writer;
        private long _frameIndex;

        private void Awake()
        {
            try
            {
                int pid = System.Diagnostics.Process.GetCurrentProcess().Id;
                string baseDir = Environment.GetFolderPath(
                    Environment.SpecialFolder.LocalApplicationData);
                string dir = Path.Combine(baseDir, "HumynCapture", "camera_bridge");
                Directory.CreateDirectory(dir);
                string path = Path.Combine(dir, pid + ".jsonl");
                // append:false — a stale file from a crashed previous run of
                // the SAME pid (unlikely but not impossible on Windows pid
                // reuse across a short window) must never silently blend
                // with this session's data.
                _writer = new StreamWriter(path, false) { AutoFlush = true };
                Logger.LogInfo("[CameraLogger] pid=" + pid + " writing to " + path);
            }
            catch (Exception e)
            {
                // A logging failure must never crash or otherwise affect the
                // actual game — this plugin is purely observational.
                Logger.LogError("[CameraLogger] failed to open output file: " + e);
                _writer = null;
            }
        }

        private void LateUpdate()
        {
            // LateUpdate (not Update): camera transforms driven by
            // follow/look-at scripts (very common in Kamla-style
            // third-person games) are typically finalized in LateUpdate,
            // so sampling here — after the game's own camera-rig scripts
            // have already run this frame — avoids reading a stale
            // position that's one frame behind what actually got rendered.
            if (_writer == null) return;

            Camera cam = Camera.main;
            if (cam == null) return;  // no active main camera this frame — skip, don't crash

            try
            {
                Transform t = cam.transform;
                Vector3 p = t.position;
                Quaternion q = t.rotation;
                Vector3 e = q.eulerAngles;
                long wallclockMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

                // Hand-built single-line JSON — deliberately no dependency on
                // a JSON library the host game may or may not ship (BepInEx
                // itself doesn't guarantee one is loadable from a plugin).
                // Schema is the CONTRACT with the Python-side reader
                // (app/core/finalize/camera_bridge.py) — keep both in sync.
                string line = string.Format(CultureInfo.InvariantCulture,
                    "{{\"frame\":{0},\"wallclock_ms\":{1},\"unity_time\":{2:F6}," +
                    "\"position\":[{3:F6},{4:F6},{5:F6}]," +
                    "\"rotation_euler\":[{6:F6},{7:F6},{8:F6}]," +
                    "\"rotation_quaternion\":[{9:F6},{10:F6},{11:F6},{12:F6}]," +
                    "\"fov_deg\":{13:F6},\"aspect\":{14:F6}}}",
                    _frameIndex, wallclockMs, Time.unscaledTime,
                    p.x, p.y, p.z,
                    e.x, e.y, e.z,
                    q.x, q.y, q.z, q.w,
                    cam.fieldOfView, cam.aspect);
                _writer.WriteLine(line);
                _frameIndex++;
            }
            catch (Exception ex)
            {
                Logger.LogWarning("[CameraLogger] frame log failed: " + ex.Message);
            }
        }

        private void OnApplicationQuit()
        {
            try
            {
                _writer?.Flush();
                _writer?.Dispose();
            }
            catch (Exception e)
            {
                Logger.LogWarning("[CameraLogger] failed to close output file cleanly: " + e.Message);
            }
        }
    }
}
