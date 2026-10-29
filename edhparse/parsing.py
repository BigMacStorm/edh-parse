"""Shared decklist / text-file parsers."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from edhparse.edhrec_util import edhrec_name_slug


def parse_card_names_file(filepath: Path | str) -> List[str]:
    """Parse card names, accepting optional MTGO-style counts ('1 Card Name')."""
    with open(filepath, "r", encoding="utf-8") as f:
        return parse_card_names_lines(f)


def parse_card_names_lines(lines: Iterable[str]) -> List[str]:
    """Parse card names from lines (paste or file), optional MTGO-style counts."""
    names: List[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"(\d+)\s+(.+)", line)
        if m:
            names.append(m.group(2).strip())
        else:
            names.append(line)
    return names


def parse_card_counts_lines(lines: Iterable[str]) -> Dict[str, int]:
    """Parse names + counts from lines, defaulting missing quantities to 1."""
    counts: Counter = Counter()
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"(\d+)\s+(.+)", line)
        if m:
            counts[m.group(2).strip()] += int(m.group(1))
        else:
            counts[line] += 1
    return dict(counts)


def parse_card_counts_file(filepath: Path | str) -> Dict[str, int]:
    """Parse names + counts from a file, defaulting missing quantities to 1."""
    with open(filepath, "r", encoding="utf-8") as f:
        return parse_card_counts_lines(f)


def parse_artist_names_file(filepath: Path | str) -> List[str]:
    """Parse artist names (one name per line)."""
    names: List[str] = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            name = line.strip()
            if name:
                names.append(name)
    return names


def parse_deck_file(filepath: Path | str) -> Dict[str, int]:
    """Parse a decklist file with format '1 Card Name'."""
    deck: Counter = Counter()
    path = Path(filepath)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            match = re.match(r"(\d+)\s+(.+)", line)
            if match:
                quantity = int(match.group(1))
                card_name = match.group(2).strip()
                deck[card_name] += quantity
            else:
                print(f"Warning: Could not parse line in {path.name}: {line}")
    return dict(deck)


def parse_card_list(filename: str | Path) -> Dict[str, dict]:
    """
    Parse MTGO-format list with optional SIDEBOARD section.

    Last non-empty line is treated as commander. Returns name ->
    {quantity, header} where header is mainboard/sideboard/commander.
    """
    path = Path(filename)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"Error: File '{filename}' not found.")
        return {}

    card_data: Dict[str, dict] = {}
    current_header = "mainboard"
    while True:
        if lines:
            last_line = lines[-1].strip()
            if last_line:
                match = re.match(r"(\d+)\s+(.+)", last_line)
                if match:
                    commander_quantity = int(match.group(1))
                    commander_name = match.group(2).strip()
                else:
                    print(f"Warning: Could not parse commander line: {last_line}")
                    commander_name = None
                    commander_quantity = 1

                if commander_name:
                    card_data[commander_name] = {
                        "quantity": commander_quantity,
                        "header": "commander",
                    }
                lines = lines[:-1]
                break

            lines = lines[:-1]
        else:
            break

    for line in lines:
        line = line.strip()

        if line.upper().startswith("SIDEBOARD:"):
            current_header = "sideboard"
            continue
        if not line:
            continue

        match = re.match(r"(\d+)\s+(.+)", line)
        if match:
            quantity = int(match.group(1))
            card_name = match.group(2).strip()
            card_data[card_name] = {"quantity": quantity, "header": current_header}
        else:
            print(f"Warning: Could not parse line: {line}")

    return card_data


def load_commander_slugs_from_file(filepath: str | Path) -> List[Tuple[str, Optional[str]]]:
    """Load EDHREC commander URLs or names → (slug, theme_or_None) pairs."""
    names: List[Tuple[str, Optional[str]]] = []
    with open(filepath, "r", encoding="utf-8") as file:
        for line in file:
            raw = line.strip()
            if not raw:
                continue
            lower = raw.lower().replace("/budget", "").replace("/expensive", "")
            match = re.search(r"/commanders/([^/#?]+)", lower)
            if match:
                commander = match.group(1).strip()
                if commander:
                    names.append((commander, None))
                continue

            if "//" in raw:
                left, right = raw.split("//", 1)
                left = left.strip()
                right = right.strip()
                if left and right:
                    combined = f"{edhrec_name_slug(left)}-{edhrec_name_slug(right)}"
                    names.append((combined, None))
                continue

            slug = edhrec_name_slug(raw)
            if slug:
                names.append((slug, None))
    return names


# Alias used by older call sites
load_urls_from_file = load_commander_slugs_from_file
get_edhrec_name = edhrec_name_slug
