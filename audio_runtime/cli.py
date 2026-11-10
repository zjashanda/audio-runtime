"""Command line entry point.

Thin by design: every subcommand maps an argv namespace onto one function in
this package, validates the payload against its published schema, and prints it.
Business logic never lives here, which is why ``tests/test_cli.py`` can drive the
whole contract with ``main(argv)`` and no subprocess.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, List, Optional, Tuple

from . import __version__
from .convert import TARGET_CHANNELS, TARGET_CODEC, TARGET_RATE, convert, normalize
from .doctor import doctor
from .endpoints import discover, find
from .errors import AudioRuntimeError, UsageError
from .models import CAPTURE, RENDER
from .play import dual_play, play
from .probe import probe
from .record import record
from .render import FORMATS, render, summary_line
from .schema import SCHEMAS, ValidationError, get_schema, list_schemas, validate

PROGRAM = "audio-runtime"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="One stable interface for audio endpoints, inspection, playback, recording and conversion.",
    )
    parser.add_argument("--version", action="version", version="%s %s" % (PROGRAM, __version__))
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_output_options(target: argparse.ArgumentParser) -> None:
        target.add_argument("--format", choices=FORMATS, default="json", help="output rendering")
        target.add_argument("--platform", default="auto", help="auto|windows|linux|darwin")
        target.add_argument("--backend", default="auto", help="backend name (auto picks a native one)")
        target.add_argument("--fixture", default=None, help="replay an endpoint fixture instead of probing hardware")

    endpoints = subparsers.add_parser("endpoints", help="enumerate audio endpoints")
    add_output_options(endpoints)
    endpoints.add_argument("--direction", choices=["all", RENDER, CAPTURE], default="all")

    probe_cmd = subparsers.add_parser("probe", help="inspect one media file")
    probe_cmd.add_argument("path")
    probe_cmd.add_argument("--format", choices=FORMATS, default="json")
    probe_cmd.add_argument("--sample-rate", type=int, default=None, help="raw PCM sample rate")
    probe_cmd.add_argument("--channels", type=int, default=None, help="raw PCM channel count")
    probe_cmd.add_argument("--bit-depth", type=int, default=None, help="raw PCM bit depth")
    probe_cmd.add_argument("--ffprobe", default=None)

    play_cmd = subparsers.add_parser("play", help="play one file on one endpoint")
    add_output_options(play_cmd)
    play_cmd.add_argument("--device", required=True, help="device_key, endpoint_id or unique name substring")
    play_cmd.add_argument("--audio-file", required=True)
    play_cmd.add_argument("--repeat", type=int, default=1)
    play_cmd.add_argument("--gap", type=float, default=0.0)
    play_cmd.add_argument("--force-default", action="store_true", help="allow the system default endpoint")
    play_cmd.add_argument("--dry-run", action="store_true", help="build and report the command without playing")

    dual = subparsers.add_parser("dual-play", help="play two endpoints in parallel")
    add_output_options(dual)
    dual.add_argument("--left-device", required=True)
    dual.add_argument("--left-file", required=True)
    dual.add_argument("--right-device", required=True)
    dual.add_argument("--right-file", required=True)
    dual.add_argument("--repeat", type=int, default=1)
    dual.add_argument("--gap", type=float, default=0.0)
    dual.add_argument("--force-default-left", action="store_true")
    dual.add_argument("--force-default-right", action="store_true")
    dual.add_argument("--dry-run", action="store_true")

    record_cmd = subparsers.add_parser("record", help="capture raw PCM from a capture endpoint")
    add_output_options(record_cmd)
    record_cmd.add_argument("--device", default=None, help="capture device_key; omit with --device-index")
    record_cmd.add_argument("--output", required=True)
    record_cmd.add_argument("--sample-rate", type=int, default=16000)
    record_cmd.add_argument("--channels", type=int, default=8)
    record_cmd.add_argument("--bit-depth", type=int, default=16)
    record_cmd.add_argument("--duration", type=float, default=0.0, help="seconds; 0 means continuous")
    record_cmd.add_argument("--host-api", default="wasapi", help="asio|directsound|wasapi|wdm-ks")
    record_cmd.add_argument("--device-index", type=int, default=None)
    record_cmd.add_argument("--device-name", default=None)
    record_cmd.add_argument("--wait-device-name", default=None)
    record_cmd.add_argument("--device-appear-delay", type=float, default=None)
    record_cmd.add_argument("--frames-per-buffer", type=int, default=None)
    record_cmd.add_argument("--omnipickup", default=None, help="OmniPickup exe or skill root")
    record_cmd.add_argument("--validate-only", action="store_true")
    record_cmd.add_argument("--timeout", type=float, default=None)
    record_cmd.add_argument("--dry-run", action="store_true")

    convert_cmd = subparsers.add_parser("convert", help="convert a media file with ffmpeg")
    convert_cmd.add_argument("source")
    convert_cmd.add_argument("target")
    convert_cmd.add_argument("--format", choices=FORMATS, default="json")
    convert_cmd.add_argument("--sample-rate", type=int, default=TARGET_RATE)
    convert_cmd.add_argument("--channels", type=int, default=TARGET_CHANNELS)
    convert_cmd.add_argument("--codec", default=TARGET_CODEC)
    convert_cmd.add_argument("--ffmpeg", default=None)
    convert_cmd.add_argument("--timeout", type=float, default=600.0)
    convert_cmd.add_argument("--dry-run", action="store_true")

    normalize_cmd = subparsers.add_parser("normalize", help="convert to 44.1 kHz stereo s16le")
    normalize_cmd.add_argument("source")
    normalize_cmd.add_argument("target")
    normalize_cmd.add_argument("--format", choices=FORMATS, default="json")
    normalize_cmd.add_argument("--ffmpeg", default=None)
    normalize_cmd.add_argument("--dry-run", action="store_true")

    doctor_cmd = subparsers.add_parser("doctor", help="report available tools and versions")
    doctor_cmd.add_argument("--format", choices=FORMATS, default="json")

    schema_cmd = subparsers.add_parser("schema", help="print a published schema")
    schema_cmd.add_argument("schema_id", nargs="?", default=None)
    schema_cmd.add_argument("--list", action="store_true")
    schema_cmd.add_argument("--format", choices=FORMATS, default="json")

    return parser


def _endpoints(args: argparse.Namespace, direction: str = "all"):
    payload = discover(
        platform=getattr(args, "platform", "auto"),
        backend=getattr(args, "backend", "auto"),
        direction=direction,
        fixture=getattr(args, "fixture", None),
    )
    validate(payload, "ark.audio-endpoints.v1")
    return payload


def _emit(payload: Any, fmt: str) -> None:
    sys.stdout.write(render(payload, fmt) + "\n")


def _dispatch(args: argparse.Namespace) -> Tuple[Any, int]:
    fmt = getattr(args, "format", "json")
    command = args.command

    if command == "endpoints":
        _emit(_endpoints(args, args.direction), fmt)
        return None, 0

    if command == "probe":
        payload = probe(
            args.path,
            sample_rate=args.sample_rate,
            channels=args.channels,
            bit_depth=args.bit_depth,
            ffprobe=args.ffprobe,
        ).to_dict()
        validate(payload, "ark.media-probe.v1")
        _emit(payload, fmt)
        return None, 0

    if command == "play":
        listing = _endpoints(args, RENDER)
        endpoint = find([_endpoint_record(item) for item in listing["endpoints"]], args.device, RENDER)
        receipt = play(
            endpoint,
            args.audio_file,
            backend=args.backend,
            repeat=args.repeat,
            gap=args.gap,
            force_default=args.force_default,
            dry_run=args.dry_run,
        ).to_dict()
        validate(receipt, "ark.audio-play-receipt.v1")
        _emit(receipt, fmt)
        return None, receipt["exit_code"]

    if command == "dual-play":
        listing = _endpoints(args, RENDER)
        records = [_endpoint_record(item) for item in listing["endpoints"]]
        left = find(records, args.left_device, RENDER)
        right = find(records, args.right_device, RENDER)
        receipts = dual_play(
            left,
            args.left_file,
            right,
            args.right_file,
            backend=args.backend,
            repeat=args.repeat,
            gap=args.gap,
            force_default_left=args.force_default_left,
            force_default_right=args.force_default_right,
            dry_run=args.dry_run,
        )
        payload = {"schema": "ark.audio-play-receipt.v1", "receipts": [item.to_dict() for item in receipts]}
        for item in receipts:
            validate(item.to_dict(), "ark.audio-play-receipt.v1")
        _emit(payload["receipts"] if fmt == "json" else payload, fmt)
        return None, max((item.exit_code for item in receipts), default=0)

    if command == "record":
        if args.device:
            listing = _endpoints(args, CAPTURE)
            endpoint = find([_endpoint_record(item) for item in listing["endpoints"]], args.device, CAPTURE)
        else:
            from .models import AudioEndpoint

            if args.device_index is None and not args.device_name:
                raise UsageError("record needs --device or --device-index/--device-name")
            endpoint = AudioEndpoint(
                endpoint_id="direct-%s" % (args.device_index if args.device_index is not None else args.device_name),
                device_key="DIRECT:%s" % (args.device_index if args.device_index is not None else args.device_name),
                direction=CAPTURE,
                platform=args.platform if args.platform != "auto" else "windows",
                name=args.device_name or "",
                channels=args.channels,
                default_sample_rate=args.sample_rate,
                host_api=args.host_api,
                backend_target=str(args.device_index or ""),
                source="direct",
            )
        receipt = record(
            endpoint,
            args.output,
            sample_rate=args.sample_rate,
            channels=args.channels,
            bit_depth=args.bit_depth,
            duration=args.duration,
            host_api=args.host_api,
            backend="fixture" if args.backend == "fixture" else "omnipickup",
            omnipickup_path=args.omnipickup,
            device_index=args.device_index,
            device_name=args.device_name,
            wait_device_name=args.wait_device_name,
            device_appear_delay=args.device_appear_delay,
            frames_per_buffer=args.frames_per_buffer,
            validate_only=args.validate_only,
            dry_run=args.dry_run,
            timeout=args.timeout,
        ).to_dict()
        validate(receipt, "ark.audio-record-receipt.v1")
        _emit(receipt, fmt)
        return None, 0

    if command in ("convert", "normalize"):
        function = normalize if command == "normalize" else convert
        receipt = function(
            args.source,
            args.target,
            sample_rate=getattr(args, "sample_rate", TARGET_RATE),
            channels=getattr(args, "channels", TARGET_CHANNELS),
            codec=getattr(args, "codec", TARGET_CODEC),
            ffmpeg=args.ffmpeg,
            dry_run=args.dry_run,
        ).to_dict()
        validate(receipt, "ark.audio-convert-receipt.v1")
        _emit(receipt, fmt)
        return None, 0

    if command == "doctor":
        payload = doctor()
        validate(payload, "ark.audio-doctor.v1")
        _emit(payload, fmt)
        return None, 0 if payload["ok"] else 3

    if command == "schema":
        if args.list or not args.schema_id:
            _emit({"schemas": list_schemas()}, fmt)
            return None, 0
        try:
            payload = get_schema(args.schema_id)
        except ValidationError as exc:
            raise UsageError(str(exc), hint="Run `audio-runtime schema --list` to see the published ids.") from exc
        _emit(payload, fmt)
        return None, 0

    raise UsageError("unknown command: %s" % command)


def _endpoint_record(raw: Any):
    from .models import AudioEndpoint

    if isinstance(raw, AudioEndpoint):
        return raw
    return AudioEndpoint.from_dict(raw)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload, code = _dispatch(args)
    except AudioRuntimeError as exc:
        _emit(exc.as_dict(), "json" if getattr(args, "format", "json") == "json" else "plain")
        if exc.hint:
            sys.stderr.write("%s: %s\nhint: %s\n" % (PROGRAM, exc.message, exc.hint))
        else:
            sys.stderr.write("%s: %s\n" % (PROGRAM, exc.message))
        return exc.exit_code
    except BrokenPipeError:  # pragma: no cover - piping into head
        return 0
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
