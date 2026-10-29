"""Deduplicate a combined decklist to singleton (1 copy per card)."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

from edhparse.services import err_result, ok_result

CARD_LINE = re.compile(r"^(\d+)\s+(.+)$")
SKIP_HEADERS = {
    "commander",
    "deck",
    "mainboard",
    "main deck",
    "about",
    "maybeboard",
    "companion",
    "sideboard",
}


def _normalize_key(name: str) -> str:
    return name.casefold()


def parse_lines(
    lines: list[str],
    *,
    include_sideboard: bool = False,
) -> tuple[Counter[str], dict[str, str], list[str]]:
    """
    Parse decklist lines into card counts.

    Returns (counts, display_names, warnings).
    display_names maps normalized key -> first-seen spelling.
    """
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    warnings: list[str] = []
    in_sideboard = False

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        upper = line.upper()
        if upper.startswith("SIDEBOARD:"):
            in_sideboard = True
            continue
        if upper.endswith(":") and line[:-1].strip().casefold() in SKIP_HEADERS:
            in_sideboard = line[:-1].strip().casefold() == "sideboard"
            continue
        if in_sideboard and not include_sideboard:
            continue

        match = CARD_LINE.match(line)
        if not match:
            warnings.append(f"skipped non-card line: {line}")
            continue

        qty = int(match.group(1))
        name = match.group(2).strip()
        key = _normalize_key(name)
        if key not in display:
            display[key] = name
        counts[key] += qty

    return counts, display, warnings


def singletonize(
    counts: Counter[str],
    display: dict[str, str],
    *,
    commander: str | None = None,
    commander_key: str | None = None,
) -> tuple[list[str], list[tuple[str, int]], str | None]:
    """
    Build singleton mainboard lines and duplicate report.

    Returns (mainboard_lines, duplicates_removed, commander_display_name).
    """
    commander_key = commander_key or (commander and _normalize_key(commander))
    duplicates: list[tuple[str, int]] = []
    mainboard: list[str] = []

    for key in sorted(counts, key=lambda k: display[k].casefold()):
        total = counts[key]
        name = display[key]
        if total > 1:
            duplicates.append((name, total - 1))
        if commander_key and key == commander_key:
            continue
        mainboard.append(f"1 {name}")

    commander_name = None
    if commander_key and commander_key in display:
        commander_name = display[commander_key]
    elif commander:
        commander_name = commander

    return mainboard, duplicates, commander_name


def read_input(path: Path | None) -> list[str]:
    if path is None or str(path) == "-":
        return sys.stdin.read().splitlines()
    return path.read_text(encoding="utf-8").splitlines()


def write_output(path: Path | None, text: str) -> None:
    if path is None or str(path) == "-":
        sys.stdout.write(text)
        return
    path.write_text(text, encoding="utf-8")


def extract_commander_from_last_line(lines: list[str]) -> tuple[str | None, list[str]]:
    """Match edh-parse convention: last non-empty line is the commander."""
    remaining = list(lines)
    while remaining:
        last = remaining[-1].strip()
        if not last:
            remaining.pop()
            continue
        match = CARD_LINE.match(last)
        if not match:
            return None, lines
        commander = match.group(2).strip()
        remaining.pop()
        return commander, remaining
    return None, lines


def folder_to_commander(folder_name: str) -> str:
    """wip/gev-scaled-scorch -> Gev Scaled Scorch (best-effort)."""
    return folder_name.replace("-", " ").replace("_", " ")


def process_file(
    input_path: Path,
    output_path: Path,
    *,
    commander: str | None = None,
    commander_last: bool = False,
    include_sideboard: bool = False,
    quiet: bool = False,
) -> int:
    lines = read_input(input_path)

    if commander_last and not commander:
        commander, lines = extract_commander_from_last_line(lines)

    counts, display, warnings = parse_lines(lines, include_sideboard=include_sideboard)
    if not counts:
        print(f"Error: no cards found in {input_path}", file=sys.stderr)
        return 1

    mainboard, duplicates, commander_name = singletonize(
        counts,
        display,
        commander=commander,
    )

    out_lines = mainboard
    if commander_name:
        out_lines = mainboard + [f"1 {commander_name}"]
    elif commander:
        out_lines = mainboard + [f"1 {commander}"]

    text = "\n".join(out_lines)
    if out_lines:
        text += "\n"
    write_output(output_path, text)

    if not quiet:
        removed = sum(extra for _, extra in duplicates)
        print(f"{input_path} -> {output_path}")
        print(f"  {len(mainboard)} unique cards", end="")
        if commander_name or commander:
            print(" + commander", end="")
        print()
        if duplicates:
            print(f"  removed {removed} duplicate copies across {len(duplicates)} cards")
            for name, extra in sorted(duplicates, key=lambda item: (-item[1], item[0].casefold()))[:15]:
                print(f"    - {name}: had {extra + 1} total, kept 1")
            if len(duplicates) > 15:
                print(f"    ... and {len(duplicates) - 15} more")
        for warning in warnings[:5]:
            print(f"  warning: {warning}", file=sys.stderr)
        if len(warnings) > 5:
            print(f"  warning: ... and {len(warnings) - 5} more skipped lines", file=sys.stderr)

    return 0


def run_singleton(
    input_path: str | Path,
    output_path: Optional[str | Path] = None,
    *,
    commander: Optional[str] = None,
    commander_last: bool = False,
    include_sideboard: bool = False,
    in_place: bool = False,
    quiet: bool = False,
) -> dict:
    """Singletonize one decklist file. Returns a result dict."""
    path = Path(input_path) if str(input_path) != "-" else Path("-")
    if in_place:
        if str(path) == "-":
            return err_result("--in-place cannot be used with stdin")
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        out = path
    elif output_path is not None:
        out = Path(output_path)
    elif str(path) == "-":
        out = Path("-")
    else:
        out = path.with_suffix(path.suffix + ".singleton")

    code = process_file(
        path,
        out,
        commander=commander,
        commander_last=commander_last,
        include_sideboard=include_sideboard,
        quiet=quiet,
    )
    if code != 0:
        return err_result(f"Failed to singletonize {input_path}")
    return ok_result(f"Wrote {out}", outputs=[str(out)])
