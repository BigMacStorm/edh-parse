"""Unit tests for Moxfield HTML parsing."""

from pathlib import Path
from edhparse.services.moxfield import parse_deck_html, parse_user_favorites, _slugify


def test_slugify():
    assert _slugify("Astoria Greengrass") == "astoria-greengrass"
    assert _slugify("Ur treasures are dragon!") == "ur-treasures-are-dragon"


def test_parse_deck_html_astoria():
    fixture = Path("cache/astoria_dom.html")
    if not fixture.is_file():
        return
    text = parse_deck_html(fixture.read_text(encoding="utf-8"))
    assert "Commander" in text
    assert "1 Aphelia, Viper Whisperer" in text
    assert "Deck" in text
    assert "1 Vraska, Golgari Queen" in text
    # Verify mainboard cards don't include sideboard
    assert "Mire Boa" not in text


def test_parse_user_favorites():
    fixture = Path("cache/moxfield_rendered.html")
    if not fixture.is_file():
        return
    favorites = parse_user_favorites(fixture.read_text(encoding="utf-8"))
    assert len(favorites) >= 9
    titles = [t for t, _ in favorites]
    assert "Astoria Greengrass" in titles
    assert "Cost of Living" in titles
