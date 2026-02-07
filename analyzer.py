"""Analysis queries for the deck database."""
import json
from collections import Counter, defaultdict


def _color_key(colors):
    """Convert color list to sorted key string."""
    return "".join(sorted(colors, key=lambda c: "WUBRG".index(c) if c in "WUBRG" else 99))


def _get_matching_deck_ids(conn, color_identity=None, bracket=None, commander_cmc=None,
                           commander_name=None, color_mode="exact"):
    """Get deck IDs matching the given filters.

    color_mode options:
        "exact"    — color_identity_key must match exactly (e.g., G = mono-green only)
        "contains" — deck must contain ALL specified colors (e.g., G = any deck with green)
        "subset"   — deck colors must be a subset of specified colors (e.g., RG = mono-R, mono-G, or RG)
    """
    conditions = []
    params = []

    if color_identity is not None:
        if color_mode == "exact":
            key = _color_key(color_identity)
            conditions.append("color_identity_key = ?")
            params.append(key)
        elif color_mode == "contains":
            # Every specified color must appear in the deck's color_identity_key
            for color in color_identity:
                conditions.append("color_identity_key LIKE ?")
                params.append(f"%{color}%")
        elif color_mode == "subset":
            # Deck colors must only contain colors from the specified set
            key = _color_key(color_identity)
            allowed = set(key)
            # Build a regex-like filter: every char in color_identity_key must be in allowed
            # SQLite doesn't have great regex, so we exclude any color NOT in the set
            for color in "WUBRG":
                if color not in allowed:
                    conditions.append("color_identity_key NOT LIKE ?")
                    params.append(f"%{color}%")

    if bracket is not None:
        conditions.append("bracket = ?")
        params.append(bracket)

    if commander_cmc is not None:
        conditions.append("commander_cmc = ?")
        params.append(commander_cmc)

    if commander_name is not None:
        conditions.append("(commander_name = ? OR partner_name = ?)")
        params.extend([commander_name, commander_name])

    where = " AND ".join(conditions) if conditions else "1=1"
    rows = conn.execute(f"SELECT id FROM decks WHERE {where}", params).fetchall()
    return [row["id"] for row in rows]


def _color_name(key):
    """Convert color identity key to common name."""
    names = {
        "": "Colorless",
        "W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green",
        "BU": "Dimir", "GU": "Simic", "RU": "Izzet", "BG": "Golgari",
        "BR": "Rakdos", "GR": "Gruul", "RW": "Boros", "UW": "Azorius",
        "BW": "Orzhov", "GW": "Selesnya",
        "BGU": "Sultai", "BRU": "Grixis", "GRU": "Temur",
        "BRW": "Mardu", "BGW": "Abzan", "GUW": "Bant",
        "GRW": "Naya", "BUW": "Esper", "RUW": "Jeskai", "BGR": "Jund",
        "BGRU": "Glint-Eye", "BGRW": "Dune", "BGUW": "Witch",
        "GRUW": "Ink", "BRUW": "Yore",
        "BGRUW": "Five-Color",
    }
    return names.get(key, key)


def get_top_cards(conn, color_identity=None, bracket=None, limit=50, min_appearances=2, color_mode="exact"):
    """
    Find the most commonly played cards across matching decks.
    Returns cards grouped by category with frequency data.
    """
    deck_ids = _get_matching_deck_ids(conn, color_identity=color_identity, bracket=bracket, color_mode=color_mode)
    if not deck_ids:
        return {"error": "No matching decks found", "deck_count": 0}

    placeholders = ",".join("?" * len(deck_ids))
    rows = conn.execute(f"""
        SELECT dc.card_name, c.categories, c.cmc, c.type_line,
               COUNT(DISTINCT dc.deck_id) as deck_count
        FROM deck_cards dc
        LEFT JOIN cards c ON dc.card_name = c.name
        WHERE dc.deck_id IN ({placeholders})
          AND dc.board = 'mainboard'
        GROUP BY dc.card_name
        HAVING deck_count >= ?
        ORDER BY deck_count DESC
    """, deck_ids + [min_appearances]).fetchall()

    total_decks = len(deck_ids)
    by_category = defaultdict(list)

    for row in rows:
        cats = json.loads(row["categories"]) if row["categories"] else ["other"]
        entry = {
            "name": row["card_name"],
            "appearances": row["deck_count"],
            "percentage": round(row["deck_count"] / total_decks * 100, 1),
            "cmc": row["cmc"],
            "type_line": row["type_line"] or "",
        }
        # File under primary category
        primary = cats[0] if cats else "other"
        by_category[primary].append(entry)

    # Sort each category by frequency
    for cat in by_category:
        by_category[cat].sort(key=lambda x: -x["appearances"])

    color_key = _color_key(color_identity) if color_identity else "ALL"
    return {
        "color_identity": color_identity or "ALL",
        "color_name": _color_name(color_key),
        "bracket": bracket,
        "deck_count": total_decks,
        "cards_by_category": dict(by_category),
    }


