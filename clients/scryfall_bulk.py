"""
Scryfall oracle_cards bulk data: download once, then serve lookups from local SQLite
instead of hitting the API. Oracle cards = one card per Oracle ID (Scryfall's chosen
printing, with that printing's price). NOTE: the price is the price of the printing
Scryfall picked for oracle_cards, not necessarily the cheapest printing.

Name lookups: exact names hit the SQLite name index. Case-insensitive and
front-face-only names (EDHREC lists double-faced cards by their front face) are
resolved through small in-memory dicts built once per connection, so a miss never
costs a full table scan.
"""
import decimal
import gzip
import json
import sqlite3
import time
from pathlib import Path

import requests

# pylint: disable=missing-function-docstring

BULK_CACHE_DIR = Path("cache/scryfall_bulk_cache")
BULK_DB_NAME = "oracle_cards.db"
BULK_META_NAME = "bulk_meta.json"
BULK_LIST_URL = "https://api.scryfall.com/bulk-data"
BULK_TYPE = "oracle_cards"  # one per card, chosen printing with price; ~168MB vs default_cards ~525MB
DEBUT_BULK_TYPE = "default_cards"  # every printing; used once to build oracle debut dates locally
DEBUT_DB_NAME = "oracle_debuts.db"
DEBUT_META_NAME = "debut_meta.json"
DFC_FACE_SEP = " // "
SCRYFALL_USER_AGENT = "edh-parse/1.0 (edh-parse CLI; +local)"

_conn = None
_loaded = False
_debut_conn = None
_debut_loaded = False

# In-memory name indexes (see module docstring). Rebuilt whenever the connection changes.
_name_index = None

# After a failed bulk load, don't retry (a network round-trip) for every single card lookup.
_LOAD_FAIL_COOLDOWN_SECONDS = 300
_load_failed_at = None

# Bulk payload is large. If our cache contains a truncated download (or a
# Cloudflare/interstitial HTML page that happens to be 200), we'd otherwise
# "successfully" build an empty/invalid DB and trigger per-card API fallbacks.
MIN_GZIP_BYTES = 5_000_000  # sanity check
MIN_DB_BYTES = 200_000     # sanity check
MIN_DEBUT_GZIP_BYTES = 50_000_000
MIN_DEBUT_DB_BYTES = 500_000


def configure_scryfall_session(session):
    """Scryfall requires a non-default User-Agent on API requests."""
    if session is None:
        return session
    session.headers["User-Agent"] = SCRYFALL_USER_AGENT
    session.headers["Accept"] = "application/json"
    return session


