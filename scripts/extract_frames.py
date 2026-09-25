#!/usr/bin/env python3
"""Extract numbered JPEG frames from a live RTSP/UDP stream.

Quick manual alternatives:
    ffmpeg -i "rtsp://USER:PASS@HOST/stream" -vf fps=1 -q:v 2 frames/frame_%06d.jpg
    ffmpeg -i "udp://@:5000" -vf fps=1 -q:v 2 frames/frame_%06d.jpg

The script keeps ffmpeg running until the stream ends or it is interrupted.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence


DEFAULT_INTERVAL = 1.0


def positive_float(value: str) -> float:
    """Parse a strictly positive floating-point CLI value."""
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"must be a number, got {value!r}") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def positive_int(value: str) -> int:
    """Parse a strictly positive integer CLI value."""
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"must be an integer, got {value!r}") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def jpeg_quality(value: str) -> int:
    """Parse ffmpeg's JPEG q:v range (lower is better quality)."""
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"must be an integer, got {value!r}") from exc
    if not 1 <= number <= 31:
        raise argparse.ArgumentTypeError("must be between 1 and 31")
    return number


def resolution_filter(value: str) -> str:
    """Turn WIDTH or WIDTHxHEIGHT into an ffmpeg scale filter."""
    value = value.strip().lower()
    if value.isdigit() and int(value) > 0:
        return f"scale={int(value)}:-2"

    if "x" in value:
        width, height = value.split("x", 1)
        if width.isdigit() and height.isdigit() and int(width) > 0 and int(height) > 0:
            return f"scale={int(width)}:{int(height)}"

    raise argparse.ArgumentTypeError(
        "must be a positive width (for example 1280) or WIDTHxHEIGHT (for example 1920x1080)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract numbered JPEG frames from a live RTSP/UDP stream using ffmpeg. "
            "The stream is sampled with an ffmpeg fps filter."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        metavar="URL_OR_PATH",
        help="input stream URL or video path (for example rtsp://... or udp://@:5000)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("./frames"),
        metavar="DIR",
        help="directory for frame_XXXXXX.jpg files",
    )
    parser.add_argument(
        "--interval",
        type=positive_float,
        default=DEFAULT_INTERVAL,
        metavar="SECONDS",
        help="seconds between extracted frames; 0.5–1.0 is a useful live-stream range",
    )
    parser.add_argument(
        "--fps",
        type=positive_float,
        metavar="RATE",
        help="sampling rate in frames per second; overrides --interval when supplied",
    )
    parser.add_argument(
        "--resolution",
        type=resolution_filter,
        metavar="WIDTH[xHEIGHT]",
        help="scale frames to a width (for example 1280) or exact WIDTHxHEIGHT",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=jpeg_quality,
        default=2,
        metavar="1-31",
        help="ffmpeg JPEG quality (lower is better; 2 is high quality)",
    )
    parser.add_argument(
        "--start-index",
        type=positive_int,
        default=1,
        metavar="N",
        help="number for the first output frame",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="overwrite existing numbered JPEGs (default: stop rather than overwrite)",
    )
    return parser


def run_ffmpeg(args: argparse.Namespace) -> int:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        print(
            "error: ffmpeg was not found on PATH; install ffmpeg and try again",
            file=sys.stderr,
        )
        return 1

    rate = args.fps if args.fps is not None else 1.0 / args.interval
    filters = [f"fps={rate:.12g}"]
    if args.resolution:
        filters.append(args.resolution)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_pattern = args.output_dir / "frame_%06d.jpg"

    command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-i",
        args.input,
        "-map",
        "0:v:0",
        "-vf",
        ",".join(filters),
        "-q:v",
        str(args.jpeg_quality),
        "-start_number",
        str(args.start_index),
        "-y" if args.overwrite else "-n",
        str(output_pattern),
    ]

    print(f"Starting ffmpeg; writing frames to {output_pattern}", file=sys.stderr)
    print("Press Ctrl-C to stop.", file=sys.stderr)
    process = subprocess.Popen(command)
    try:
        return process.wait()
    except KeyboardInterrupt:
        print("\nStopping ffmpeg...", file=sys.stderr)
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("ffmpeg did not stop promptly; terminating it.", file=sys.stderr)
            process.kill()
            process.wait()
        return 130


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_ffmpeg(args)


if __name__ == "__main__":
    sys.exit(main())