def get_curve_profile(conn, color_identity=None, bracket=None, commander_cmc=None, color_mode="exact"):
    """
    Analyze mana curve distribution across matching decks.
    Returns average curve and range.
    """
    deck_ids = _get_matching_deck_ids(
        conn, color_identity=color_identity, bracket=bracket, commander_cmc=commander_cmc,
        color_mode=color_mode
    )
    if not deck_ids:
        return {"error": "No matching decks found", "deck_count": 0}

    curves = []
    for did in deck_ids:
        row = conn.execute("SELECT curve_json FROM decks WHERE id = ?", (did,)).fetchone()
        if row and row["curve_json"]:
            curves.append(json.loads(row["curve_json"]))

    if not curves:
        return {"error": "No curve data available", "deck_count": len(deck_ids)}

    # Compute averages
    avg_curve = {}
    for bucket in range(7):
        key = str(bucket)
        values = [c.get(key, 0) for c in curves]
        avg_curve[key] = {
            "avg": round(sum(values) / len(values), 1),
            "min": min(values),
            "max": max(values),
        }

    return {
        "color_identity": color_identity or "ALL",
        "bracket": bracket,
        "commander_cmc": commander_cmc,
        "deck_count": len(deck_ids),
        "curve": avg_curve,
    }


def get_category_distribution(conn, color_identity=None, bracket=None, color_mode="exact"):
    """
    Analyze how many slots decks dedicate to each category.
    """
    deck_ids = _get_matching_deck_ids(conn, color_identity=color_identity, bracket=bracket, color_mode=color_mode)
    if not deck_ids:
        return {"error": "No matching decks found", "deck_count": 0}

    distributions = defaultdict(list)

    for did in deck_ids:
        row = conn.execute("SELECT category_counts_json FROM decks WHERE id = ?", (did,)).fetchone()
        if row and row["category_counts_json"]:
            cats = json.loads(row["category_counts_json"])
            for cat, count in cats.items():
                distributions[cat].append(count)

    result = {}
    for cat, values in sorted(distributions.items()):
        result[cat] = {
            "avg": round(sum(values) / len(values), 1),
            "min": min(values),
            "max": max(values),
            "decks_with": len(values),
        }

    return {
        "color_identity": color_identity or "ALL",
        "bracket": bracket,
        "deck_count": len(deck_ids),
        "categories": result,
    }


