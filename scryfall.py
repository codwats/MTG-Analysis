"""Scryfall API client with bulk data support."""
import json
import os
import time
import requests

from categorizer import categorize_card

BULK_DATA_PATH = os.path.join(os.path.dirname(__file__), "scryfall_bulk.json")
SCRYFALL_API = "https://api.scryfall.com"
RATE_LIMIT_DELAY = 0.1  # 100ms = 10 req/sec

# In-memory card cache (loaded from bulk data or API calls)
_card_cache = {}
_name_index = {}  # Lowercase comma-stripped name -> canonical name (for filename matching)
_bulk_loaded = False
_api_available = True  # Circuit breaker for API


def _index_name(canonical_name):
    """Add a card name to the fuzzy index."""
    # Index the comma-stripped lowercase version
    stripped = canonical_name.replace(",", "").lower()
    _name_index[stripped] = canonical_name
    # Also index the exact lowercase
    _name_index[canonical_name.lower()] = canonical_name


def _parse_scryfall_card(card_json):
    """Convert raw Scryfall JSON into our card data format."""
    type_line = card_json.get("type_line", "")
    oracle_text = card_json.get("oracle_text", "")

    # Handle double-faced cards
    if "card_faces" in card_json and not oracle_text:
        faces = card_json["card_faces"]
        oracle_text = " // ".join(f.get("oracle_text", "") for f in faces)
        if not type_line:
            type_line = " // ".join(f.get("type_line", "") for f in faces)

    is_land = "Land" in type_line.split("//")[0] if type_line else False
    is_creature = "Creature" in type_line if type_line else False

    card_data = {
        "scryfall_id": card_json.get("id", ""),
        "name": card_json.get("name", ""),
        "color_identity": card_json.get("color_identity", []),
        "cmc": card_json.get("cmc", 0),
        "mana_cost": card_json.get("mana_cost", ""),
        "type_line": type_line,
        "oracle_text": oracle_text,
        "keywords": card_json.get("keywords", []),
        "is_land": is_land,
        "is_creature": is_creature,
    }
    card_data["categories"] = categorize_card(card_data)
    return card_data


def load_bulk_data(path=None):
    """Load Scryfall bulk data into memory cache."""
    global _card_cache, _bulk_loaded
    path = path or BULK_DATA_PATH

    if not os.path.exists(path):
        return False

    print(f"Loading bulk card data from {path}...")
    with open(path, "r", encoding="utf-8") as f:
        cards = json.load(f)

    count = 0
    for card in cards:
        # Skip tokens, extras, etc.
        if card.get("layout") in ("token", "double_faced_token", "emblem", "art_series"):
            continue
        # Use the Oracle name (handles DFCs)
        name = card.get("name", "")
        if name and name not in _card_cache:
            _card_cache[name] = _parse_scryfall_card(card)
            _index_name(name)
            count += 1

    _bulk_loaded = True
    print(f"Loaded {count} cards from bulk data.")
    return True


def download_bulk_data(path=None):
    """Download Scryfall Oracle Cards bulk data."""
    path = path or BULK_DATA_PATH
    try:
        print("Fetching bulk data download URL from Scryfall...")
        resp = requests.get(f"{SCRYFALL_API}/bulk-data/oracle-cards", timeout=30)
        resp.raise_for_status()
        download_uri = resp.json()["download_uri"]

        print(f"Downloading bulk card data (~35MB)...")
        resp = requests.get(download_uri, stream=True, timeout=60)
        resp.raise_for_status()

        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    print(f"\r  {downloaded // (1024*1024)}MB / {total // (1024*1024)}MB ({pct:.0f}%)", end="", flush=True)
        print("\nBulk data downloaded.")
        return load_bulk_data(path)
    except requests.RequestException as e:
        print(f"\nERROR: Could not download bulk data: {type(e).__name__}: {e}")
        print("You can manually download from: https://scryfall.com/docs/api/bulk-data")
        print(f"Save the 'Oracle Cards' file as: {path}")
        return False


def fetch_card_api(name):
    """Fetch a single card from Scryfall API (fallback)."""
    global _api_available
    if not _api_available:
        return None

    time.sleep(RATE_LIMIT_DELAY)
    try:
        resp = requests.get(
            f"{SCRYFALL_API}/cards/named",
            params={"exact": name},
            headers={"User-Agent": "MTGDeckAnalyzer/1.0"},
            timeout=10,
        )
        if resp.status_code == 404:
            # Try fuzzy search
            time.sleep(RATE_LIMIT_DELAY)
            resp = requests.get(
                f"{SCRYFALL_API}/cards/named",
                params={"fuzzy": name},
                headers={"User-Agent": "MTGDeckAnalyzer/1.0"},
                timeout=10,
            )
        if resp.status_code != 200:
            print(f"  WARNING: Could not find card '{name}' on Scryfall (HTTP {resp.status_code})")
            return None

        card_data = _parse_scryfall_card(resp.json())
        _card_cache[card_data["name"]] = card_data
        _index_name(card_data["name"])
        return card_data
    except requests.RequestException as e:
        _api_available = False
        print(f"  WARNING: Scryfall API unavailable ({type(e).__name__}). Skipping API lookups.")
        print(f"  Run 'python cli.py init' to download bulk data for offline use.")
        return None


def _resolve_name(name):
    """Try to resolve a name to its canonical form via the index."""
    # Exact match
    if name in _card_cache:
        return name
    # Try lowercase comma-stripped match
    stripped = name.replace(",", "").lower()
    if stripped in _name_index:
        return _name_index[stripped]
    # Try just lowercase
    lower = name.lower()
    if lower in _name_index:
        return _name_index[lower]
    return None


def lookup_card(name):
    """Look up a card — tries bulk cache first, then API."""
    # Check cache (exact)
    if name in _card_cache:
        return _card_cache[name]

    # Try name index (handles comma-stripped filenames)
    canonical = _resolve_name(name)
    if canonical and canonical in _card_cache:
        return _card_cache[canonical]

    # Try without set/collector info that sometimes appears
    # Handle "Card Name (SET) 123" patterns
    clean_name = name.split(" (")[0].strip()
    if clean_name != name:
        canonical = _resolve_name(clean_name)
        if canonical and canonical in _card_cache:
            return _card_cache[canonical]

    # Load bulk data if not loaded
    if not _bulk_loaded:
        load_bulk_data()
        canonical = _resolve_name(name)
        if canonical and canonical in _card_cache:
            return _card_cache[canonical]
        if clean_name != name:
            canonical = _resolve_name(clean_name)
            if canonical and canonical in _card_cache:
                return _card_cache[canonical]

    # Fallback to API (use original name — Scryfall fuzzy handles commas)
    return fetch_card_api(name)


def get_commander_identity(commander_name, partner_name=None):
    """Get combined color identity and CMC for commander(s)."""
    card = lookup_card(commander_name)
    if not card:
        return [], 0

    identity = set(card["color_identity"])
    cmc = card["cmc"]

    if partner_name:
        partner = lookup_card(partner_name)
        if partner:
            identity.update(partner["color_identity"])

    sorted_identity = sorted(identity, key=lambda c: "WUBRG".index(c) if c in "WUBRG" else 99)
    return sorted_identity, cmc
