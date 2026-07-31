"""Fix for issue #2 (camera pose/intrinsics columns 100% empty in every
delivery). Pure-math/file-parsing coverage — this is the part of the
Unity-camera-data fix that's actually testable on macOS with no Unity/
BepInEx toolchain; the plugin itself (unity_plugin/CameraLogger/) is
UNVERIFIED, see its own docstring."""
import csv
import json
import math

from app.core.finalize.camera_bridge import (
    _nearest_sample, find_camera_log, intrinsics_from_fov, load_camera_samples,
    patch_frames_csv, quaternion_to_c2w_matrix,
)


class TestQuaternionToC2W:
    def test_identity_rotation_at_origin_is_identity_matrix(self):
        m = quaternion_to_c2w_matrix((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))
        assert m == [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ]

    def test_translation_lands_in_the_last_column_of_each_row(self):
        m = quaternion_to_c2w_matrix((1.5, 2.5, -3.5), (0.0, 0.0, 0.0, 1.0))
        assert (m[3], m[7], m[11]) == (1.5, 2.5, -3.5)
        assert m[15] == 1.0

    def test_bottom_row_is_always_zero_zero_zero_one(self):
        m = quaternion_to_c2w_matrix((9.0, -4.0, 2.0), (0.1, 0.2, 0.3, 0.9273618495))
        assert m[12:16] == [0.0, 0.0, 0.0, 1.0]

    def test_90_degree_yaw_rotates_forward_into_right(self):
        """A 90-degree rotation about Y (Unity's up axis) should turn the
        camera's local forward (0,0,1) into world +X or -X, not leave it
        unchanged — a sanity check that this isn't secretly the identity
        matrix regardless of input."""
        half = math.radians(90) / 2
        q = (0.0, math.sin(half), 0.0, math.cos(half))  # 90 deg about Y
        m = quaternion_to_c2w_matrix((0.0, 0.0, 0.0), q)
        # R * (0,0,1) = (r02, r12, r22)
        forward_in_world = (m[2], m[6], m[10])
        assert abs(forward_in_world[1]) < 1e-9  # no change in Y (rotation axis)
        assert abs(forward_in_world[0]) > 0.9    # rotated substantially into X


class TestIntrinsicsFromFov:
    def test_fx_equals_fy_always(self):
        """The client's own explicit acceptance criterion (spec §4.3#8:
        'camera_intrinsics parameters, fx = fy') — true by construction
        whenever the projection aspect matches the pixel aspect, which
        holds for an unmodified Camera.main render."""
        fx, fy, cx, cy = intrinsics_from_fov(60.0, 1920, 1080)
        assert fx == fy

    def test_principal_point_is_image_center(self):
        fx, fy, cx, cy = intrinsics_from_fov(60.0, 1920, 1080)
        assert (cx, cy) == (960.0, 540.0)

    def test_wider_fov_gives_smaller_focal_length(self):
        fx_narrow, *_ = intrinsics_from_fov(30.0, 1920, 1080)
        fx_wide, *_ = intrinsics_from_fov(90.0, 1920, 1080)
        assert fx_wide < fx_narrow


class TestLoadCameraSamples:
    def test_missing_file_returns_empty_list(self, tmp_path):
        assert load_camera_samples(tmp_path / "nope.jsonl") == []

    def test_skips_truncated_last_line_without_losing_earlier_ones(self, tmp_path):
        """Real scenario this guards against: the game (or the plugin's
        host process) is killed mid-write, truncating the last JSON line —
        must not lose every earlier, valid sample over one bad line."""
        path = tmp_path / "123.jsonl"
        good = json.dumps({"frame": 0, "wallclock_ms": 1000, "position": [0, 0, 0],
                            "rotation_quaternion": [0, 0, 0, 1], "fov_deg": 60})
        path.write_text(good + "\n" + '{"frame": 1, "wallclock_ms": 1033, trunc')
        samples = load_camera_samples(path)
        assert len(samples) == 1
        assert samples[0]["frame"] == 0

    def test_sorts_by_wallclock_even_if_file_is_out_of_order(self, tmp_path):
        path = tmp_path / "123.jsonl"
        lines = [
            json.dumps({"wallclock_ms": 3000}),
            json.dumps({"wallclock_ms": 1000}),
            json.dumps({"wallclock_ms": 2000}),
        ]
        path.write_text("\n".join(lines))
        samples = load_camera_samples(path)
        assert [s["wallclock_ms"] for s in samples] == [1000, 2000, 3000]