def find_packages(conn, color_identity=None, bracket=None, min_co_occurrence=0.7, min_cards=3, color_mode="exact"):
    """
    Find groups of cards that frequently appear together.
    Uses a simple co-occurrence approach.
    """
    deck_ids = _get_matching_deck_ids(conn, color_identity=color_identity, bracket=bracket, color_mode=color_mode)
    if len(deck_ids) < 3:
        return {"error": "Need at least 3 matching decks for package detection", "deck_count": len(deck_ids)}

    # Build card presence matrix: {card_name: set(deck_ids)}
    placeholders = ",".join("?" * len(deck_ids))
    rows = conn.execute(f"""
        SELECT dc.card_name, dc.deck_id
        FROM deck_cards dc
        WHERE dc.deck_id IN ({placeholders})
          AND dc.board = 'mainboard'
    """, deck_ids).fetchall()

    card_decks = defaultdict(set)
    for row in rows:
        card_decks[row["card_name"]].add(row["deck_id"])

    total_decks = len(deck_ids)

    # Filter to cards appearing in at least 30% of decks (common enough to analyze)
    common_cards = {
        name: decks for name, decks in card_decks.items()
        if len(decks) / total_decks >= 0.3
        and name not in ("Sol Ring", "Command Tower", "Arcane Signet")  # Skip auto-includes
    }

    # Find co-occurring pairs
    card_names = sorted(common_cards.keys())
    packages = []

    # Simple greedy clustering: start with highest co-occurrence pairs
    co_occurrence = {}
    for i, card_a in enumerate(card_names):
        for card_b in card_names[i + 1:]:
            shared = len(common_cards[card_a] & common_cards[card_b])
            union = len(common_cards[card_a] | common_cards[card_b])
            if union > 0:
                jaccard = shared / union
                if jaccard >= min_co_occurrence:
                    co_occurrence[(card_a, card_b)] = jaccard

    # Greedy cluster building
    used = set()
    for (a, b), score in sorted(co_occurrence.items(), key=lambda x: -x[1]):
        if a in used and b in used:
            continue
        cluster = {a, b}
        # Try to grow the cluster
        for card_c in card_names:
            if card_c in cluster or card_c in used:
                continue
            # Check co-occurrence with all cluster members
            fits = True
            for member in cluster:
                pair = tuple(sorted([card_c, member]))
                if co_occurrence.get(pair, 0) < min_co_occurrence:
                    fits = False
                    break
            if fits:
                cluster.add(card_c)

        if len(cluster) >= min_cards:
            # Calculate package frequency
            decks_with_all = set.intersection(*(common_cards[c] for c in cluster))
            packages.append({
                "cards": sorted(cluster),
                "frequency": round(len(decks_with_all) / total_decks * 100, 1),
                "deck_count": len(decks_with_all),
            })
            used.update(cluster)

    packages.sort(key=lambda p: -p["frequency"])

    return {
        "color_identity": color_identity or "ALL",
        "bracket": bracket,
        "deck_count": total_decks,
        "packages": packages,
    }


def compare_brackets(conn, color_identity, bracket_a, bracket_b, color_mode="exact"):
    """
    Compare two bracket levels within the same color identity.
    Finds cards that differentiate the brackets.
    """
    ids_a = _get_matching_deck_ids(conn, color_identity=color_identity, bracket=bracket_a, color_mode=color_mode)
    ids_b = _get_matching_deck_ids(conn, color_identity=color_identity, bracket=bracket_b, color_mode=color_mode)

    if not ids_a or not ids_b:
        return {
            "error": f"Need decks in both brackets (bracket {bracket_a}: {len(ids_a)}, bracket {bracket_b}: {len(ids_b)})",
        }

    def get_card_frequencies(deck_ids):
        placeholders = ",".join("?" * len(deck_ids))
        rows = conn.execute(f"""
            SELECT card_name, COUNT(DISTINCT deck_id) as cnt
            FROM deck_cards
            WHERE deck_id IN ({placeholders}) AND board = 'mainboard'
            GROUP BY card_name
        """, deck_ids).fetchall()
        total = len(deck_ids)
        return {row["card_name"]: row["cnt"] / total for row in rows}

    freq_a = get_card_frequencies(ids_a)
    freq_b = get_card_frequencies(ids_b)

    all_cards = set(freq_a.keys()) | set(freq_b.keys())

    # Find cards with biggest frequency difference
    diffs = []
    for card in all_cards:
        fa = freq_a.get(card, 0)
        fb = freq_b.get(card, 0)
        diff = fb - fa  # Positive means more common in bracket_b
        if abs(diff) > 0.2:  # At least 20% difference
            diffs.append({
                "name": card,
                f"bracket_{bracket_a}_pct": round(fa * 100, 1),
                f"bracket_{bracket_b}_pct": round(fb * 100, 1),
                "diff": round(diff * 100, 1),
            })

    diffs.sort(key=lambda x: -abs(x["diff"]))

    return {
        "color_identity": color_identity,
        "bracket_a": bracket_a,
        "bracket_b": bracket_b,
        "decks_a": len(ids_a),
        "decks_b": len(ids_b),
        "more_in_bracket_b": [d for d in diffs if d["diff"] > 0][:20],
        "more_in_bracket_a": [d for d in diffs if d["diff"] < 0][:20],
    }


