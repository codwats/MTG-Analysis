"""LLM-assisted card categorization using the Anthropic API."""
import json
import os
import time

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"

# Cache file for LLM categorizations (persists across runs)
LLM_CACHE_PATH = os.path.join(os.path.dirname(__file__), "llm_category_cache.json")

_llm_cache = None


def _load_cache():
    global _llm_cache
    if _llm_cache is not None:
        return _llm_cache
    if os.path.exists(LLM_CACHE_PATH):
        with open(LLM_CACHE_PATH, "r") as f:
            _llm_cache = json.load(f)
    else:
        _llm_cache = {}
    return _llm_cache


def _save_cache():
    if _llm_cache is not None:
        with open(LLM_CACHE_PATH, "w") as f:
            json.dump(_llm_cache, f, indent=2)


def _get_api_key():
    """Get Anthropic API key from environment."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        # Try .env file in project directory
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("ANTHROPIC_API_KEY="):
                        key = line.split("=", 1)[1].strip().strip("'\"")
                        break
    return key


def categorize_batch_llm(cards, batch_size=20):
    """
    Categorize a batch of cards using Claude.
    
    Args:
        cards: list of dicts with 'name', 'type_line', 'oracle_text', 'mana_cost'
        batch_size: how many cards per API call
        
    Returns:
        dict of {card_name: [categories]}
    """
    api_key = _get_api_key()
    if not api_key:
        print("  No ANTHROPIC_API_KEY found. Set it via environment variable or .env file.")
        return {}

    if not HAS_REQUESTS:
        print("  requests library not available.")
        return {}

    cache = _load_cache()
    results = {}
    uncached = []

    # Check cache first
    for card in cards:
        name = card["name"]
        if name in cache:
            results[name] = cache[name]
        else:
            uncached.append(card)

    if not uncached:
        return results

    print(f"  Categorizing {len(uncached)} cards via Claude API ({len(cards) - len(uncached)} cached)...")

    # Process in batches
    for i in range(0, len(uncached), batch_size):
        batch = uncached[i:i + batch_size]
        batch_text = "\n".join(
            f"- {c['name']} | {c.get('mana_cost', '')} | {c.get('type_line', '')} | {c.get('oracle_text', '')}"
            for c in batch
        )

        prompt = f"""Categorize each Magic: The Gathering card into one or more functional categories for Commander deck analysis. 

Categories (assign ALL that apply):
- ramp: Produces mana, fetches lands, mana rocks/dorks
- draw: Card draw, card selection, impulse draw
- removal: Targeted removal (destroy/exile/bounce single targets)
- board_wipe: Mass removal (destroys/exiles all or most creatures/permanents)
- counterspell: Counters spells
- tutor: Searches library for specific cards
- protection: Grants hexproof/indestructible/phasing/shroud/ward
- recursion: Returns cards from graveyard
- land: It's a land
- other: Doesn't fit above categories (creatures, synergy pieces, win conditions, etc.)

For each card, respond with ONLY a JSON object mapping card name to category array. No explanation.

Cards:
{batch_text}

Respond with valid JSON only, like: {{"Sol Ring": ["ramp"], "Swords to Plowshares": ["removal"]}}"""

        try:
            resp = _requests.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": MODEL,
                    "max_tokens": 2000,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )

            if resp.status_code != 200:
                print(f"  API error (HTTP {resp.status_code}): {resp.text[:200]}")
                continue

            content = resp.json()["content"][0]["text"].strip()
            # Strip markdown code fences if present
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()

            batch_results = json.loads(content)

            for name, cats in batch_results.items():
                if isinstance(cats, list):
                    results[name] = cats
                    cache[name] = cats

            _save_cache()
            print(f"    Batch {i // batch_size + 1}: {len(batch_results)} cards categorized")

            # Small delay between batches
            if i + batch_size < len(uncached):
                time.sleep(0.5)

        except _requests.RequestException as e:
            print(f"  API request failed: {type(e).__name__}")
            continue
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            print(f"  Failed to parse API response: {e}")
            continue

    return results


def categorize_single_llm(name, type_line="", oracle_text="", mana_cost=""):
    """Categorize a single card via LLM (checks cache first)."""
    cache = _load_cache()
    if name in cache:
        return cache[name]

    result = categorize_batch_llm([{
        "name": name,
        "type_line": type_line,
        "oracle_text": oracle_text,
        "mana_cost": mana_cost,
    }], batch_size=1)

    return result.get(name, ["other"])


def get_uncategorized_cards(conn, limit=100):
    """Find cards that only have 'other' category and have oracle text (worth categorizing)."""
    rows = conn.execute("""
        SELECT name, type_line, oracle_text, mana_cost
        FROM cards
        WHERE categories = '["other"]'
          AND oracle_text != ''
          AND scryfall_id NOT LIKE 'stub-%'
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def apply_llm_categories(conn, limit=200):
    """
    Find cards with only 'other' category and recategorize via LLM.
    Updates the database with results.
    """
    uncategorized = get_uncategorized_cards(conn, limit=limit)
    if not uncategorized:
        print("  No uncategorized cards to process.")
        return 0

    results = categorize_batch_llm(uncategorized)
    updated = 0

    for card in uncategorized:
        name = card["name"]
        if name in results and results[name] != ["other"]:
            conn.execute(
                "UPDATE cards SET categories = ? WHERE name = ?",
                (json.dumps(sorted(results[name])), name)
            )
            updated += 1

    conn.commit()

    # Recompute stats for all decks that contain updated cards
    if updated > 0:
        deck_ids = conn.execute("""
            SELECT DISTINCT deck_id FROM deck_cards
            WHERE card_name IN ({})
        """.format(",".join("?" * len(results))), list(results.keys())).fetchall()
        
        from db import update_deck_stats
        for row in deck_ids:
            update_deck_stats(conn, row["id"])

    print(f"  Updated {updated} card categorizations.")
    return updated