def _cache_dir():
    Path(BULK_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    return BULK_CACHE_DIR


def _db_path():
    return _cache_dir() / BULK_DB_NAME


def _meta_path():
    return _cache_dir() / BULK_META_NAME


def _debut_db_path():
    return _cache_dir() / DEBUT_DB_NAME


def _debut_meta_path():
    return _cache_dir() / DEBUT_META_NAME


def _get_bulk_uri(session, bulk_type=BULK_TYPE):
    """Return (download_uri, updated_at) for a Scryfall bulk-data type."""
    try:
        r = session.get(BULK_LIST_URL, timeout=30)
        r.raise_for_status()
        data = r.json()
        for item in data.get("data", []):
            if item.get("type") == bulk_type:
                # Scryfall has used multiple field names over time for the bulk URL.
                # Current payload provides a JSONL gz file via `jsonl_download_uri`.
                download_uri = (
                    item.get("jsonl_download_uri")
                    or item.get("download_uri")
                    or item.get("uri")
                )
                return download_uri, item.get("updated_at")
    except requests.exceptions.RequestException as err:
        print(f"Error fetching bulk data list: {err}")
    return None, None


def _download_bulk(session, download_uri, dest_path):
    """Stream download gzip bulk file to dest_path.

    Uses plain ``requests`` rather than the cached session: these files are 25-150MB,
    and going through requests_cache buffers them in memory and stores a second copy
    of every download inside the HTTP cache database.
    """
    dest_path = Path(dest_path)
    part_path = dest_path.with_name(dest_path.name + ".part")
    headers = {"User-Agent": SCRYFALL_USER_AGENT, "Accept": "*/*"}
    try:
        with requests.get(download_uri, stream=True, timeout=60, headers=headers) as r:
            r.raise_for_status()
            with open(part_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if chunk:
                        f.write(chunk)
        part_path.replace(dest_path)
        # Sanity check: bulk files should be a large gzip.
        if not _is_valid_bulk_cache(dest_path):
            try:
                dest_path.unlink()
            except OSError:
                pass
            return False
        return True
    except requests.exceptions.RequestException as err:
        print(f"Error downloading bulk data: {err}")
        try:
            part_path.unlink()
        except OSError:
            pass
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


def _is_valid_bulk_cache(gzip_path: Path) -> bool:
    """Check gzip file looks sane (size + gzip magic)."""
    try:
        if not gzip_path.exists():
            return False
        if gzip_path.stat().st_size < MIN_GZIP_BYTES:
            return False
        with open(gzip_path, "rb") as raw:
            magic = raw.read(2)
        return magic == b"\x1f\x8b"
    except OSError:
        return False


def _is_valid_db(db_path: Path) -> bool:
    """Check DB file looks sane (size)."""
    try:
        return db_path.exists() and db_path.stat().st_size >= MIN_DB_BYTES
    except OSError:
        return False


def _close_main_conn():
    """Release the cards DB handle (needed before replacing the file, esp. on Windows)."""
    global _conn, _loaded, _name_index
    if _conn is not None:
        try:
            _conn.close()
        except sqlite3.Error:
            pass
    _conn = None
    _loaded = False
    _name_index = None


def _build_db_from_gzip(gzip_path):
    """Stream-parse Scryfall bulk JSONL.gz and fill SQLite (one JSON object per line).

    Builds into a temp file and swaps it in at the end, so a failed or interrupted build
    never destroys a working database. The raw JSON line is stored as-is (no re-serialising),
    rows are inserted in batches, and the name index is created after the data is loaded.
    """
    _close_main_conn()
    db_path = _db_path()
    tmp_path = db_path.with_name(db_path.name + ".building")
    if tmp_path.exists():
        tmp_path.unlink()
    conn = sqlite3.connect(str(tmp_path))
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("CREATE TABLE cards (id TEXT PRIMARY KEY, name TEXT, data TEXT)")

    insert_sql = "INSERT OR REPLACE INTO cards (id, name, data) VALUES (?, ?, ?)"
    count = 0
    batch = []
    try:
        with _open_bulk_file(gzip_path) as f:
            # Scryfall bulk is line-delimited JSON objects.
            for raw_line in f:
                if not raw_line:
                    continue
                if isinstance(raw_line, bytes):
                    line = raw_line.decode("utf-8", errors="replace")
                else:
                    line = str(raw_line)
                line = line.strip()
                if not line:
                    continue
                card = json.loads(line)
                if not isinstance(card, dict):
                    continue
                cid = card.get("id")
                if not cid:
                    continue
                batch.append((cid, card.get("name"), line))
                count += 1
                if len(batch) >= 5000:
                    conn.executemany(insert_sql, batch)
                    batch.clear()
        if batch:
            conn.executemany(insert_sql, batch)
        conn.execute("CREATE INDEX idx_name ON cards(name)")
        conn.commit()
    except Exception as e:
        print(f"Error building bulk DB: {e}")
        conn.close()
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    conn.close()
    tmp_path.replace(db_path)
    return count


def is_available():
    """Return True if bulk DB is loaded and usable."""
    return _loaded and _conn is not None


def ensure_loaded(session, force_refresh=False):
    """Download oracle_cards bulk data if needed and build SQLite index.

    After a failure, further calls return False immediately for a few minutes instead
    of re-trying the network on every card lookup.
    """
    global _load_failed_at
    if (
        not force_refresh
        and not _loaded
        and _load_failed_at is not None
        and time.monotonic() - _load_failed_at < _LOAD_FAIL_COOLDOWN_SECONDS
    ):
        return False
    ok = _ensure_loaded_impl(session, force_refresh=force_refresh)
    _load_failed_at = None if ok else time.monotonic()
    return ok


def _ensure_loaded_impl(session, force_refresh=False):
    global _conn, _loaded
    configure_scryfall_session(session)
    if _loaded and _conn is not None and not force_refresh:
        return True
    cache = _cache_dir()
    meta_path = _meta_path()
    gzip_path = cache / "oracle_cards.json.gz"

    download_uri, updated_at = _get_bulk_uri(session, BULK_TYPE)
    if not download_uri:
        return False
    local_updated_at = None
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            local_updated_at = meta.get("updated_at")
        except (json.JSONDecodeError, OSError):
            local_updated_at = None

    db_exists = _db_path().exists()
    gzip_exists = gzip_path.exists()
    gzip_valid = gzip_exists and _is_valid_bulk_cache(gzip_path)
    db_valid = db_exists and _is_valid_db(_db_path())
    is_current = (
        (not force_refresh)
        and gzip_valid
        and db_valid
        and local_updated_at
        and local_updated_at == updated_at
    )
    if is_current:
        if _conn is None:
            _conn = sqlite3.connect(str(_db_path()), check_same_thread=False)
        _loaded = True
        return True

    need_download = force_refresh or (not gzip_valid) or (local_updated_at != updated_at)
    if need_download:
        print("Scryfall bulk: downloading oracle_cards...")
        if not _download_bulk(session, download_uri, gzip_path):
            return False
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"updated_at": updated_at}, f)

    if not db_valid or need_download:
        print("Scryfall bulk: building local index...")
        try:
            n = _build_db_from_gzip(gzip_path)
            print(f"Scryfall bulk: indexed {n} cards.")
            if n <= 0:
                return False
        except ImportError:
            print(
                "scryfall_bulk: install 'ijson' to use bulk data (pip install ijson). Falling back to API."
            )
            return False

    if _conn is None:
        _conn = sqlite3.connect(str(_db_path()), check_same_thread=False)
    _loaded = True
    return True