def get_ramp_by_commander_cmc(conn, cmc):
    """Analyze ramp patterns for commanders at a given CMC."""
    deck_ids = _get_matching_deck_ids(conn, commander_cmc=cmc)
    if not deck_ids:
        return {"error": f"No decks with commander CMC {cmc}", "deck_count": 0}

    ramp_counts = []
    land_counts = []
    ramp_cards = Counter()

    for did in deck_ids:
        row = conn.execute("SELECT category_counts_json, land_count FROM decks WHERE id = ?", (did,)).fetchone()
        if row:
            cats = json.loads(row["category_counts_json"]) if row["category_counts_json"] else {}
            ramp_counts.append(cats.get("ramp", 0))
            land_counts.append(row["land_count"] or 0)

        # Get specific ramp cards
        ramp_rows = conn.execute("""
            SELECT dc.card_name
            FROM deck_cards dc
            JOIN cards c ON dc.card_name = c.name
            WHERE dc.deck_id = ? AND dc.board = 'mainboard'
              AND c.categories LIKE '%ramp%'
        """, (did,)).fetchall()
        for r in ramp_rows:
            ramp_cards[r["card_name"]] += 1

    total = len(deck_ids)
    avg_ramp = sum(ramp_counts) / total if total else 0
    avg_lands = sum(land_counts) / total if total else 0

    top_ramp = [
        {"name": name, "appearances": cnt, "percentage": round(cnt / total * 100, 1)}
        for name, cnt in ramp_cards.most_common(20)
    ]

    return {
        "commander_cmc": cmc,
        "deck_count": total,
        "avg_ramp_count": round(avg_ramp, 1),
        "avg_land_count": round(avg_lands, 1),
        "ramp_range": {"min": min(ramp_counts) if ramp_counts else 0, "max": max(ramp_counts) if ramp_counts else 0},
        "top_ramp_cards": top_ramp,
    }


def list_decks(conn, color_identity=None, bracket=None, limit=50, color_mode="exact"):
    """List decks in the database with optional filters."""
    conditions = []
    params = []

    if color_identity is not None:
        if color_mode == "exact":
            key = _color_key(color_identity)
            conditions.append("color_identity_key = ?")
            params.append(key)
        elif color_mode == "contains":
            for color in color_identity:
                conditions.append("color_identity_key LIKE ?")
                params.append(f"%{color}%")
        elif color_mode == "subset":
            allowed = set(_color_key(color_identity))
            for color in "WUBRG":
                if color not in allowed:
                    conditions.append("color_identity_key NOT LIKE ?")
                    params.append(f"%{color}%")
    if bracket is not None:
        conditions.append("bracket = ?")
        params.append(bracket)

    where = " AND ".join(conditions) if conditions else "1=1"
    rows = conn.execute(f"""
        SELECT id, name, commander_name, partner_name, color_identity_key,
               bracket, commander_cmc, total_cards, avg_cmc, land_count, builder, date_added
        FROM decks
        WHERE {where}
        ORDER BY date_added DESC
        LIMIT ?
    """, params + [limit]).fetchall()

    return [dict(row) for row in rows]


