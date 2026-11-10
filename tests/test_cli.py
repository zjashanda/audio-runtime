"""The CLI contract, driven through ``main(argv)`` with no subprocess.

Every subcommand must (a) print something that parses, (b) validate against the
schema it claims, and (c) return an exit code that matches the exit-code table in
``docs/interface.md``. Failures are JSON too, because a caller in CI should never
have to scrape stderr.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
import wave

from audio_runtime.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "fixtures", "linux-alsa.json")
WINDOWS_FIXTURE = os.path.join(ROOT, "fixtures", "windows-omnipickup.json")


def make_wav(path: str, seconds: float = 0.05, rate: int = 44100) -> str:
    frames = int(round(rate * seconds))
    with wave.open(path, "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00\x04\x00" * frames)
    return path


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.wav = make_wav(self.path("tone.wav"))

    def path(self, name: str) -> str:
        return os.path.join(self.directory.name, name)

    def run_cli(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def run_json(self, argv):
        code, out, err = self.run_cli(argv)
        return code, json.loads(out), err


class SchemaAndDoctorTests(CliTestCase):
    def test_schema_list_names_every_schema(self) -> None:
        code, payload, _ = self.run_json(["schema", "--list"])

        self.assertEqual(code, 0)
        listed = payload["schemas"]
        for expected in (
            "ark.audio-endpoints.v1",
            "ark.media-probe.v1",
            "ark.audio-play-receipt.v1",
            "ark.audio-record-receipt.v1",
            "ark.audio-convert-receipt.v1",
            "ark.audio-doctor.v1",
        ):
            self.assertIn(expected, listed)

    def test_single_schema_and_unknown_schema(self) -> None:
        code, payload, _ = self.run_json(["schema", "ark.audio-endpoint.v1"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["$id"], "ark.audio-endpoint.v1")

        code, payload, stderr = self.run_json(["schema", "ark.nope.v1"])
        self.assertEqual(code, 2)
        self.assertEqual(payload["exit_code"], 2)
        self.assertIn("unknown schema", stderr)

    def test_doctor_reports_a_json_payload(self) -> None:
        code, out, _ = self.run_cli(["doctor", "--format", "json"])

        self.assertIn(code, (0, 3))
        payload = json.loads(out)
        self.assertEqual(payload["schema"], "ark.audio-doctor.v1")


class EndpointTests(CliTestCase):
    def test_fixture_endpoints_render_all_directions(self) -> None:
        code, payload, _ = self.run_json(
            ["endpoints", "--backend", "fixture", "--fixture", FIXTURE]
        )

        self.assertEqual(code, 0)
        self.assertEqual(payload["schema"], "ark.audio-endpoints.v1")
        self.assertEqual(len(payload["endpoints"]), 3)

    def test_direction_filter_narrows_the_list(self) -> None:
        code, payload, _ = self.run_json(
            ["endpoints", "--backend", "fixture", "--fixture", FIXTURE, "--direction", "capture"]
        )

        self.assertEqual(code, 0)
        self.assertEqual(len(payload["endpoints"]), 1)
        self.assertEqual(payload["endpoints"][0]["direction"], "capture")

    def test_windows_fixture_keeps_host_api_and_channels(self) -> None:
        code, payload, _ = self.run_json(
            ["endpoints", "--backend", "fixture", "--fixture", WINDOWS_FIXTURE]
        )

        self.assertEqual(code, 0)
        capture = [item for item in payload["endpoints"] if item["direction"] == "capture"]
        self.assertEqual(len(capture), 2)
        self.assertTrue(any(item["channels"] == 8 for item in capture))

    def test_fixture_backend_without_a_fixture_file_fails_cleanly(self) -> None:
        code, payload, _ = self.run_json(["endpoints", "--backend", "fixture"])

        self.assertEqual(code, 2)
        self.assertIn("--fixture", payload["hint"])

    def test_table_rendering_is_not_json(self) -> None:
        code, out, _ = self.run_cli(
            ["endpoints", "--backend", "fixture", "--fixture", FIXTURE, "--format", "table"]
        )

        self.assertEqual(code, 0)
        self.assertIn("DEVICE_KEY", out)


class ProbeTests(CliTestCase):
    def test_probe_a_wav(self) -> None:
        code, payload, _ = self.run_json(["probe", self.wav])

        self.assertEqual(code, 0)
        self.assertEqual(payload["sample_rate"], 44100)
        self.assertFalse(payload["raw_pcm"])

    def test_probe_raw_pcm_with_triple(self) -> None:
        raw = self.path("take.pcm")
        with open(raw, "wb") as handle:
            handle.write(b"\x00" * 1600)

        code, payload, _ = self.run_json(
            ["probe", raw, "--sample-rate", "8000", "--channels", "1", "--bit-depth", "16"]
        )

        self.assertEqual(code, 0)
        self.assertTrue(payload["raw_pcm"])
        self.assertAlmostEqual(payload["duration_seconds"], 0.1, places=6)

    def test_probe_missing_file_returns_exit_2(self) -> None:
        code, payload, stderr = self.run_json(["probe", self.path("absent.wav")])

        self.assertEqual(code, 2)
        self.assertEqual(payload["code"], "usage")


class PlayTests(CliTestCase):
    def play_args(self, device: str, *extra: str):
        return [
            "play",
            "--backend",
            "fixture",
            "--fixture",
            FIXTURE,
            "--device",
            device,
            "--audio-file",
            self.wav,
            *extra,
        ]

    def test_play_returns_a_receipt_and_the_backend_exit_code(self) -> None:
        code, payload, _ = self.run_json(self.play_args("ALSA:CARD1"))

        self.assertEqual(code, 0)
        self.assertEqual(payload["exit_code"], 0)
        self.assertEqual(payload["device_key"], "ALSA:CARD1")

    def test_default_endpoint_without_force_fails(self) -> None:
        code, payload, _ = self.run_json(self.play_args("ALSA:CARD0"))

        self.assertEqual(code, 2)
        self.assertIn("--force-default", payload["hint"])

    def test_default_endpoint_with_force_succeeds(self) -> None:
        code, payload, _ = self.run_json(self.play_args("ALSA:CARD0", "--force-default"))

        self.assertEqual(code, 0)
        self.assertTrue(payload["device_key"] == "ALSA:CARD0")

    def test_ambiguous_name_fails_instead_of_guessing(self) -> None:
        twin = self.path("twins.json")
        with open(twin, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "platform": "windows",
                    "endpoints": [
                        {
                            "endpoint_id": "left",
                            "device_key": "WASAPI:LEFT",
                            "direction": "render",
                            "platform": "windows",
                            "name": "ListenAI Speaker A",
                        },
                        {
                            "endpoint_id": "right",
                            "device_key": "WASAPI:RIGHT",
                            "direction": "render",
                            "platform": "windows",
                            "name": "ListenAI Speaker B",
                        },
                    ],
                },
                handle,
            )

        code, payload, _ = self.run_json(
            [
                "play",
                "--backend",
                "fixture",
                "--fixture",
                twin,
                "--device",
                "ListenAI",
                "--audio-file",
                self.wav,
            ]
        )

        self.assertEqual(code, 5)
        self.assertEqual(payload["code"], "device-ambiguous")

    def test_dual_play_emits_one_receipt_per_endpoint(self) -> None:
        code, payload, _ = self.run_json(
            [
                "dual-play",
                "--backend",
                "fixture",
                "--fixture",
                FIXTURE,
                "--left-device",
                "ALSA:CARD0",
                "--force-default-left",
                "--left-file",
                self.wav,
                "--right-device",
                "ALSA:CARD1",
                "--right-file",
                self.wav,
            ]
        )

        self.assertEqual(code, 0)
        self.assertEqual(len(payload), 2)
        self.assertEqual({item["device_key"] for item in payload}, {"ALSA:CARD0", "ALSA:CARD1"})


class RecordTests(CliTestCase):
    def test_record_fixture_writes_a_file_and_a_receipt(self) -> None:
        target = self.path("take.pcm")

        code, payload, _ = self.run_json(
            [
                "record",
                "--backend",
                "fixture",
                "--fixture",
                FIXTURE,
                "--device",
                "ALSA:CARD1:IN",
                "--output",
                target,
                "--duration",
                "0.1",
                "--sample-rate",
                "8000",
                "--channels",
                "1",
            ]
        )

        self.assertEqual(code, 0)
        self.assertEqual(payload["actual_bytes"], 1600)
        self.assertEqual(os.path.getsize(target), 1600)

    def test_record_without_a_device_fails(self) -> None:
        code, payload, _ = self.run_json(
            ["record", "--backend", "fixture", "--output", self.path("x.pcm"), "--duration", "0.1"]
        )

        self.assertEqual(code, 2)
        self.assertIn("--device", payload["message"])


class ConvertTests(CliTestCase):
    def test_convert_dry_run_and_normalize(self) -> None:
        code, payload, _ = self.run_json(
            ["convert", self.wav, self.path("out.wav"), "--ffmpeg", "ffmpeg", "--dry-run"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["codec"], "pcm_s16le")

        code, payload, _ = self.run_json(
            ["normalize", self.wav, self.path("norm.wav"), "--ffmpeg", "ffmpeg", "--dry-run"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["sample_rate"], 44100)
        self.assertEqual(payload["channels"], 2)


class ProcessLevelTests(CliTestCase):
    def test_version_exits_zero(self) -> None:
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stdout(io.StringIO()):
            main(["--version"])

        self.assertEqual(caught.exception.code, 0)

    def test_unknown_command_exits_two(self) -> None:
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
            main(["frobnicate"])

        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
