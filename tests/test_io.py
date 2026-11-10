"""Playback, recording and conversion behaviour, exercised without hardware.

The fixture backend exists for exactly this: the full pipeline runs, receipts are
real objects that validate against their schemas, and the only thing missing is
the sound.
"""

from __future__ import annotations

import os
import tempfile
import unittest
import wave

from audio_runtime.backends import FixturePlayback, resolve_backend
from audio_runtime.backends import omnipickup
from audio_runtime.convert import build_convert_argv, convert, normalize
from audio_runtime.doctor import doctor
from audio_runtime.endpoints import fixture_endpoints, find
from audio_runtime.errors import (
    ConversionError,
    Interrupted,
    RecordingError,
    UsageError,
)
from audio_runtime.models import CAPTURE, RENDER
from audio_runtime.play import dual_play, play
from audio_runtime.record import record
from audio_runtime.schema import validate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "fixtures", "linux-alsa.json")


def make_wav(path: str, seconds: float = 0.05, rate: int = 44100) -> str:
    frames = int(round(rate * seconds))
    with wave.open(path, "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00\x04\x00" * frames)
    return path


class IoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.endpoints = fixture_endpoints(FIXTURE)
        self.wav = make_wav(self.path("tone.wav"))

    def path(self, name: str) -> str:
        return os.path.join(self.directory.name, name)

    def endpoint(self, query: str):
        return find(self.endpoints, query)


