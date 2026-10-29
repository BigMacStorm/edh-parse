#!/usr/bin/env python3
"""
CLI: deduplicate a combined decklist to singleton (1 copy per card).
Logic lives in edhparse.services.singleton.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from edhparse.services.singleton import (
    folder_to_commander,
    parse_lines,
    process_file,
    run_singleton,
    singletonize,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deduplicate a combined decklist to singleton (1 of each card).",
        epilog=(
            "Examples:\n"
            "  python scripts/singleton_deck.py wip/gev/raw.txt -o wip/gev/deck.txt "
            '--commander "Gev, Scaled Scorch"\n'
            "  python scripts/singleton_deck.py wip/gev/raw.txt --commander-last --in-place\n"
            "  python scripts/singleton_deck.py wip/ -r --commander-from-folder\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="Input decklist file, directory (with -r), or '-' for stdin.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output file (default: <input>.singleton or stdout for '-')",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite the input file (creates a .bak backup first).",
    )
    parser.add_argument(
        "--commander",
        help="Commander card name. Removed from mainboard and written as the last line.",
    )
    parser.add_argument(
        "--commander-last",
        action="store_true",
        help="Treat the last non-empty input line as the commander (edh-parse style).",
    )
    parser.add_argument(
        "--commander-from-folder",
        action="store_true",
        help="With -r: use each subfolder name as the commander (spaces instead of hyphens).",
    )
    parser.add_argument(
        "--include-sideboard",
        action="store_true",
        help="Include sideboard cards (skipped by default).",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Process each subfolder's raw.txt (reads <folder>/raw.txt, writes <folder>/deck.txt).",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress summary stats.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.input is None:
        parser.error("input file or directory is required (use '-' for stdin)")

    if args.recursive:
        if not args.input.is_dir():
            parser.error("-r requires a directory input")
        failures = 0
        for subdir in sorted(p for p in args.input.iterdir() if p.is_dir()):
            raw_path = subdir / "raw.txt"
            if not raw_path.is_file():
                continue
            commander = args.commander
            if args.commander_from_folder and not commander:
                commander = folder_to_commander(subdir.name)
            out_path = subdir / "deck.txt"
            failures += process_file(
                raw_path,
                out_path,
                commander=commander,
                commander_last=args.commander_last,
                include_sideboard=args.include_sideboard,
                quiet=args.quiet,
            )
        return 1 if failures else 0

    result = run_singleton(
        args.input,
        args.output,
        commander=args.commander,
        commander_last=args.commander_last,
        include_sideboard=args.include_sideboard,
        in_place=args.in_place,
        quiet=args.quiet,
    )
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
