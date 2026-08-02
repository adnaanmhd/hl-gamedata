// HumynCapture camera-data bridge — BepInEx plugin.
//
// Confirmed on real hardware: BepInEx injection + pid-based handshake work
// (see LogOutput.log from a real Outer Wilds session — plugin loaded,
// opened camera_bridge/<pid>.jsonl at the expected path). The bug found on
// that same run: the file stayed at 0 bytes. ResolveCamera() was
// hard-excluding any camera with a non-null `targetTexture`, meant to skip
// UI/render-to-texture utility cameras — but Outer Wilds (Unity 2019.4,
// likely routing its real gameplay camera through a render texture for its
// post-processing/space-lighting effects) had ZERO cameras that passed that
// filter at all. No fallback found -> ResolveCamera() returned null every
// frame -> LateUpdate returned immediately -> nothing written. This also
// explains why LogOutput.log stayed completely silent: the "using active
// screen camera" warning only fires when a fallback IS found.
//
// Fix: treat targetTexture as a lower-priority signal, not a hard
// exclusion — prefer a normal screen-rendering camera, but accept a
// render-texture camera rather than nothing. Also logs once if truly zero
// candidates exist, instead of failing silently.
//
// WHY THIS EXISTS: HumynCapture only sees screen pixels + OS input, never
// the game engine's real camera. This plugin runs inside the game process
// (via BepInEx) and is the only piece that can read Camera.main's real
// transform, writing it to a file HumynCapture reads after the game closes.
//
// GAME-AGNOSTIC: only calls Unity's public Camera/Transform API — the same
// compiled DLL works for any Mono-build Unity title; only the injection
// setup (BepInEx 5.x Mono vs 6.x IL2CPP) varies per title.
//
// HANDOFF: writes to `%LOCALAPPDATA%\HumynCapture\camera_bridge\<pid>.jsonl`,
// keyed by this process's own pid — HumynCapture already records
// `game.pid_at_capture` in metadata.json, so no other coordination is
// needed (HumynCapture attaches to an already-running game, it doesn't
// launch it).
//
// COORDINATES: logged verbatim in Unity's own left-handed X-right/Y-up/
// Z-forward convention, matching the client's spec — no axis conversion.
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
        public const string PluginVersion = "1.0.2";

        private StreamWriter _writer;
        private long _frameIndex;
        private Camera _fallbackCamera;
        private Camera[] _cameraBuffer = new Camera[8];
        private bool _loggedFallbackCamera;
        private bool _loggedNoCamera;
        // Diagnostic only (real bug found: a real session's camera_bridge
        // file came back completely 0-byte, with NO further log lines at
        // all after Awake's "writing to ..." line -- not even the "no
        // active camera found" warning, which only needs LateUpdate to run
        // ONCE to fire. That absence points at LateUpdate never executing
        // at all for this plugin on this game, a different and earlier
        // problem than camera selection. These flags log once, unconditionally,
        // the first time each lifecycle method is actually invoked by Unity,
        // to confirm definitively whether that's happening at all.
        private bool _loggedAwakeComplete;
        private bool _loggedFirstLateUpdate;

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
                _writer = new StreamWriter(path, false) { AutoFlush = true };
                Logger.LogInfo("[CameraLogger] pid=" + pid + " writing to " + path);
            }
            catch (Exception e)
            {
                Logger.LogError("[CameraLogger] failed to open output file: " + e);
                _writer = null;
            }
            // Diagnostic: proves Awake ran to completion without an
            // exception escaping past the try/catch above (which would
            // otherwise disable the component silently in some Unity
            // versions).
            Logger.LogInfo("[CameraLogger] Awake() complete, writer=" +
                            (_writer != null ? "open" : "null") +
                            ", enabled=" + enabled + ", gameObject.activeInHierarchy=" +
                            gameObject.activeInHierarchy);
            _loggedAwakeComplete = true;
        }

        private void LateUpdate()
        {
            if (!_loggedFirstLateUpdate)
            {
                Logger.LogInfo("[CameraLogger] LateUpdate() is running (first call).");
                _loggedFirstLateUpdate = true;
            }
            if (_writer == null) return;

            Camera cam = ResolveCamera();
            if (cam == null) return;

            try
            {
                Transform t = cam.transform;
                Vector3 p = t.position;
                Quaternion q = t.rotation;
                Vector3 e = q.eulerAngles;
                long wallclockMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

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

        private Camera ResolveCamera()
        {
            Camera main = Camera.main;
            if (main != null && main.isActiveAndEnabled)
            {
                return main;
            }

            if (_fallbackCamera != null && _fallbackCamera.isActiveAndEnabled)
            {
                return _fallbackCamera;
            }

            int cameraCount = Camera.allCamerasCount;
            if (cameraCount > _cameraBuffer.Length)
            {
                _cameraBuffer = new Camera[cameraCount];
            }

            int populatedCount = Camera.GetAllCameras(_cameraBuffer);
            Camera best = null;
            bool bestRendersToScreen = false;
            bool bestIsPerspective = false;
            long bestPixelArea = -1;
            float bestDepth = float.NegativeInfinity;

            for (int i = 0; i < populatedCount; i++)
            {
                Camera candidate = _cameraBuffer[i];
                if (candidate == null || !candidate.isActiveAndEnabled)
                {
                    continue;
                }

                // Real bug fixed here: a render-texture target used to be a
                // hard exclusion, meant to skip UI/render-to-texture utility
                // cameras. On a title whose real gameplay camera ALSO
                // renders through a texture (common for post-processing —
                // confirmed the cause of a real 0-byte output on Outer
                // Wilds), that excluded every candidate outright. Now it's
                // just a lower-priority signal: a screen-rendering camera
                // still wins first, but a render-texture camera is accepted
                // rather than nothing.
                bool rendersToScreen = candidate.targetTexture == null;
                bool isPerspective = !candidate.orthographic;
                long pixelArea = (long)candidate.pixelWidth * candidate.pixelHeight;

                bool better;
                if (best == null)
                {
                    better = true;
                }
                else if (rendersToScreen != bestRendersToScreen)
                {
                    better = rendersToScreen;
                }
                else if (isPerspective != bestIsPerspective)
                {
                    better = isPerspective;
                }
                else if (pixelArea != bestPixelArea)
                {
                    better = pixelArea > bestPixelArea;
                }
                else
                {
                    better = candidate.depth > bestDepth;
                }

                if (better)
                {
                    best = candidate;
                    bestRendersToScreen = rendersToScreen;
                    bestIsPerspective = isPerspective;
                    bestPixelArea = pixelArea;
                    bestDepth = candidate.depth;
                }
            }

            _fallbackCamera = best;
            if (_fallbackCamera != null && !_loggedFallbackCamera)
            {
                Logger.LogWarning(
                    "[CameraLogger] Camera.main is unavailable; using active camera '" +
                    _fallbackCamera.name + "' (rendersToScreen=" + bestRendersToScreen + ").");
                _loggedFallbackCamera = true;
            }
            else if (_fallbackCamera == null && !_loggedNoCamera)
            {
                Logger.LogWarning(
                    "[CameraLogger] no active camera found at all (" + populatedCount +
                    " candidates scanned) — camera data will not be recorded this session.");
                _loggedNoCamera = true;
            }

            return _fallbackCamera;
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