def get_cmc_curve_correlation(conn, color_identity=None, bracket=None, color_mode="exact"):
    """
    Analyze how commander CMC correlates with mana curve shape and card choices.
    Groups decks by commander CMC and compares curves, ramp counts, and
    which spells appear at each point on the curve.
    """
    deck_ids = _get_matching_deck_ids(
        conn, color_identity=color_identity, bracket=bracket, color_mode=color_mode
    )
    if not deck_ids:
        return {"error": "No matching decks found", "deck_count": 0}

    # Group decks by commander CMC
    cmc_groups = defaultdict(list)
    for did in deck_ids:
        row = conn.execute(
            "SELECT id, commander_name, commander_cmc, curve_json, category_counts_json, "
            "land_count, avg_cmc FROM decks WHERE id = ?", (did,)
        ).fetchone()
        if row:
            cmc_bucket = int(row["commander_cmc"])
            cmc_groups[cmc_bucket].append(dict(row))

    if not cmc_groups:
        return {"error": "No curve data available", "deck_count": len(deck_ids)}

    results = {}
    for cmc_val in sorted(cmc_groups.keys()):
        group = cmc_groups[cmc_val]
        n = len(group)

        # Aggregate curves
        curves = [json.loads(d["curve_json"]) for d in group if d["curve_json"]]
        avg_curve = {}
        for bucket in range(7):
            key = str(bucket)
            values = [c.get(key, 0) for c in curves]
            avg_curve[key] = round(sum(values) / len(values), 1) if values else 0

        # Aggregate category counts
        ramp_counts = []
        draw_counts = []
        land_counts = []
        avg_cmcs = []
        for d in group:
            cats = json.loads(d["category_counts_json"]) if d["category_counts_json"] else {}
            ramp_counts.append(cats.get("ramp", 0))
            draw_counts.append(cats.get("draw", 0))
            land_counts.append(d["land_count"] or 0)
            avg_cmcs.append(d["avg_cmc"] or 0)

        # Get the most common spells at each CMC bucket in these decks
        placeholders = ",".join("?" * len([d["id"] for d in group]))
        group_ids = [d["id"] for d in group]

        spells_by_cmc = {}
        for bucket in range(7):
            if bucket < 6:
                cmc_filter = "c.cmc = ?"
                cmc_param = bucket
            else:
                cmc_filter = "c.cmc >= ?"
                cmc_param = 6

            rows = conn.execute(f"""
                SELECT dc.card_name, c.categories, c.type_line,
                       COUNT(DISTINCT dc.deck_id) as cnt
                FROM deck_cards dc
                JOIN cards c ON dc.card_name = c.name
                WHERE dc.deck_id IN ({placeholders})
                  AND dc.board = 'mainboard'
                  AND c.is_land = 0
                  AND {cmc_filter}
                GROUP BY dc.card_name
                ORDER BY cnt DESC
                LIMIT 8
            """, group_ids + [cmc_param]).fetchall()

            spells_by_cmc[str(bucket)] = [
                {
                    "name": r["card_name"],
                    "count": r["cnt"],
                    "pct": round(r["cnt"] / n * 100, 1),
                    "categories": json.loads(r["categories"]) if r["categories"] else [],
                    "type_line": r["type_line"] or "",
                }
                for r in rows
            ]

        results[cmc_val] = {
            "commander_cmc": cmc_val,
            "deck_count": n,
            "commanders": list(set(d["commander_name"] for d in group)),
            "avg_deck_cmc": round(sum(avg_cmcs) / n, 2) if avg_cmcs else 0,
            "avg_curve": avg_curve,
            "avg_ramp": round(sum(ramp_counts) / n, 1) if ramp_counts else 0,
            "avg_draw": round(sum(draw_counts) / n, 1) if draw_counts else 0,
            "avg_lands": round(sum(land_counts) / n, 1) if land_counts else 0,
            "top_spells_by_cmc": spells_by_cmc,
        }

    return {
        "color_identity": color_identity or "ALL",
        "bracket": bracket,
        "deck_count": len(deck_ids),
        "by_commander_cmc": results,
    }


def get_db_summary(conn):
    """Get a summary of what's in the database."""
    total_decks = conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0]
    total_cards = conn.execute("SELECT COUNT(DISTINCT card_name) FROM deck_cards").fetchone()[0]

    # Decks by color identity
    color_rows = conn.execute("""
        SELECT color_identity_key, COUNT(*) as cnt
        FROM decks GROUP BY color_identity_key ORDER BY cnt DESC
    """).fetchall()

    # Decks by bracket
    bracket_rows = conn.execute("""
        SELECT bracket, COUNT(*) as cnt
        FROM decks WHERE bracket IS NOT NULL GROUP BY bracket ORDER BY bracket
    """).fetchall()

    return {
        "total_decks": total_decks,
        "unique_cards": total_cards,
        "by_color": [
            {"colors": _color_name(row["color_identity_key"]), "key": row["color_identity_key"], "count": row["cnt"]}
            for row in color_rows
        ],
        "by_bracket": [{"bracket": row["bracket"], "count": row["cnt"]} for row in bracket_rows],
    }