class PlayTests(IoTestCase):
    def test_fixture_play_produces_a_valid_receipt(self) -> None:
        receipt = play(self.endpoint("ALSA:CARD1"), self.wav, backend="fixture")

        payload = receipt.to_dict()
        validate(payload, "ark.audio-play-receipt.v1")
        self.assertEqual(receipt.exit_code, 0)
        self.assertEqual(receipt.backend, "fixture")
        self.assertEqual(receipt.repeats, 1)
        self.assertFalse(receipt.degraded)
        self.assertEqual(receipt.command[0], "fixture-play")
        self.assertIn("ALSA:CARD1", receipt.command)
        self.assertRegex(receipt.started_at, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertNotIn("T", receipt.started_at)
        self.assertNotIn("+", receipt.started_at)

    def test_repeat_count_is_honoured_and_reported(self) -> None:
        receipt = play(self.endpoint("ALSA:CARD1"), self.wav, backend="fixture", repeat=3)

        self.assertEqual(receipt.repeats, 3)
        self.assertEqual(receipt.exit_code, 0)

    def test_default_endpoint_requires_an_explicit_flag(self) -> None:
        with self.assertRaises(UsageError) as caught:
            play(self.endpoint("ALSA:CARD0"), self.wav, backend="fixture")
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertIn("--force-default", caught.exception.hint or "")

    def test_default_endpoint_plays_when_forced(self) -> None:
        receipt = play(self.endpoint("ALSA:CARD0"), self.wav, backend="fixture", force_default=True)

        self.assertEqual(receipt.exit_code, 0)

    def test_capture_endpoint_is_rejected_for_playback(self) -> None:
        capture = find(self.endpoints, "ALSA:CARD1", CAPTURE)

        with self.assertRaises(UsageError):
            play(capture, self.wav, backend="fixture", force_default=True)

    def test_missing_file_and_bad_repeat_are_usage_errors(self) -> None:
        with self.assertRaises(UsageError):
            play(self.endpoint("ALSA:CARD1"), self.path("absent.wav"), backend="fixture")
        with self.assertRaises(UsageError):
            play(self.endpoint("ALSA:CARD1"), self.wav, backend="fixture", repeat=0)
        with self.assertRaises(UsageError):
            play(self.endpoint("ALSA:CARD1"), self.wav, backend="fixture", gap=-1)

    def test_dry_run_reports_the_command_without_playing(self) -> None:
        receipt = play(self.endpoint("ALSA:CARD1"), self.wav, backend="fixture", dry_run=True)

        self.assertTrue(any("dry run" in warning for warning in receipt.warnings))
        self.assertEqual(receipt.command[0], "fixture-play")

    def test_dual_play_returns_one_receipt_per_endpoint(self) -> None:
        receipts = dual_play(
            self.endpoint("ALSA:CARD0"),
            self.wav,
            self.endpoint("ALSA:CARD1"),
            self.wav,
            backend="fixture",
            force_default_left=True,
        )

        self.assertEqual(len(receipts), 2)
        self.assertEqual({item.device_key for item in receipts}, {"ALSA:CARD0", "ALSA:CARD1"})
        for item in receipts:
            validate(item.to_dict(), "ark.audio-play-receipt.v1")

    def test_dual_play_refuses_the_same_endpoint_twice(self) -> None:
        same = self.endpoint("ALSA:CARD1")

        with self.assertRaises(UsageError):
            dual_play(same, self.wav, same, self.wav, backend="fixture")


class RecordTests(IoTestCase):
    def test_fixture_recording_writes_the_expected_number_of_bytes(self) -> None:
        target = self.path("take.pcm")

        receipt = record(
            find(self.endpoints, "ALSA:CARD1", CAPTURE),
            target,
            sample_rate=16000,
            channels=2,
            bit_depth=16,
            duration=0.25,
            backend="fixture",
        )

        payload = receipt.to_dict()
        validate(payload, "ark.audio-record-receipt.v1")
        self.assertTrue(os.path.isfile(target))
        expected = 16000 * 2 * 2 * 0.25
        self.assertEqual(os.path.getsize(target), int(expected))
        self.assertEqual(receipt.expected_bytes, int(expected))
        self.assertEqual(receipt.actual_bytes, int(expected))
        self.assertAlmostEqual(receipt.actual_seconds, 0.25, places=3)
        self.assertRegex(receipt.finished_at, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertNotIn("T", receipt.finished_at)

    def test_fixture_recording_needs_a_finite_duration(self) -> None:
        with self.assertRaises(UsageError):
            record(
                find(self.endpoints, "ALSA:CARD1", CAPTURE),
                self.path("endless.pcm"),
                backend="fixture",
                duration=0.0,
            )

    def test_render_endpoint_is_rejected_for_recording(self) -> None:
        with self.assertRaises(UsageError):
            record(self.endpoint("ALSA:CARD1"), self.path("x.pcm"), backend="fixture", duration=0.1)

    def test_dry_run_does_not_touch_the_disk(self) -> None:
        target = self.path("dry.pcm")

        receipt = record(
            find(self.endpoints, "ALSA:CARD1", CAPTURE),
            target,
            backend="fixture",
            duration=0.1,
            dry_run=True,
        )

        self.assertFalse(os.path.exists(target))
        self.assertTrue(any("dry run" in warning for warning in receipt.warnings))

    def test_unknown_backend_and_bad_format_are_usage_errors(self) -> None:
        capture = find(self.endpoints, "ALSA:CARD1", CAPTURE)
        with self.assertRaises(UsageError):
            record(capture, self.path("a.pcm"), backend="arecord")
        with self.assertRaises(UsageError):
            record(capture, self.path("b.pcm"), backend="fixture", bit_depth=12, duration=0.1)


class OmniPickupContractTests(unittest.TestCase):
    def test_host_api_aliases_are_normalised(self) -> None:
        self.assertEqual(omnipickup.normalize_host_api("Windows WASAPI"), "wasapi")
        self.assertEqual(omnipickup.normalize_host_api("WDMKS"), "wdm-ks")
        self.assertEqual(omnipickup.normalize_host_api("directsound"), "directsound")

    def test_unknown_host_api_and_depth_are_usage_errors(self) -> None:
        with self.assertRaises(UsageError):
            omnipickup.normalize_host_api("coreaudio")
        with self.assertRaises(UsageError):
            omnipickup.validate_format(16000, 8, 12)

    def test_record_argv_matches_the_documented_contract(self) -> None:
        argv = omnipickup.build_record_argv(
            "/opt/OmniPickup",
            "out/take.pcm",
            sample_rate=16000,
            channels=8,
            bit_depth=16,
            duration=5,
            host_api="wasapi",
            device_index=3,
            wait_device_name="ListenAI",
            frames_per_buffer=4800,
        )

        self.assertEqual(argv[0], "/opt/OmniPickup")
        self.assertEqual(argv[argv.index("--host-api") + 1], "wasapi")
        self.assertEqual(argv[argv.index("--sample-rate") + 1], "16000")
        self.assertEqual(argv[argv.index("--channels") + 1], "8")
        self.assertEqual(argv[argv.index("--bit-depth") + 1], "16")
        self.assertEqual(argv[argv.index("--duration") + 1], "5")
        self.assertEqual(argv[argv.index("--device-index") + 1], "3")
        self.assertEqual(argv[argv.index("--wait-device-name") + 1], "ListenAI")
        self.assertEqual(argv[argv.index("--frames-per-buffer") + 1], "4800")
        self.assertEqual(argv[argv.index("--output") + 1], "out/take.pcm")

    def test_validate_only_replaces_the_output_flag(self) -> None:
        argv = omnipickup.build_record_argv(
            "/opt/OmniPickup",
            "out/take.pcm",
            sample_rate=16000,
            channels=1,
            bit_depth=16,
            duration=1,
            validate_only=True,
        )

        self.assertIn("--validate-only", argv)
        self.assertNotIn("--output", argv)

    def test_continuous_recording_emits_explicit_zero_duration(self) -> None:
        argv = omnipickup.build_record_argv(
            "/opt/OmniPickup", "out/take.pcm", sample_rate=16000, channels=1, bit_depth=16
        )

        self.assertEqual(argv[argv.index("--duration") + 1], "0")
        self.assertEqual(omnipickup.expected_bytes(16000, 8, 16, 0), 0)

    def test_skill_root_resolves_to_its_own_venv_python(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        os.makedirs(os.path.join(directory.name, "src"))
        os.makedirs(os.path.join(directory.name, ".venv", "Scripts"))
        script = os.path.join(directory.name, "src", "OmniPickup.py")
        python = os.path.join(directory.name, ".venv", "Scripts", "python.exe")
        open(script, "w").close()
        open(python, "w").close()

        resolved = omnipickup.find_executable(directory.name)

        self.assertEqual(omnipickup.executable_argv(resolved), [python, script])

    def test_missing_skill_root_is_a_backend_error(self) -> None:
        with self.assertRaises(Exception) as caught:
            omnipickup.find_executable("/nonexistent/omni-pickup")
        self.assertEqual(getattr(caught.exception, "exit_code", None), 3)

    def test_exit_codes_map_onto_package_errors(self) -> None:
        omnipickup.interpret_exit(0)
        with self.assertRaises(Interrupted) as interrupted:
            omnipickup.interpret_exit(130, "")
        self.assertEqual(interrupted.exception.exit_code, 130)
        with self.assertRaises(RecordingError) as failed:
            omnipickup.interpret_exit(2, "device busy")
        self.assertEqual(failed.exception.exit_code, 8)
        self.assertIn("device busy", failed.exception.message)


class ConvertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def path(self, name: str) -> str:
        return os.path.join(self.directory.name, name)

    def test_argv_drops_video_and_pins_the_bench_format(self) -> None:
        argv = build_convert_argv("in.m4a", "out.wav", ffmpeg="/usr/bin/ffmpeg")

        self.assertEqual(argv[0], "/usr/bin/ffmpeg")
        self.assertIn("-vn", argv)
        self.assertEqual(argv[argv.index("-acodec") + 1], "pcm_s16le")
        self.assertEqual(argv[argv.index("-ac") + 1], "2")
        self.assertEqual(argv[argv.index("-ar") + 1], "44100")
        self.assertEqual(argv[-1], "out.wav")

    def test_dry_run_reports_without_executing(self) -> None:
        source = make_wav(self.path("src.wav"))

        receipt = convert(source, self.path("out.wav"), ffmpeg="ffmpeg", dry_run=True)

        self.assertTrue(any("dry run" in warning for warning in receipt.warnings))
        self.assertFalse(os.path.exists(self.path("out.wav")))
        validate(receipt.to_dict(), "ark.audio-convert-receipt.v1")

    def test_missing_source_is_a_usage_error(self) -> None:
        with self.assertRaises(UsageError):
            convert(self.path("absent.wav"), self.path("out.wav"), dry_run=True)

    def test_absent_ffmpeg_is_a_conversion_error(self) -> None:
        from audio_runtime.convert import ffmpeg_path

        if ffmpeg_path() is not None:
            self.skipTest("ffmpeg is installed on this host")

        with self.assertRaises(ConversionError) as caught:
            build_convert_argv("in.wav", "out.wav")
        self.assertEqual(caught.exception.exit_code, 7)

    def test_normalize_pins_the_bench_standard(self) -> None:
        source = make_wav(self.path("src.wav"))

        receipt = normalize(source, self.path("norm.wav"), ffmpeg="ffmpeg", dry_run=True)

        self.assertEqual(receipt.sample_rate, 44100)
        self.assertEqual(receipt.channels, 2)
        self.assertEqual(receipt.codec, "pcm_s16le")


class BackendAndDoctorTests(unittest.TestCase):
    def test_resolve_backend_by_name_and_rejection_of_unknown(self) -> None:
        self.assertIsInstance(resolve_backend("fixture", "linux"), FixturePlayback)
        with self.assertRaises(UsageError):
            resolve_backend("coreaudio", "linux")

    def test_doctor_payload_validates(self) -> None:
        payload = doctor()

        validate(payload, "ark.audio-doctor.v1")
        self.assertIsInstance(payload["ok"], bool)
        names = [tool["name"] for tool in payload["tools"]]
        self.assertIn("ffmpeg", names)
        self.assertIn("ffprobe", names)


if __name__ == "__main__":
    unittest.main()
