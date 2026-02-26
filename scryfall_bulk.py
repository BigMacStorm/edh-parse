"""
Scryfall oracle_cards bulk data: download once, then serve lookups from local SQLite
instead of hitting the API. Oracle cards = one card per Oracle ID (Scryfall's chosen
printing, usually with price) — smaller and sufficient when you only need the cheapest/canonical printing.
"""
import decimal
import gzip
import json
import sqlite3
from pathlib import Path

import requests

# pylint: disable=missing-function-docstring

BULK_CACHE_DIR = Path("scryfall_bulk_cache")
BULK_DB_NAME = "oracle_cards.db"
BULK_META_NAME = "bulk_meta.json"
BULK_LIST_URL = "https://api.scryfall.com/bulk-data"
BULK_TYPE = "oracle_cards"  # one per card, chosen printing with price; ~168MB vs default_cards ~525MB

_conn = None
_loaded = False


def _cache_dir():
    Path(BULK_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    return BULK_CACHE_DIR


def _db_path():
    return _cache_dir() / BULK_DB_NAME


def _meta_path():
    return _cache_dir() / BULK_META_NAME


def _get_bulk_uri(session):
    """Return (download_uri, updated_at) for oracle_cards bulk data."""
    try:
        r = session.get(BULK_LIST_URL, timeout=30)
        r.raise_for_status()
        data = r.json()
        for item in data.get("data", []):
            if item.get("type") == BULK_TYPE:
                return item.get("download_uri"), item.get("updated_at")
    except requests.exceptions.RequestException as err:
        print(f"Error fetching bulk data list: {err}")
    return None, None


def _download_bulk(session, download_uri, dest_path):
    """Stream download gzip bulk file to dest_path."""
    try:
        r = session.get(download_uri, stream=True, timeout=60)
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
        return True
    except requests.exceptions.RequestException as err:
        print(f"Error downloading bulk data: {err}")
        return False


def _json_serializable(obj):
    """Convert Decimal (e.g. from ijson/Scryfall) to float for JSON."""
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _open_bulk_file(path):
    """Open bulk file as gzip or raw JSON. Returns a context manager (use with 'with')."""
    with open(path, "rb") as raw:
        magic = raw.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rb")
    return open(path, "rb")


def _build_db_from_gzip(gzip_path):
    """Stream-parse JSON array (gzip or raw) and fill SQLite. Oracle cards = one per card."""
    import ijson  # optional: stream parse

    db_path = _db_path()
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE cards (id TEXT PRIMARY KEY, name TEXT, data TEXT)"
    )
    conn.execute("CREATE INDEX idx_name ON cards(name)")
    conn.commit()

    count = 0
    try:
        with _open_bulk_file(gzip_path) as f:
            for card in ijson.items(f, "item"):
                if not isinstance(card, dict):
                    continue
                cid = card.get("id")
                name = card.get("name")
                if not cid:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO cards (id, name, data) VALUES (?, ?, ?)",
                    (cid, name, json.dumps(card, default=_json_serializable)),
                )
                count += 1
                if count % 10000 == 0:
                    conn.commit()
        conn.commit()
    except Exception as e:
        print(f"Error building bulk DB: {e}")
        conn.close()
        if db_path.exists():
            db_path.unlink()
        raise
    conn.close()
    return count


def is_available():
    """Return True if bulk DB is loaded and usable."""
    return _loaded and _conn is not None


def ensure_loaded(session, force_refresh=False):
    """Download oracle_cards bulk data if needed and build SQLite index."""
    global _conn, _loaded
    if _loaded and _conn is not None and not force_refresh:
        return True
    cache = _cache_dir()
    meta_path = _meta_path()
    gzip_path = cache / "oracle_cards.json.gz"

    download_uri, updated_at = _get_bulk_uri(session)
    if not download_uri:
        return False
    if gzip_path.exists() and meta_path.exists() and _db_path().exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("updated_at") == updated_at:
                if _conn is None:
                    _conn = sqlite3.connect(str(_db_path()))
                _loaded = True
                return True
        except (json.JSONDecodeError, OSError):
            pass

    need_download = not gzip_path.exists()
    if need_download:
        print("Scryfall bulk: downloading oracle_cards...")
        if not _download_bulk(session, download_uri, gzip_path):
            return False
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"updated_at": updated_at}, f)

    if not _db_path().exists() or need_download:
        print("Scryfall bulk: building local index...")
        try:
            n = _build_db_from_gzip(gzip_path)
            print(f"Scryfall bulk: indexed {n} cards.")
        except ImportError:
            print(
                "scryfall_bulk: install 'ijson' to use bulk data (pip install ijson). Falling back to API."
            )
            return False

    if _conn is None:
        _conn = sqlite3.connect(str(_db_path()))
    _loaded = True
    return True


def get_card_by_id(card_id):
    """Return card dict from bulk DB or None."""
    global _conn
    if _conn is None:
        return None
    try:
        row = _conn.execute(
            "SELECT data FROM cards WHERE id = ?", (card_id,)
        ).fetchone()
        if row:
            return json.loads(row[0])
    except (sqlite3.OperationalError, json.JSONDecodeError):
        pass
    return None


def get_card_by_name(name):
    """Return the single card dict for exact name (oracle_cards has one per card) or None."""
    global _conn
    if _conn is None or not name:
        return None
    try:
        row = _conn.execute(
            "SELECT data FROM cards WHERE name = ?", (name.strip(),)
        ).fetchone()
        if row:
            return json.loads(row[0])
    except (sqlite3.OperationalError, json.JSONDecodeError):
        pass
    return None


def close():
    """Close the DB connection (e.g. on exit)."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