def _is_valid_debut_db(db_path: Path) -> bool:
    try:
        return db_path.exists() and db_path.stat().st_size >= MIN_DEBUT_DB_BYTES
    except OSError:
        return False


def _build_debut_db_from_gzip(gzip_path):
    """Stream-parse default_cards bulk and keep earliest printing per oracle_id."""
    db_path = _debut_db_path()
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE debuts (oracle_id TEXT PRIMARY KEY, released_at TEXT, set_code TEXT)"
    )
    conn.commit()

    count = 0
    upsert_sql = """
        INSERT INTO debuts (oracle_id, released_at, set_code) VALUES (?, ?, ?)
        ON CONFLICT(oracle_id) DO UPDATE SET
          released_at = CASE
            WHEN excluded.released_at < debuts.released_at THEN excluded.released_at
            ELSE debuts.released_at
          END,
          set_code = CASE
            WHEN excluded.released_at < debuts.released_at THEN excluded.set_code
            ELSE debuts.set_code
          END
    """
    try:
        with _open_bulk_file(gzip_path) as f:
            for raw_line in f:
                if not raw_line:
                    continue
                if isinstance(raw_line, bytes):
                    line = raw_line.decode("utf-8", errors="replace")
                else:
                    line = str(raw_line)
                line = line.strip()
                if not line:
                    continue
                card = json.loads(line)
                if not isinstance(card, dict):
                    continue
                oracle_id = card.get("oracle_id")
                released_at = card.get("released_at")
                set_code = card.get("set")
                if not oracle_id or not released_at or not set_code:
                    continue
                conn.execute(upsert_sql, (oracle_id, released_at, set_code))
                count += 1
                if count % 25000 == 0:
                    conn.commit()
        conn.commit()
    except Exception as e:
        print(f"Error building debut DB: {e}")
        conn.close()
        if db_path.exists():
            db_path.unlink()
        raise
    conn.close()
    return count


