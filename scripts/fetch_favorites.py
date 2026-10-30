import re
import time
from pathlib import Path
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

FAVORITE_DECKS = [
    ("Astoria Greengrass", "https://moxfield.com/decks/ZupfHu7aD0mMEntRnJe0Qw"),
    ("Cost of Living", "https://moxfield.com/decks/XCAzgTMi00a4-YeOkeYXBg"),
    ("Ur treasures are dragon", "https://moxfield.com/decks/3XbwFU1wNUqZgX4sSR2cww"),
    ("Swing Swing Batter Batter", "https://moxfield.com/decks/A1UjMkXLHUGo4IuQhfr1bw"),
    ("Reptile Dysfunction", "https://moxfield.com/decks/w0u3JgWS5EKznDDfugo0WA"),
    ("I Royal for Initiative", "https://moxfield.com/decks/AqrxdNx-ZEaawIwO1-Q02g"),
    ("Hope you didn't want to play", "https://moxfield.com/decks/u_Kqd7QTtU2Z8lmla7qvCA"),
    ("Elvish Counter Charge", "https://moxfield.com/decks/RZmkF3FktUebAc_mMOTuog"),
    ("Blinkosaurs", "https://moxfield.com/decks/asIyzs1ioE2zrjbqax5v9Q"),
    ("Baylen", "https://moxfield.com/decks/rnuq4OoPa0ejzaWEWh5PyA"),
]

def slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"[\s_-]+", "-", s).strip("-")

def is_cat_header(tag):
    return tag.name == 'a' and tag.has_attr('class') and 'table-deck-row-link' in tag['class'] and not tag.get('href')

def parse_deck_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    commanders = []
    mainboard = []
    
    for li in soup.find_all("li"):
        a = li.find("a", href=lambda h: h and "/cards/" in h)
        if not a:
            continue
        name = a.get_text().strip()
        if not name:
            continue
        header = li.find_previous(is_cat_header)
        h_text = header.get_text().lower() if header else ""
        
        text = li.get_text(separator=" ").strip()
        m = re.match(r"^(\d+)\s+", text)
        qty = int(m.group(1)) if m else 1
        
        if "commander" in h_text:
            commanders.append((qty, name))
        elif "sideboard" in h_text or "considering" in h_text or "maybe" in h_text:
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

def main():
    options = Options()
    options.page_load_strategy = "eager"
    driver = webdriver.Chrome(options=options)
    
    out_dir = Path("inputs/moxfield")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    saved_decks = []
    try:
        for idx, (title, url) in enumerate(FAVORITE_DECKS, 1):
            print(f"[{idx}/{len(FAVORITE_DECKS)}] Fetching '{title}' from {url} ...")
            driver.get(url)
            
            # Wait for deck to render
            for _ in range(12):
                body = driver.find_element(By.TAG_NAME, "body").text
                if "Commander (" in body or "Mainboard" in body or "Creatures (" in body or "Lands (" in body:
                    break
                time.sleep(1)
                
            time.sleep(1.5)
            html = driver.page_source
            decklist_text = parse_deck_html(html)
            
            slug = slugify(title)
            file_path = out_dir / f"{slug}.txt"
            file_path.write_text(decklist_text, encoding="utf-8")
            
            # Count cards
            cmd_count = sum(int(l.split()[0]) for l in decklist_text.splitlines() if l.startswith("1 ") and "Commander" in decklist_text[:decklist_text.index(l)])
            total_cards = sum(int(l.split()[0]) for l in decklist_text.splitlines() if re.match(r"^\d+\s+", l))
            print(f"  -> Saved {file_path} (Total cards: {total_cards})")
            saved_decks.append((title, file_path, total_cards))
            time.sleep(1)
            
    finally:
        driver.quit()
        
    print("\n--- Summary of Saved Favorited Decks ---")
    for t, p, cnt in saved_decks:
        print(f"  * {t}: {cnt} cards -> {p}")

if __name__ == "__main__":
    main()
