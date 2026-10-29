#!/usr/bin/env python3
"""
CLI: read a commander list and write a combined EDHREC card dump per commander
into wipcoms/. Logic lives in edhparse.services.wipcoms.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.config import RunConfig
from edhparse.services.wipcoms import (
    DEFAULT_MIN_INCLUSION,
    DEFAULT_MIN_LAND_INCLUSION,
    DEFAULT_MIN_LAND_SYNERGY,
    fetch_wipcoms,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch all EDHREC cards (average/budget/expensive × default + top tags) into wipcoms/.",
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=Path("inputs/wantcoms"),
        help="Commander list file (EDHREC URLs or names). Default: inputs/wantcoms",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("wipcoms"),
        help="Directory for per-commander output files. Default: wipcoms/",
    )
    parser.add_argument(
        "--max-themes",
        type=int,
        default=5,
        help="Number of top EDHREC theme tags to include (default: 5).",
    )
    parser.add_argument(
        "--fresh-cache",
        action="store_true",
        help="Clear the HTTP cache before fetching.",
    )
    parser.add_argument(
        "--min-inclusion",
        type=float,
        default=DEFAULT_MIN_INCLUSION,
        help="Non-land cards must exceed this inclusion %% (default: 5).",
    )
    parser.add_argument(
        "--min-land-synergy",
        type=float,
        default=DEFAULT_MIN_LAND_SYNERGY,
        help="Land cards pass if synergy exceeds this %% (default: 50).",
    )
    parser.add_argument(
        "--min-land-inclusion",
        type=float,
        default=DEFAULT_MIN_LAND_INCLUSION,
        help="Land cards also pass if inclusion exceeds this %% (default: 30).",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress per-URL fetch details.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = RunConfig(fresh_cache=args.fresh_cache, max_themes=args.max_themes)
    result = fetch_wipcoms(
        args.input,
        args.output_dir,
        cfg,
        max_themes=args.max_themes,
        min_inclusion=args.min_inclusion,
        min_land_synergy=args.min_land_synergy,
        min_land_inclusion=args.min_land_inclusion,
        quiet=args.quiet,
    )
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
