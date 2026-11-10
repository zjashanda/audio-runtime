import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from audio_runtime.endpoints import (  # noqa: E402
    device_key_for,
    discover,
    fixture_endpoints,
    find,
    parse_alsa_listing,
    parse_omnipickup_listing,
    parse_powershell_endpoints,
    require_explicit_default,
    resolve_platform,
    slug,
)
from audio_runtime.errors import DeviceAmbiguous, DeviceNotFound, UsageError  # noqa: E402

WINDOWS_FIXTURE = os.path.join(ROOT, "fixtures", "windows-omnipickup.json")
LINUX_FIXTURE = os.path.join(ROOT, "fixtures", "linux-alsa.json")

OMNI_LISTING = """
=== WASAPI ===
    --32
      麦克风 (ListenAI Audio)
        host_api=Windows WASAPI inputs=8 outputs=0 default_rate=16000
=== DirectSound ===
    --5
      扬声器 (ListenAI Audio)
        host_api=Windows DirectSound inputs=0 outputs=2 default_rate=44100
""".strip()

APLAY_LISTING = """
**** List of PLAYBACK Hardware Devices ****
card 0: PCH [HDA Intel PCH], device 0: ALC257 Analog [ALC257 Analog]
  Subdevices: 1/1
card 1: Device [USB Audio Device], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
""".strip()

PW_JSON = json.dumps(
    [
        {
            "Name": "扬声器 (ListenAI Audio)",
            "DeviceID": r"SWD\MMDEVAPI\{0.0.0.00000000}.{11111111-2222-3333-4444-555555555555}",
            "Manufacturer": "ListenAI",
            "Status": "OK",
        },
        {
            "Name": "麦克风 (ListenAI Audio)",
            "DeviceID": r"SWD\MMDEVAPI\{0.0.1.00000000}.{66666666-7777-8888-9999-000000000000}",
            "Manufacturer": "ListenAI",
            "Status": "OK",
        },
    ]
)


class KeyTests(unittest.TestCase):
    def test_slug_strips_non_ascii_and_upper_cases(self):
        self.assertEqual(slug("麦克风 (ListenAI Audio)"), "LISTENAI-AUDIO")

    def test_pnp_token_beats_a_label(self):
        key = device_key_for("麦克风 (ListenAI Audio) (VID_2886&PID_0018)", host_api="WASAPI")
        self.assertEqual(key, "VID_2886&PID_0018")

    def test_alsa_card_key_is_used_without_a_token(self):
        self.assertEqual(device_key_for("USB Audio Device", host_api="ALSA", card_index=1), "ALSA:CARD1")

    def test_host_api_prefixed_label_is_the_last_resort(self):
        self.assertEqual(device_key_for("Speakers", host_api="Windows WASAPI"), "WASAPI:SPEAKERS")


class ParserTests(unittest.TestCase):
    def test_omnipickup_listing_splits_inputs_and_outputs(self):
        entries = parse_omnipickup_listing(OMNI_LISTING)
        self.assertEqual(len(entries), 2)
        capture = [entry for entry in entries if entry.direction == "capture"][0]
        render = [entry for entry in entries if entry.direction == "render"][0]
        self.assertEqual(capture.channels, 8)
        self.assertEqual(capture.default_sample_rate, 16000)
        self.assertEqual(capture.backend_target, "32")
        self.assertEqual(render.channels, 2)
        self.assertEqual(render.host_api, "Windows DirectSound")
        self.assertTrue(render.extra["render_capability_only"])

    def test_omnipickup_listing_is_empty_for_noise(self):
        self.assertEqual(parse_omnipickup_listing("PyAudio is not installed"), [])

    def test_alsa_listing_builds_plughw_targets(self):
        entries = parse_alsa_listing(APLAY_LISTING, direction="render")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].backend_target, "plughw:PCH,0")
        self.assertEqual(entries[0].device_key, "ALSA:CARD0")
        self.assertEqual(entries[1].card_index, 1)
        self.assertTrue(entries[0].is_default)
        self.assertFalse(entries[1].is_default)

    def test_powershell_json_maps_data_flow_to_direction(self):
        entries = parse_powershell_endpoints(PW_JSON)
        directions = sorted(entry.direction for entry in entries)
        self.assertEqual(directions, ["capture", "render"])
        self.assertTrue(all(entry.platform == "windows" for entry in entries))

    def test_platform_resolution_rejects_nonsense(self):
        with self.assertRaises(UsageError):
            resolve_platform("solaris")


class DiscoveryTests(unittest.TestCase):
    def test_windows_fixture_replay(self):
        payload = discover(platform="auto", backend="fixture", fixture=WINDOWS_FIXTURE)
        self.assertEqual(payload["schema"], "ark.audio-endpoints.v1")
        self.assertEqual(payload["backend"], "fixture")
        self.assertEqual(len(payload["endpoints"]), 3)

    def test_direction_filter(self):
        payload = discover(backend="fixture", fixture=WINDOWS_FIXTURE, direction="capture")
        self.assertTrue(all(item["direction"] == "capture" for item in payload["endpoints"]))
        self.assertEqual(len(payload["endpoints"]), 2)

    def test_fixture_backend_requires_a_path(self):
        with self.assertRaises(UsageError):
            discover(backend="fixture")

    def test_unknown_backend_is_a_usage_error(self):
        with self.assertRaises(UsageError):
            discover(backend="telepathy", fixture=WINDOWS_FIXTURE)

    def test_fixture_entries_load_as_endpoints(self):
        entries = fixture_endpoints(LINUX_FIXTURE)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0].device_key, "ALSA:CARD0")


class FindTests(unittest.TestCase):
    def setUp(self):
        self.entries = fixture_endpoints(WINDOWS_FIXTURE)

    def test_exact_device_key_wins(self):
        entry = find(self.entries, "VID_2886&PID_0018")
        self.assertEqual(entry.direction, "capture")

    def test_endpoint_id_is_accepted(self):
        entry = find(self.entries, "omnipickup-12-render")
        self.assertEqual(entry.device_key, "WASAPI:SPEAKERS-LISTENAI-AUDIO")

    def test_unique_name_substring_is_accepted(self):
        entry = find(self.entries, "Realtek")
        self.assertEqual(entry.device_key, "WASAPI:MIC-REALTEK-AUDIO")

    def test_ambiguous_name_fails_instead_of_guessing(self):
        with self.assertRaises(DeviceAmbiguous) as caught:
            find(self.entries, "ListenAI")
        self.assertIn("2 endpoints", str(caught.exception))

    def test_missing_device_lists_what_is_available(self):
        with self.assertRaises(DeviceNotFound) as caught:
            find(self.entries, "nonexistent")
        self.assertIn("VID_2886&PID_0018", caught.exception.message)

    def test_direction_constrains_the_search(self):
        with self.assertRaises(DeviceNotFound):
            find(self.entries, "VID_2886&PID_0018", direction="render")

    def test_system_default_needs_explicit_permission(self):
        entry = find(self.entries, "WASAPI:SPEAKERS-LISTENAI-AUDIO")
        with self.assertRaises(UsageError):
            require_explicit_default(entry, force_default=False)
        require_explicit_default(entry, force_default=True)


if __name__ == "__main__":
    unittest.main()