def ensure_debut_index(session, force_refresh=False):
    """Download default_cards bulk once and build a local oracle debut-date index."""
    global _debut_conn, _debut_loaded
    configure_scryfall_session(session)
    if _debut_loaded and _debut_conn is not None and not force_refresh:
        return True

    cache = _cache_dir()
    meta_path = _debut_meta_path()
    gzip_path = cache / "default_cards.json.gz"

    download_uri, updated_at = _get_bulk_uri(session, DEBUT_BULK_TYPE)
    if not download_uri:
        return False

    local_updated_at = None
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            local_updated_at = meta.get("updated_at")
        except (json.JSONDecodeError, OSError):
            local_updated_at = None

    db_exists = _debut_db_path().exists()
    gzip_exists = gzip_path.exists()
    gzip_valid = gzip_exists and gzip_path.stat().st_size >= MIN_DEBUT_GZIP_BYTES
    db_valid = db_exists and _is_valid_debut_db(_debut_db_path())
    is_current = (
        (not force_refresh)
        and gzip_valid
        and db_valid
        and local_updated_at
        and local_updated_at == updated_at
    )
    if is_current:
        if _debut_conn is None:
            _debut_conn = sqlite3.connect(str(_debut_db_path()), check_same_thread=False)
        _debut_loaded = True
        return True

    need_download = force_refresh or (not gzip_valid) or (local_updated_at != updated_at)
    if need_download:
        print("Scryfall bulk: downloading default_cards for debut dates...")
        if not _download_bulk(session, download_uri, gzip_path):
            return False
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"updated_at": updated_at}, f)

    if not db_valid or need_download:
        print("Scryfall bulk: building debut-date index...")
        try:
            n = _build_debut_db_from_gzip(gzip_path)
            print(f"Scryfall bulk: indexed debut dates for {n} printings.")
            if n <= 0:
                return False
        except Exception:
            return False

    if _debut_conn is None:
        _debut_conn = sqlite3.connect(str(_debut_db_path()), check_same_thread=False)
    _debut_loaded = True
    return True


def get_debut_printing(oracle_id):
    """Return (released_at, set_code) for a card's first printing, or (None, None)."""
    global _debut_conn
    if _debut_conn is None or not oracle_id:
        return None, None
    try:
        row = _debut_conn.execute(
            "SELECT released_at, set_code FROM debuts WHERE oracle_id = ?",
            (oracle_id,),
        ).fetchone()
        if row:
            return row[0], row[1]
    except sqlite3.OperationalError:
        pass
    return None, None


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
    """Return the best card dict for exact name, or None if not found."""
    return get_card_by_name_fuzzy(name)


def _get_name_index():
    """Lowercase-name and front-face-name -> [card ids], built once per connection."""
    global _name_index
    conn = _conn
    if conn is None:
        return None
    if _name_index is not None and _name_index["conn"] is conn:
        return _name_index
    lower = {}
    front = {}
    try:
        rows = conn.execute("SELECT id, name FROM cards").fetchall()
    except sqlite3.OperationalError:
        return None
    for cid, name in rows:
        if not name:
            continue
        lower.setdefault(name.strip().lower(), []).append(cid)
        if DFC_FACE_SEP in name:
            front_name = name.split(DFC_FACE_SEP, 1)[0].strip().lower()
            front.setdefault(front_name, []).append(cid)
    _name_index = {"conn": conn, "lower": lower, "front": front}
    return _name_index


