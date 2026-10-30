"""Moxfield deck grabber via local browser session."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

from utils.progress_utils import notify_progress


def _slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"[\s_-]+", "-", s).strip("-")


def _is_cat_header(tag) -> bool:
    return (
        tag.name == "a"
        and tag.has_attr("class")
        and "table-deck-row-link" in tag["class"]
        and not tag.get("href")
    )


def parse_deck_html(html: str) -> str:
    """Parse a rendered Moxfield deck page HTML into MTGO text decklist."""
    soup = BeautifulSoup(html, "html.parser")
    commanders: List[Tuple[int, str]] = []
    mainboard: List[Tuple[int, str]] = []

    # Restrict to <section class="deckview"> so charts and the Hypergeometric
    # Calculator widget below the decklist aren't scraped as duplicate cards.
    deckview = soup.find("section", class_="deckview")
    root = deckview if deckview else (soup.find("main") or soup)

    for li in root.find_all("li"):
        a = li.find("a", href=lambda h: h and "/cards/" in h)
        if not a:
            continue
        name = a.get_text().strip()
        if not name:
            continue
        header = li.find_previous(_is_cat_header)
        h_text = header.get_text().lower() if header else ""

        text = li.get_text(separator=" ").strip()
        m = re.match(r"^(\d+)\s+", text)
        qty = int(m.group(1)) if m else 1

        if "commander" in h_text:
            commanders.append((qty, name))
        elif "sideboard" in h_text or "considering" in h_text or "maybe" in h_text or "wishlist" in h_text:
            continue
        else:
            mainboard.append((qty, name))

    lines = ["Commander"]
    for q, n in commanders:
        lines.append(f"{q} {n}")
    lines.append("")
    lines.append("Deck")
    for q, n in mainboard:
        lines.append(f"{q} {n}")

    return "\n".join(lines)


def parse_user_favorites(html: str) -> List[Tuple[str, str]]:
    """Extract deck (title, url) pairs from a Moxfield user profile's Favorite Decks section."""
    soup = BeautifulSoup(html, "html.parser")
    decks: List[Tuple[str, str]] = []
    
    # Favorites section is under a details element or heading
    details = soup.find("details")
    container = details if details else soup
    for a in container.find_all("a", href=lambda h: h and "/decks/" in h):
        href = a.get("href")
        title = a.get_text().strip()
        if href and "/decks/public" not in href and "/decks/explore" not in href:
            full_url = f"https://moxfield.com{href}" if href.startswith("/") else href
            if (title, full_url) not in decks:
                decks.append((title, full_url))
    return decks


def _create_driver(headless: bool = False) -> webdriver.Chrome:
    options = Options()
    options.page_load_strategy = "eager"
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
    return webdriver.Chrome(options=options)


def fetch_deck_url(url: str, out_dir: str = "inputs/moxfield", driver: Optional[webdriver.Chrome] = None) -> Tuple[str, Path]:
    """Fetch a single Moxfield deck by URL and save to <out_dir>/<slug>.txt."""
    close_driver = False
    if driver is None:
        driver = _create_driver()
        close_driver = True

    try:
        driver.get(url)
        for _ in range(40):
            card_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/cards/']")
            if len(card_links) >= 10:
                break
            time.sleep(0.25)
        time.sleep(0.5)
        html = driver.page_source
        deck_text = parse_deck_html(html)
        soup = BeautifulSoup(html, "html.parser")
        h1 = soup.find("h1")
        title = h1.get_text().strip() if h1 else "deck"
        slug = _slugify(title)
        
        out_path = Path(out_dir) / f"{slug}.txt"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(deck_text, encoding="utf-8")
        return title, out_path
    finally:
        if close_driver:
            driver.quit()


def fetch_user_favorites(
    username: str = "bigmacstorm", out_dir: str = "inputs/moxfield"
) -> List[Tuple[str, Path, int]]:
    """Fetch all favorited decks for a Moxfield user into out_dir."""
    profile_url = f"https://moxfield.com/users/{username}"
    notify_progress(0, 0, f"Connecting to Moxfield for @{username}...")
    driver = _create_driver()
    results: List[Tuple[str, Path, int]] = []
    try:
        driver.get(profile_url)
        favorites = []
        for _ in range(30):
            favorites = parse_user_favorites(driver.page_source)
            if favorites:
                break
            time.sleep(0.5)

        if not favorites:
            raise RuntimeError(f"No favorited decks found on {profile_url}")

        total_decks = len(favorites)
        notify_progress(0, total_decks, f"Found {total_decks} favorited decks. Starting download...")

        for idx, (title, deck_url) in enumerate(favorites, 1):
            notify_progress(idx - 1, total_decks, f"Grabbing [{idx}/{total_decks}] {title}...")
            t, path = fetch_deck_url(deck_url, out_dir=out_dir, driver=driver)
            text = path.read_text(encoding="utf-8")
            count = sum(int(l.split()[0]) for l in text.splitlines() if re.match(r"^\d+\s+", l))
            results.append((t, path, count))
            notify_progress(idx, total_decks, f"Saved [{idx}/{total_decks}] {title} ({count} cards)")
            time.sleep(0.3)
    finally:
        driver.quit()
    return results


def list_grabbed_decks(moxfield_dir: Optional[Path] = None) -> List[dict]:
    """Return all grabbed Moxfield deck text files with parsed commander and display metadata."""
    if moxfield_dir is None:
        moxfield_dir = Path("inputs/moxfield")
    decks: List[dict] = []
    if not moxfield_dir.is_dir():
        return decks

    for p in sorted(moxfield_dir.glob("*.txt")):
        commander = ""
        card_count = 0
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
            in_cmd = False
            for l in lines:
                s = l.strip()
                if not s:
                    continue
                if s.lower() == "commander":
                    in_cmd = True
                    continue
                if in_cmd:
                    m = re.match(r"^\d+\s+(.*)", s)
                    commander = m.group(1) if m else s
                    in_cmd = False
                elif s.lower() == "deck":
                    continue
                m_qty = re.match(r"^(\d+)\s+", s)
                if m_qty:
                    card_count += int(m_qty.group(1))
        except Exception:
            pass
        title = p.stem.replace("-", " ").title()
        display = f"{title} ({commander})" if commander else title
        decks.append({
            "path": f"inputs/moxfield/{p.name}",
            "filename": p.name,
            "title": title,
            "commander": commander,
            "display": display,
            "card_count": card_count,
        })
    return decks
