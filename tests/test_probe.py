"""Probe tests built on files we generate ourselves.

Every expectation here is arithmetic we can check by hand: a 0.05 s stereo WAV
is 8820 frames, full-scale samples must read as clipping, and a header-less file
is only meaningful together with the format triple it was probed with.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
import wave

from audio_runtime.errors import ProbeError, UsageError
from audio_runtime.probe import (
    probe,
    read_wav_format,
    sha256_file,
    wav_duration_from_pcm,
)
from audio_runtime.schema import validate


def write_wav(path: str, frames: bytes, rate: int = 44100, channels: int = 2, width: int = 2) -> None:
    with wave.open(path, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(frames)


def write_silence(path: str, seconds: float = 0.05, rate: int = 44100, channels: int = 2) -> None:
    frames = int(round(rate * seconds))
    write_wav(path, b"\x00\x00" * channels * frames, rate=rate, channels=channels)


def write_full_scale(path: str, seconds: float = 0.05, rate: int = 44100, channels: int = 2) -> None:
    frames = int(round(rate * seconds))
    sample = (32767).to_bytes(2, "little", signed=True)
    write_wav(path, sample * channels * frames, rate=rate, channels=channels)


class WavTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def path(self, name: str) -> str:
        return os.path.join(self.directory.name, name)

    def test_silent_wav_reports_format_and_no_audible_signal(self) -> None:
        target = self.path("silence.wav")
        write_silence(target)

        result = probe(target)

        self.assertFalse(result.raw_pcm)
        self.assertEqual(result.sample_rate, 44100)
        self.assertEqual(result.channels, 2)
        self.assertEqual(result.bit_depth, 16)
        self.assertAlmostEqual(result.duration_seconds, 0.05, places=4)
        self.assertEqual(result.container, "wav")
        self.assertEqual(result.codec, "pcm_s16le")
        self.assertEqual(result.clipping_status, "unknown")
        self.assertIsNone(result.non_silent)
        self.assertIsNone(result.peak_volume_db)
        self.assertIsNone(result.mean_volume_db)

    def test_full_scale_wav_is_reported_as_clipping(self) -> None:
        target = self.path("loud.wav")
        write_full_scale(target)

        result = probe(target)

        self.assertEqual(result.clipping_status, "clipping")
        self.assertTrue(result.non_silent)
        self.assertGreaterEqual(result.peak_level, 0.999)
        self.assertEqual(result.mean_level, result.peak_level)

    def test_probe_payload_satisfies_its_schema(self) -> None:
        target = self.path("ok.wav")
        write_silence(target)

        payload = probe(target).to_dict()

        validate(payload, "ark.media-probe.v1")
        self.assertRegex(payload["sha256"], r"^[0-9a-f]{64}$")

    def test_sha256_matches_the_standard_library(self) -> None:
        target = self.path("digest.wav")
        write_silence(target)
        with open(target, "rb") as handle:
            expected = hashlib.sha256(handle.read()).hexdigest()

        self.assertEqual(sha256_file(target), expected)

    def test_missing_file_is_a_usage_error(self) -> None:
        with self.assertRaises(UsageError) as caught:
            probe(self.path("absent.wav"))
        self.assertEqual(caught.exception.exit_code, 2)

    def test_broken_wav_header_is_a_probe_error(self) -> None:
        target = self.path("broken.wav")
        with open(target, "wb") as handle:
            handle.write(b"RIFF" + (100).to_bytes(4, "little") + b"WAVE" + b"garbage" * 8)

        with self.assertRaises(ProbeError):
            probe(target)

    def test_non_wav_without_ffprobe_is_a_probe_error(self) -> None:
        target = self.path("noise.bin")
        with open(target, "wb") as handle:
            handle.write(b"\x7f" * 4096)

        with self.assertRaises(ProbeError) as caught:
            probe(target, ffprobe="/nonexistent/ffprobe")

        self.assertIn("--sample-rate", caught.exception.hint or "")

    def test_wav_format_helper_and_rejection_of_non_wav(self) -> None:
        target = self.path("format.wav")
        write_silence(target, rate=16000, channels=1)

        self.assertEqual(read_wav_format(target), (16000, 1, 16))

        other = self.path("raw.pcm")
        with open(other, "wb") as handle:
            handle.write(b"\x00" * 64)
        with self.assertRaises(ProbeError):
            read_wav_format(other)


class RawPcmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def path(self, name: str) -> str:
        return os.path.join(self.directory.name, name)

    def test_explicit_triple_describes_headerless_pcm(self) -> None:
        target = self.path("take.pcm")
        # 0.1 s of 8 kHz mono 16-bit is 1600 bytes, one frame is 2 bytes.
        with open(target, "wb") as handle:
            handle.write(b"\x00" * 1600)

        result = probe(target, sample_rate=8000, channels=1, bit_depth=16)

        self.assertTrue(result.raw_pcm)
        self.assertEqual(result.sample_rate, 8000)
        self.assertEqual(result.channels, 1)
        self.assertEqual(result.bit_depth, 16)
        self.assertAlmostEqual(result.duration_seconds, 0.1, places=6)
        self.assertEqual(result.container, "raw")
        self.assertEqual(result.codec, "pcm_s16le")
        self.assertEqual(result.warnings, [])
        validate(result.to_dict(), "ark.media-probe.v1")

    def test_partial_frame_is_reported_not_hidden(self) -> None:
        target = self.path("short.pcm")
        with open(target, "wb") as handle:
            handle.write(b"\x00" * 1601)

        result = probe(target, sample_rate=8000, channels=1, bit_depth=16)

        self.assertTrue(any("whole number" in warning for warning in result.warnings))

    def test_duration_of_a_pcm_file_is_pure_arithmetic(self) -> None:
        self.assertAlmostEqual(wav_duration_from_pcm(176400, 44100, 2, 16), 1.0, places=6)
        self.assertEqual(wav_duration_from_pcm(1000, 0, 2, 16), 0.0)


if __name__ == "__main__":
    unittest.main()