def _cards_by_ids(ids):
    out = []
    for cid in ids:
        row = _conn.execute("SELECT data FROM cards WHERE id = ?", (cid,)).fetchone()
        if row:
            out.append(json.loads(row[0]))
    return out


def _name_match_candidates(name):
    """All oracle_cards rows sharing an exact (case-insensitive) name."""
    global _conn
    if _conn is None or not name:
        return []
    wanted = name.strip()
    try:
        rows = _conn.execute(
            "SELECT data FROM cards WHERE name = ?", (wanted,)
        ).fetchall()
        if rows:
            return [json.loads(row[0]) for row in rows]
        index = _get_name_index()
        if index is None:
            return []
        return _cards_by_ids(index["lower"].get(wanted.lower(), []))
    except (sqlite3.OperationalError, json.JSONDecodeError):
        return []


def get_card_by_name_fuzzy(name):
    """Exact name match first, then case-insensitive match against bulk data."""
    candidates = _name_match_candidates(name)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return _pick_best_name_match(candidates, name)


def _is_playable_bulk_card(data):
    if not isinstance(data, dict):
        return False
    if data.get("layout") == "art_series":
        return False
    type_line = (data.get("type_line") or "").strip()
    if type_line in {"Card", "Card // Card"}:
        return False
    promo_types = set(data.get("promo_types") or [])
    if "jumpstart" in promo_types and type_line == "Card":
        return False
    return True


def _pick_best_name_match(candidates, wanted_name):
    """Prefer real playable printings over Jumpstart themes and other placeholders."""
    wanted = (wanted_name or "").strip().lower()
    playable = [card for card in candidates if _is_playable_bulk_card(card)]
    pool = playable or candidates
    pool.sort(
        key=lambda card: (
            0 if _is_playable_bulk_card(card) else 1,
            0 if (card.get("type_line") or "").strip() != "Card" else 1,
            0 if card.get("layout") not in {"token", "art_series"} else 1,
            card.get("released_at") or "",
            card.get("name") or "",
        )
    )
    for card in pool:
        if (card.get("name") or "").strip().lower() == wanted:
            return card
    return pool[0] if pool else None


def _front_face_name(data):
    card_name = (data.get("name") or "").strip()
    if DFC_FACE_SEP in card_name:
        return card_name.split(DFC_FACE_SEP, 1)[0].strip()
    faces = data.get("card_faces") or []
    if faces:
        return (faces[0].get("name") or "").strip()
    return card_name


def _pick_face_match(candidates, wanted_name):
    wanted = wanted_name.strip().lower()
    playable = [card for card in candidates if _is_playable_bulk_card(card)]
    exact_front = [
        card for card in playable if _front_face_name(card).lower() == wanted
    ]
    if exact_front:
        preferred_layouts = {"transform", "modal_dfc", "double_faced_token", "reversible_card"}
        exact_front.sort(
            key=lambda card: (
                0 if card.get("layout") in preferred_layouts else 1,
                card.get("name") or "",
            )
        )
        return exact_front[0]
    if playable:
        return playable[0]
    return candidates[0] if candidates else None


def get_card_by_name_or_face(name):
    """Match exact/face name, or a double-faced card when only one face is given."""
    card = get_card_by_name_fuzzy(name)
    if card is not None:
        return card
    if not name or DFC_FACE_SEP in name:
        return None
    global _conn
    if _conn is None:
        return None
    try:
        index = _get_name_index()
        if index is None:
            return None
        ids = index["front"].get(name.strip().lower(), [])
        if not ids:
            return None
        return _pick_face_match(_cards_by_ids(ids), name)
    except (sqlite3.OperationalError, json.JSONDecodeError):
        pass
    return None


def close():
    """Close the DB connection (e.g. on exit)."""
    global _conn, _debut_conn, _name_index
    _name_index = None
    if _conn is not None:
        _conn.close()
        _conn = None
    if _debut_conn is not None:
        _debut_conn.close()
        _debut_conn = None
