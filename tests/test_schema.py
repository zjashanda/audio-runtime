import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_runtime.schema import (  # noqa: E402
    SCHEMAS,
    ValidationError,
    get_schema,
    list_schemas,
    validate,
)


class SchemaTests(unittest.TestCase):
    def test_every_schema_is_self_describing(self):
        self.assertGreaterEqual(len(list_schemas()), 6)
        for schema_id in list_schemas():
            schema = get_schema(schema_id)
            self.assertEqual(schema["$id"], schema_id)
            self.assertEqual(schema["type"], "object")
            self.assertIn("title", schema)

    def test_unknown_schema_is_rejected(self):
        with self.assertRaises(ValidationError):
            get_schema("ark.nope.v1")

    def test_endpoint_payload_accepts_a_real_record(self):
        payload = {
            "endpoint_id": "omnipickup-32-capture",
            "device_key": "VID_2886&PID_0018",
            "direction": "capture",
            "platform": "windows",
            "name": "麦克风 (ListenAI Audio)",
            "channels": 8,
        }
        validate(payload, "ark.audio-endpoint.v1")

    def test_missing_required_key_is_reported_with_a_path(self):
        payload = {"endpoint_id": "x", "device_key": "k", "direction": "render"}
        with self.assertRaises(ValidationError) as caught:
            validate(payload, "ark.audio-endpoint.v1")
        self.assertIn("platform", str(caught.exception))

    def test_enum_violation_is_reported(self):
        payload = {
            "endpoint_id": "x",
            "device_key": "k",
            "direction": "monitor",
            "platform": "windows",
            "name": "n",
        }
        with self.assertRaises(ValidationError):
            validate(payload, "ark.audio-endpoint.v1")

    def test_timestamp_pattern_rejects_offsetless_time(self):
        payload = {
            "schema": "ark.audio-endpoints.v1",
            "platform": "linux",
            "backend": "fixture",
            "generated_at": "2026-11-10T21:55:03",
            "endpoints": [],
        }
        with self.assertRaises(ValidationError):
            validate(payload, "ark.audio-endpoints.v1")

    def test_receipt_timestamp_accepts_beijing_offset(self):
        payload = {
            "device_key": "ALSA:CARD0",
            "endpoint_id": "alsa-card0-dev0-render",
            "file": "tone.wav",
            "backend": "fixture",
            "started_at": "2026-11-10T21:55:03.123+08:00",
            "repeats": 1,
            "degraded": False,
        }
        validate(payload, "ark.audio-play-receipt.v1")

    def test_error_payload_shape(self):
        validate({"code": "usage", "message": "bad flag", "exit_code": 2}, "ark.audio-error.v1")

    def test_integer_rejects_bool(self):
        payload = {
            "device_key": "k",
            "endpoint_id": "e",
            "file": "f",
            "backend": "fixture",
            "started_at": "2026-11-10T21:55:03.123+08:00",
            "repeats": True,
            "degraded": False,
        }
        with self.assertRaises(ValidationError):
            validate(payload, "ark.audio-play-receipt.v1")

    def test_every_schema_is_json_serialisable(self):
        for schema in SCHEMAS.values():
            json.dumps(schema, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
