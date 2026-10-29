from pathlib import Path

from scripts.singleton_deck import parse_lines, singletonize


def test_parse_and_singletonize_dedupes():
    lines = [
        "1 Sol Ring",
        "1 Arcane Signet",
        "1 Sol Ring",
        "2 Mountain",
        "1 Mountain",
        "SIDEBOARD:",
        "1 Toxic Deluge",
    ]
    counts, display, _ = parse_lines(lines)
    mainboard, duplicates, _ = singletonize(counts, display)

    assert "1 Sol Ring" in mainboard
    assert mainboard.count("1 Sol Ring") == 1
    assert "1 Mountain" in mainboard
    assert "1 Toxic Deluge" not in mainboard
    assert any(name == "Sol Ring" for name, _ in duplicates)


def test_commander_removed_from_mainboard():
    lines = ["1 Sol Ring", "1 Gev, Scaled Scorch", "1 Sol Ring"]
    counts, display, _ = parse_lines(lines)
    mainboard, _, commander = singletonize(
        counts,
        display,
        commander="Gev, Scaled Scorch",
    )

    assert commander == "Gev, Scaled Scorch"
    assert all("Gev" not in line for line in mainboard)


def test_cli_round_trip(tmp_path: Path):
    raw = tmp_path / "raw.txt"
    raw.write_text(
        "1 Sol Ring\n1 Sol Ring\n1 Command Tower\n1 Gev, Scaled Scorch\n",
        encoding="utf-8",
    )
    out = tmp_path / "deck.txt"

    from scripts.singleton_deck import main

    assert main([str(raw), "-o", str(out), "--commander-last"]) == 0
    text = out.read_text(encoding="utf-8").splitlines()
    assert text[-1] == "1 Gev, Scaled Scorch"
    assert "1 Command Tower" in text
    assert text.count("1 Sol Ring") == 1