class TestFindCameraLog:
    def test_finds_file_matching_pid(self, tmp_path):
        (tmp_path / "4242.jsonl").write_text("")
        assert find_camera_log(4242, tmp_path) == tmp_path / "4242.jsonl"

    def test_none_when_no_matching_pid(self, tmp_path):
        (tmp_path / "9999.jsonl").write_text("")
        assert find_camera_log(4242, tmp_path) is None


class TestNearestSample:
    def test_finds_closest_within_tolerance(self):
        samples = [{"wallclock_ms": 1000}, {"wallclock_ms": 1050}, {"wallclock_ms": 1100}]
        assert _nearest_sample(1040, samples)["wallclock_ms"] == 1050

    def test_returns_none_when_gap_exceeds_tolerance(self):
        """Real requirement: a frame with no close-enough camera sample
        must be left blank, never silently attached to the wrong pose."""
        samples = [{"wallclock_ms": 1000}, {"wallclock_ms": 5000}]
        assert _nearest_sample(3000, samples) is None

    def test_returns_none_for_empty_samples(self):
        assert _nearest_sample(1000, []) is None


class TestPatchFramesCsv:
    def _write_frames_csv(self, path, rows_timestamps_ms):
        from translator.v2 import V2_FRAME_COLS
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=V2_FRAME_COLS)
            writer.writeheader()
            for i, ts in enumerate(rows_timestamps_ms):
                row = {c: "" for c in V2_FRAME_COLS}
                row["frame_id"] = i
                row["timestamp_ms"] = ts
                writer.writerow(row)

    def test_patches_rows_with_a_close_sample_leaves_others_blank(self, tmp_path):
        frames_csv = tmp_path / "frames.csv"
        self._write_frames_csv(frames_csv, [0, 1000, 5000])  # frame 2 has no sample nearby

        session_json = tmp_path / "session.json"
        session_json.write_text(json.dumps({
            "created_at_utc": "2026-07-31T09:02:05.000000Z",
            "record_width_px": 1920, "record_height_px": 1080,
        }))

        start_epoch_ms = 1785488525000.0  # matches created_at_utc above (not asserted directly)
        camera_log = tmp_path / "cam.jsonl"
        camera_log.write_text("\n".join(json.dumps(s) for s in [
            {"wallclock_ms": start_epoch_ms + 0, "position": [1, 2, 3],
             "rotation_quaternion": [0, 0, 0, 1], "fov_deg": 60},
            {"wallclock_ms": start_epoch_ms + 1000, "position": [4, 5, 6],
             "rotation_quaternion": [0, 0, 0, 1], "fov_deg": 60},
        ]))

        patched = patch_frames_csv(frames_csv, session_json, camera_log)
        assert patched == 2

        with frames_csv.open(newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["c2w_m03"] == "1.0"  # frame 0 -> position x=1
        assert rows[1]["c2w_m03"] == "4.0"  # frame 1000ms -> position x=4
        assert rows[2]["c2w_m03"] == ""     # frame 5000ms -> no close sample, left blank
        assert rows[0]["camera_fx"] == rows[0]["camera_fy"]  # spec's fx==fy criterion

    def test_no_camera_log_patches_nothing(self, tmp_path):
        frames_csv = tmp_path / "frames.csv"
        self._write_frames_csv(frames_csv, [0])
        session_json = tmp_path / "session.json"
        session_json.write_text(json.dumps({
            "created_at_utc": "2026-07-31T09:02:05.000000Z",
            "record_width_px": 1920, "record_height_px": 1080,
        }))
        patched = patch_frames_csv(frames_csv, session_json, tmp_path / "missing.jsonl")
        assert patched == 0
