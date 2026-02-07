"""Database setup and operations for MTG Deck Analyzer."""
import sqlite3
import json
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "decks.db")


def get_connection(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path=None):
    conn = get_connection(db_path)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS cards (
        scryfall_id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        color_identity TEXT NOT NULL DEFAULT '[]',  -- JSON array
        cmc REAL NOT NULL DEFAULT 0,
        mana_cost TEXT DEFAULT '',
        type_line TEXT DEFAULT '',
        oracle_text TEXT DEFAULT '',
        keywords TEXT NOT NULL DEFAULT '[]',         -- JSON array
        is_land INTEGER NOT NULL DEFAULT 0,
        is_creature INTEGER NOT NULL DEFAULT 0,
        categories TEXT NOT NULL DEFAULT '[]'         -- JSON array of detected categories
    );

    CREATE TABLE IF NOT EXISTS decks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        source_file TEXT,
        source_url TEXT,
        source TEXT NOT NULL DEFAULT 'manual',       -- manual, archidekt, moxfield
        date_added TEXT NOT NULL DEFAULT (datetime('now')),
        builder TEXT,
        commander_name TEXT NOT NULL,
        partner_name TEXT,
        color_identity TEXT NOT NULL DEFAULT '[]',   -- JSON array from commander(s)
        color_identity_key TEXT NOT NULL DEFAULT '',  -- Sorted string like 'BGRUW' for queries
        bracket INTEGER,                              -- 1-4 or NULL
        commander_cmc REAL NOT NULL DEFAULT 0,
        -- Computed stats (updated on import)
        total_cards INTEGER DEFAULT 0,
        avg_cmc REAL DEFAULT 0,
        land_count INTEGER DEFAULT 0,
        creature_count INTEGER DEFAULT 0,
        instant_sorcery_count INTEGER DEFAULT 0,
        curve_json TEXT DEFAULT '{}',                 -- JSON {0: n, 1: n, ...}
        category_counts_json TEXT DEFAULT '{}'         -- JSON {ramp: n, draw: n, ...}
    );

    CREATE TABLE IF NOT EXISTS deck_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
        card_name TEXT NOT NULL,
        scryfall_id TEXT REFERENCES cards(scryfall_id),
        quantity INTEGER NOT NULL DEFAULT 1,
        board TEXT NOT NULL DEFAULT 'mainboard',     -- mainboard, commander, sideboard
        category_override TEXT                        -- Manual category override
    );

    CREATE INDEX IF NOT EXISTS idx_deck_cards_deck ON deck_cards(deck_id);
    CREATE INDEX IF NOT EXISTS idx_deck_cards_card ON deck_cards(card_name);
    CREATE INDEX IF NOT EXISTS idx_decks_color ON decks(color_identity_key);
    CREATE INDEX IF NOT EXISTS idx_decks_bracket ON decks(bracket);
    CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(name);
    """)
    conn.commit()
    return conn


def insert_card(conn, card_data):
    """Insert or update a card from Scryfall data."""
    conn.execute("""
        INSERT INTO cards (scryfall_id, name, color_identity, cmc, mana_cost,
                          type_line, oracle_text, keywords, is_land, is_creature, categories)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            color_identity=excluded.color_identity,
            cmc=excluded.cmc,
            mana_cost=excluded.mana_cost,
            type_line=excluded.type_line,
            oracle_text=excluded.oracle_text,
            keywords=excluded.keywords,
            is_land=excluded.is_land,
            is_creature=excluded.is_creature,
            categories=excluded.categories
    """, (
        card_data["scryfall_id"],
        card_data["name"],
        json.dumps(card_data.get("color_identity", [])),
        card_data.get("cmc", 0),
        card_data.get("mana_cost", ""),
        card_data.get("type_line", ""),
        card_data.get("oracle_text", ""),
        json.dumps(card_data.get("keywords", [])),
        1 if card_data.get("is_land", False) else 0,
        1 if card_data.get("is_creature", False) else 0,
        json.dumps(card_data.get("categories", [])),
    ))


def insert_deck(conn, deck_data):
    """Insert a deck and its cards. Returns deck id."""
    cursor = conn.execute("""
        INSERT INTO decks (name, source_file, source, builder,
                          commander_name, partner_name, color_identity,
                          color_identity_key, bracket, commander_cmc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        deck_data["name"],
        deck_data.get("source_file", ""),
        deck_data.get("source", "manual"),
        deck_data.get("builder"),
        deck_data["commander_name"],
        deck_data.get("partner_name"),
        json.dumps(deck_data["color_identity"]),
        deck_data["color_identity_key"],
        deck_data.get("bracket"),
        deck_data.get("commander_cmc", 0),
    ))
    deck_id = cursor.lastrowid

    for card_entry in deck_data.get("cards", []):
        conn.execute("""
            INSERT INTO deck_cards (deck_id, card_name, scryfall_id, quantity, board)
            VALUES (?, ?, ?, ?, ?)
        """, (
            deck_id,
            card_entry["name"],
            card_entry.get("scryfall_id"),
            card_entry.get("quantity", 1),
            card_entry.get("board", "mainboard"),
        ))

    conn.commit()
    return deck_id


def update_deck_stats(conn, deck_id):
    """Recompute and store stats for a deck."""
    rows = conn.execute("""
        SELECT dc.quantity, dc.board, c.cmc, c.type_line, c.is_land, c.is_creature, c.categories
        FROM deck_cards dc
        LEFT JOIN cards c ON dc.card_name = c.name
        WHERE dc.deck_id = ?
    """, (deck_id,)).fetchall()

    total = 0
    cmc_sum = 0.0
    nonland_count = 0
    land_count = 0
    creature_count = 0
    instant_sorcery = 0
    curve = {i: 0 for i in range(7)}  # 0-5, 6+
    category_counts = {}

    for row in rows:
        qty = row["quantity"]
        board = row["board"]
        if board == "commander":
            continue  # Don't count commander in mainboard stats

        total += qty
        is_land = row["is_land"] if row["is_land"] is not None else 0
        is_creature = row["is_creature"] if row["is_creature"] is not None else 0
        type_line = row["type_line"] or ""
        cmc = row["cmc"] or 0

        if is_land:
            land_count += qty
        else:
            nonland_count += qty
            cmc_sum += cmc * qty
            bucket = min(int(cmc), 6)
            curve[bucket] = curve.get(bucket, 0) + qty

        if is_creature:
            creature_count += qty
        if "Instant" in type_line or "Sorcery" in type_line:
            instant_sorcery += qty

        # Category counts
        cats = json.loads(row["categories"]) if row["categories"] else []
        for cat in cats:
            category_counts[cat] = category_counts.get(cat, 0) + qty

    avg_cmc = cmc_sum / nonland_count if nonland_count > 0 else 0

    conn.execute("""
        UPDATE decks SET
            total_cards = ?, avg_cmc = ?, land_count = ?,
            creature_count = ?, instant_sorcery_count = ?,
            curve_json = ?, category_counts_json = ?
        WHERE id = ?
    """, (
        total, round(avg_cmc, 2), land_count,
        creature_count, instant_sorcery,
        json.dumps(curve), json.dumps(category_counts),
        deck_id,
    ))
    conn.commit()


def get_card(conn, name):
    """Look up a card by name."""
    return conn.execute("SELECT * FROM cards WHERE name = ?", (name,)).fetchone()


def deck_exists(conn, source_file):
    """Check if a deck from this file has already been imported."""
    row = conn.execute("SELECT id FROM decks WHERE source_file = ?", (source_file,)).fetchone()
    return row is not None
