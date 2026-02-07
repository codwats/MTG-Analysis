#!/usr/bin/env python3
"""MTG Commander Deck Analyzer — CLI Interface."""
import argparse
import glob
import json
import os
import sys

from db import init_db, insert_card, insert_deck, update_deck_stats, deck_exists, get_connection
from scryfall import lookup_card, get_commander_identity, load_bulk_data, download_bulk_data
from parser import parse_deck_file
from analyzer import (
    get_top_cards,
    get_curve_profile,
    get_category_distribution,
    find_packages,
    compare_brackets,
    get_ramp_by_commander_cmc,
    get_cmc_curve_correlation,
    list_decks,
    get_db_summary,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DECKS_DIR = os.path.join(BASE_DIR, "decks")


def _resolve_color_mode(args):
    """Determine color matching mode from CLI args."""
    if hasattr(args, "include") and args.include:
        return "contains"
    return "exact"


def _add_color_args(parser):
    """Add standard color identity args to a subparser."""
    parser.add_argument("-c", "--colors", help="Color identity (e.g., UR, WBG)")
    parser.add_argument("--include", action="store_true",
                        help="Match any deck containing these colors (e.g., -c G --include matches RG, WBG, etc.)")
    parser.add_argument("-b", "--bracket", type=int, help="Bracket level")


def cmd_init(args):
    """Initialize database and download Scryfall bulk data."""
    print("Initializing database...")
    init_db()
    print("Database ready at decks.db")

    if args.skip_bulk:
        print("Skipping bulk data download (use --skip-bulk=false to download)")
        return

    bulk_path = os.path.join(BASE_DIR, "scryfall_bulk.json")
    if os.path.exists(bulk_path) and not args.force:
        print(f"Bulk data already exists at {bulk_path}")
        print("Use --force to re-download")
        load_bulk_data()
    else:
        download_bulk_data()


def cmd_import(args):
    """Import deck file(s)."""
    conn = init_db()

    # Ensure bulk data is loaded
    load_bulk_data()

    paths = []
    if args.file:
        paths = [args.file]
    elif args.dir:
        paths = sorted(glob.glob(os.path.join(args.dir, "*.txt")))
    else:
        # Default: import all .txt files from decks/ directory
        paths = sorted(glob.glob(os.path.join(DECKS_DIR, "*.txt")))

    if not paths:
        print(f"No .txt files found. Place deck files in {DECKS_DIR}/")
        return

    imported = 0
    skipped = 0
    errors = 0

    for filepath in paths:
        filename = os.path.basename(filepath)
        print(f"\n{'='*60}")
        print(f"Importing: {filename}")

        # Check for duplicates
        if deck_exists(conn, filename) and not args.force:
            print(f"  SKIPPED (already imported, use --force to re-import)")
            skipped += 1
            continue

        # If force, delete old entry
        if args.force and deck_exists(conn, filename):
            conn.execute("DELETE FROM decks WHERE source_file = ?", (filename,))
            conn.commit()

        try:
            deck_data = parse_deck_file(filepath)
        except (ValueError, FileNotFoundError) as e:
            print(f"  ERROR parsing file: {e}")
            errors += 1
            continue

        # Get commander info from Scryfall
        print(f"  Commander: {deck_data['commander_name']}")
        if deck_data.get("partner_name"):
            print(f"  Partner: {deck_data['partner_name']}")

        color_identity, commander_cmc = get_commander_identity(
            deck_data["commander_name"],
            deck_data.get("partner_name"),
        )
        color_key = "".join(sorted(color_identity, key=lambda c: "WUBRG".index(c) if c in "WUBRG" else 99))

        deck_data["color_identity"] = color_identity
        deck_data["color_identity_key"] = color_key
        deck_data["commander_cmc"] = commander_cmc

        if not color_identity:
            print(f"  WARNING: Could not determine color identity (Scryfall unavailable?)")
            print(f"  Deck will import but analysis may be limited. Re-import after running 'init'.")

        print(f"  Colors: {color_key or 'Colorless'} | Bracket: {deck_data.get('bracket', '?')} | Commander CMC: {commander_cmc}")

        # Enrich each card with Scryfall data
        card_count = 0
        not_found = []
        for card_entry in deck_data["cards"]:
            card_data = lookup_card(card_entry["name"])
            if card_data:
                insert_card(conn, card_data)
                card_entry["scryfall_id"] = card_data["scryfall_id"]
                # Update name to canonical Scryfall name
                card_entry["name"] = card_data["name"]
                card_count += 1
            else:
                not_found.append(card_entry["name"])
                # Create a stub card entry so the database is consistent
                stub_id = f"stub-{card_entry['name'][:50]}"
                stub = {
                    "scryfall_id": stub_id,
                    "name": card_entry["name"],
                    "color_identity": [],
                    "cmc": 0,
                    "mana_cost": "",
                    "type_line": "",
                    "oracle_text": "",
                    "keywords": [],
                    "is_land": "land" in card_entry["name"].lower(),
                    "is_creature": False,
                    "categories": ["other"],
                }
                insert_card(conn, stub)
                card_entry["scryfall_id"] = stub_id

        if not_found:
            print(f"  WARNING: {len(not_found)} cards not found: {', '.join(not_found[:5])}")
            if len(not_found) > 5:
                print(f"    ... and {len(not_found) - 5} more")

        # Store deck
        deck_id = insert_deck(conn, deck_data)
        conn.commit()

        # Compute stats
        update_deck_stats(conn, deck_id)
        print(f"  Imported: {card_count} cards enriched, deck ID #{deck_id}")
        imported += 1

    print(f"\n{'='*60}")
    print(f"Done: {imported} imported, {skipped} skipped, {errors} errors")

    # Optional LLM categorization
    if args.categorize and imported > 0:
        print(f"\n{'='*60}")
        print("Running LLM categorization on uncategorized cards...")
        from llm_categorizer import apply_llm_categories
        apply_llm_categories(conn)


def cmd_list(args):
    """List decks in the database."""
    conn = init_db()
    colors = list(args.colors.upper()) if args.colors else None
    color_mode = _resolve_color_mode(args)
    decks = list_decks(conn, color_identity=colors, bracket=args.bracket, color_mode=color_mode)

    if not decks:
        print("No decks found matching filters.")
        return

    print(f"\n{'ID':<5} {'Commander':<30} {'Colors':<8} {'Br':<4} {'Cards':<6} {'Avg CMC':<8} {'Lands':<6} {'Name'}")
    print("-" * 100)
    for d in decks:
        partner = f" + {d['partner_name']}" if d.get("partner_name") else ""
        print(f"{d['id']:<5} {(d['commander_name'] + partner)[:29]:<30} {d['color_identity_key']:<8} "
              f"{d['bracket'] or '?':<4} {d['total_cards'] or 0:<6} {d['avg_cmc'] or 0:<8.2f} "
              f"{d['land_count'] or 0:<6} {d['name'] or ''}")


def cmd_summary(args):
    """Show database summary."""
    conn = init_db()
    summary = get_db_summary(conn)

    print(f"\n=== MTG Deck Analyzer Database ===")
    print(f"Total decks: {summary['total_decks']}")
    print(f"Unique cards: {summary['unique_cards']}")

    if summary["by_color"]:
        print(f"\nBy color identity:")
        for entry in summary["by_color"]:
            print(f"  {entry['colors']:<20} ({entry['key']:<6}) {entry['count']} decks")

    if summary["by_bracket"]:
        print(f"\nBy bracket:")
        for entry in summary["by_bracket"]:
            print(f"  Bracket {entry['bracket']}: {entry['count']} decks")


def cmd_staples(args):
    """Find staple cards for a color identity + bracket."""
    conn = init_db()
    colors = list(args.colors.upper()) if args.colors else None
    color_mode = _resolve_color_mode(args)
    result = get_top_cards(conn, color_identity=colors, bracket=args.bracket, limit=args.limit, color_mode=color_mode)

    if "error" in result:
        print(f"Error: {result['error']}")
        return

    bracket_str = f"Bracket {result['bracket']}" if result['bracket'] else ""
    mode_str = " (includes)" if _resolve_color_mode(args) == "contains" else ""
    print(f"\n=== Top Cards: {result['color_name']}{mode_str} {bracket_str} ===")
    print(f"Based on {result['deck_count']} decks\n")

    # Display order for categories
    cat_order = ["ramp", "draw", "removal", "board_wipe", "counterspell", "tutor",
                 "protection", "recursion", "other", "land"]

    for cat in cat_order:
        cards = result["cards_by_category"].get(cat, [])
        if not cards:
            continue
        print(f"\n{cat.upper().replace('_', ' ')}:")
        for i, card in enumerate(cards[:args.limit_per_cat], 1):
            bar = "█" * int(card["percentage"] / 5)
            print(f"  {i:>2}. {card['name']:<35} {card['percentage']:>5.1f}% ({card['appearances']}/{result['deck_count']}) {bar}")

    # Also show any remaining categories
    for cat, cards in result["cards_by_category"].items():
        if cat not in cat_order and cards:
            print(f"\n{cat.upper()}:")
            for i, card in enumerate(cards[:args.limit_per_cat], 1):
                print(f"  {i:>2}. {card['name']:<35} {card['percentage']:>5.1f}% ({card['appearances']}/{result['deck_count']})")


def cmd_curve(args):
    """Show mana curve analysis."""
    conn = init_db()
    colors = list(args.colors.upper()) if args.colors else None
    color_mode = _resolve_color_mode(args)
    result = get_curve_profile(conn, color_identity=colors, bracket=args.bracket, commander_cmc=args.cmc, color_mode=color_mode)

    if "error" in result:
        print(f"Error: {result['error']}")
        return

    print(f"\n=== Mana Curve Profile ===")
    print(f"Based on {result['deck_count']} decks\n")

    print(f"{'CMC':<8} {'Avg':<8} {'Min':<6} {'Max':<6} {'Visual'}")
    print("-" * 50)
    for bucket in range(7):
        key = str(bucket)
        data = result["curve"].get(key, {"avg": 0, "min": 0, "max": 0})
        label = f"{bucket}" if bucket < 6 else "6+"
        bar = "█" * int(data["avg"])
        print(f"  {label:<6} {data['avg']:<8.1f} {data['min']:<6} {data['max']:<6} {bar}")


def cmd_categories(args):
    """Show category distribution analysis."""
    conn = init_db()
    colors = list(args.colors.upper()) if args.colors else None
    color_mode = _resolve_color_mode(args)
    result = get_category_distribution(conn, color_identity=colors, bracket=args.bracket, color_mode=color_mode)

    if "error" in result:
        print(f"Error: {result['error']}")
        return

    print(f"\n=== Category Distribution ===")
    print(f"Based on {result['deck_count']} decks\n")

    print(f"{'Category':<16} {'Avg':<8} {'Min':<6} {'Max':<6} {'Decks'}")
    print("-" * 50)
    for cat, data in sorted(result["categories"].items()):
        print(f"  {cat:<14} {data['avg']:<8.1f} {data['min']:<6} {data['max']:<6} {data['decks_with']}")


def cmd_packages(args):
    """Detect card packages."""
    conn = init_db()
    colors = list(args.colors.upper()) if args.colors else None
    color_mode = _resolve_color_mode(args)
    result = find_packages(
        conn, color_identity=colors, bracket=args.bracket,
        min_co_occurrence=args.threshold, min_cards=args.min_cards, color_mode=color_mode
    )

    if "error" in result:
        print(f"Error: {result['error']}")
        return

    print(f"\n=== Card Packages ===")
    print(f"Based on {result['deck_count']} decks\n")

    if not result["packages"]:
        print("No packages detected. Try lowering --threshold or adding more decks.")
        return

    for i, pkg in enumerate(result["packages"], 1):
        print(f"Package {i} — {pkg['frequency']}% of decks ({pkg['deck_count']}/{result['deck_count']}):")
        for card in pkg["cards"]:
            print(f"  • {card}")
        print()


def cmd_compare(args):
    """Compare two brackets."""
    conn = init_db()
    colors = list(args.colors.upper())
    color_mode = _resolve_color_mode(args)
    result = compare_brackets(conn, colors, args.bracket_a, args.bracket_b, color_mode=color_mode)

    if "error" in result:
        print(f"Error: {result['error']}")
        return

    print(f"\n=== Bracket {args.bracket_a} vs {args.bracket_b} ===")
    print(f"Bracket {args.bracket_a}: {result['decks_a']} decks | Bracket {args.bracket_b}: {result['decks_b']} decks\n")

    if result["more_in_bracket_b"]:
        print(f"Cards MORE common in Bracket {args.bracket_b}:")
        for d in result["more_in_bracket_b"][:15]:
            print(f"  {d['name']:<35} B{args.bracket_a}: {d[f'bracket_{args.bracket_a}_pct']:>5.1f}% → B{args.bracket_b}: {d[f'bracket_{args.bracket_b}_pct']:>5.1f}%  (+{d['diff']:.1f}%)")

    if result["more_in_bracket_a"]:
        print(f"\nCards MORE common in Bracket {args.bracket_a}:")
        for d in result["more_in_bracket_a"][:15]:
            print(f"  {d['name']:<35} B{args.bracket_a}: {d[f'bracket_{args.bracket_a}_pct']:>5.1f}% → B{args.bracket_b}: {d[f'bracket_{args.bracket_b}_pct']:>5.1f}%  ({d['diff']:.1f}%)")


def cmd_ramp(args):
    """Analyze ramp for a commander CMC."""
    conn = init_db()
    result = get_ramp_by_commander_cmc(conn, args.cmc)

    if "error" in result:
        print(f"Error: {result['error']}")
        return

    print(f"\n=== Ramp Analysis for {args.cmc}-CMC Commanders ===")
    print(f"Based on {result['deck_count']} decks\n")
    print(f"Average ramp count: {result['avg_ramp_count']:.1f} (range: {result['ramp_range']['min']}-{result['ramp_range']['max']})")
    print(f"Average land count: {result['avg_land_count']:.1f}")

    if result["top_ramp_cards"]:
        print(f"\nMost popular ramp cards:")
        for i, card in enumerate(result["top_ramp_cards"], 1):
            print(f"  {i:>2}. {card['name']:<35} {card['percentage']:>5.1f}%")


def cmd_cmc_curve(args):
    """Show how commander CMC correlates with curve shape and card choices."""
    conn = init_db()
    colors = list(args.colors.upper()) if args.colors else None
    color_mode = _resolve_color_mode(args)
    result = get_cmc_curve_correlation(conn, color_identity=colors, bracket=args.bracket, color_mode=color_mode)

    if "error" in result:
        print(f"Error: {result['error']}")
        return

    print(f"\n=== Commander CMC vs Curve Analysis ===")
    print(f"Based on {result['deck_count']} decks\n")

    for cmc_val in sorted(result["by_commander_cmc"].keys()):
        data = result["by_commander_cmc"][cmc_val]
        commanders = ", ".join(data["commanders"][:4])
        if len(data["commanders"]) > 4:
            commanders += f" +{len(data['commanders']) - 4} more"

        print(f"{'='*70}")
        print(f"  COMMANDER CMC {cmc_val}  ({data['deck_count']} decks)")
        print(f"  Commanders: {commanders}")
        print(f"  Avg deck CMC: {data['avg_deck_cmc']:.2f} | Ramp: {data['avg_ramp']:.1f} | "
              f"Draw: {data['avg_draw']:.1f} | Lands: {data['avg_lands']:.1f}")

        # Curve visual
        print(f"\n  Curve:")
        for bucket in range(7):
            key = str(bucket)
            val = data["avg_curve"].get(key, 0)
            label = f"  {bucket}" if bucket < 6 else " 6+"
            bar = "█" * int(val) + ("▌" if val % 1 >= 0.5 else "")
            print(f"    {label} CMC: {val:>5.1f}  {bar}")

        # Top spells by CMC bucket
        if args.spells:
            print(f"\n  Top spells by CMC slot:")
            for bucket in range(7):
                key = str(bucket)
                spells = data["top_spells_by_cmc"].get(key, [])
                if not spells:
                    continue
                label = f"{bucket}" if bucket < 6 else "6+"
                print(f"\n    --- {label} CMC ---")
                for s in spells[:args.top_n]:
                    cats = ", ".join(c for c in s["categories"] if c not in ("other", "land"))
                    cat_str = f" [{cats}]" if cats else ""
                    print(f"      {s['name']:<32} {s['pct']:>5.1f}%  ({s['count']}/{data['deck_count']}){cat_str}")

        print()


def cmd_categorize(args):
    """Run LLM categorization on uncategorized cards."""
    from llm_categorizer import apply_llm_categories, get_uncategorized_cards

    conn = init_db()

    # Show what's uncategorized
    uncategorized = get_uncategorized_cards(conn, limit=args.limit)
    if not uncategorized:
        print("All cards with oracle text are already categorized.")
        return

    print(f"\n=== LLM Categorization ===")
    print(f"Found {len(uncategorized)} uncategorized cards with oracle text.")

    if args.dry_run:
        print("\nSample uncategorized cards:")
        for card in uncategorized[:20]:
            print(f"  {card['name']:<35} {card['type_line']}")
        print(f"\nRun without --dry-run to categorize via Claude API.")
        return

    updated = apply_llm_categories(conn, limit=args.limit)
    print(f"\nDone. {updated} cards recategorized. Deck stats recomputed.")


def cmd_tag(args):
    """Manually tag a card's category."""
    conn = init_db()

    valid_categories = [
        "ramp", "draw", "removal", "board_wipe", "counterspell",
        "tutor", "protection", "recursion", "land", "other",
    ]

    if args.action == "set":
        # Validate categories
        categories = [c.strip().lower() for c in args.categories.split(",")]
        for cat in categories:
            if cat not in valid_categories:
                print(f"Invalid category '{cat}'. Valid: {', '.join(valid_categories)}")
                return

        # Check card exists
        row = conn.execute("SELECT name, categories FROM cards WHERE name = ?", (args.card_name,)).fetchone()
        if not row:
            # Try fuzzy match
            row = conn.execute(
                "SELECT name, categories FROM cards WHERE name LIKE ?",
                (f"%{args.card_name}%",)
            ).fetchone()
            if row:
                print(f"  Matched: {row['name']}")
            else:
                print(f"Card '{args.card_name}' not found in database.")
                return

        card_name = row["name"]
        old_cats = json.loads(row["categories"])

        conn.execute(
            "UPDATE cards SET categories = ? WHERE name = ?",
            (json.dumps(sorted(categories)), card_name)
        )
        conn.commit()

        # Recompute stats for decks containing this card
        deck_rows = conn.execute(
            "SELECT DISTINCT deck_id FROM deck_cards WHERE card_name = ?",
            (card_name,)
        ).fetchall()
        for dr in deck_rows:
            update_deck_stats(conn, dr["deck_id"])

        print(f"  {card_name}: {old_cats} → {categories}")
        print(f"  Updated stats for {len(deck_rows)} deck(s).")

    elif args.action == "show":
        name = args.card_name
        row = conn.execute("SELECT name, categories, type_line, oracle_text FROM cards WHERE name = ?", (name,)).fetchone()
        if not row:
            row = conn.execute(
                "SELECT name, categories, type_line, oracle_text FROM cards WHERE name LIKE ?",
                (f"%{name}%",)
            ).fetchone()
        if not row:
            print(f"Card '{name}' not found.")
            return

        cats = json.loads(row["categories"])
        print(f"\n  {row['name']}")
        print(f"  Type:       {row['type_line']}")
        print(f"  Categories: {cats}")
        print(f"  Oracle:     {row['oracle_text'][:200]}")

    elif args.action == "list":
        cat = args.card_name  # Reusing the positional arg as category filter
        if cat not in valid_categories:
            print(f"Listing all cards tagged as a category. Valid: {', '.join(valid_categories)}")
            return
        rows = conn.execute(
            "SELECT name, cmc, type_line FROM cards WHERE categories LIKE ? ORDER BY name",
            (f'%"{cat}"%',)
        ).fetchall()
        print(f"\n=== Cards tagged '{cat}' ({len(rows)} cards) ===")
        for r in rows:
            print(f"  {r['name']:<40} CMC {r['cmc']:<4} {r['type_line']}")

    else:
        print(f"Unknown action '{args.action}'. Use: set, show, list")


def main():
    parser = argparse.ArgumentParser(
        description="MTG Commander Deck Analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # init
    p_init = subparsers.add_parser("init", help="Initialize database and download Scryfall data")
    p_init.add_argument("--skip-bulk", action="store_true", help="Skip bulk data download")
    p_init.add_argument("--force", action="store_true", help="Re-download bulk data")

    # import
    p_import = subparsers.add_parser("import", help="Import deck file(s)")
    p_import.add_argument("-f", "--file", help="Single deck file to import")
    p_import.add_argument("-d", "--dir", help="Directory of deck files")
    p_import.add_argument("--force", action="store_true", help="Re-import existing decks")
    p_import.add_argument("--categorize", action="store_true",
                         help="Run LLM categorization after import (requires ANTHROPIC_API_KEY)")

    # list
    p_list = subparsers.add_parser("list", help="List decks in database")
    _add_color_args(p_list)

    # summary
    subparsers.add_parser("summary", help="Show database summary")

    # staples
    p_staples = subparsers.add_parser("staples", help="Find staple cards")
    _add_color_args(p_staples)
    p_staples.add_argument("-l", "--limit", type=int, default=50, help="Total card limit")
    p_staples.add_argument("--limit-per-cat", type=int, default=10, help="Cards per category")

    # curve
    p_curve = subparsers.add_parser("curve", help="Mana curve analysis")
    _add_color_args(p_curve)
    p_curve.add_argument("--cmc", type=int, help="Filter by commander CMC")

    # categories
    p_cats = subparsers.add_parser("categories", help="Category distribution analysis")
    _add_color_args(p_cats)

    # packages
    p_pkgs = subparsers.add_parser("packages", help="Detect card packages")
    _add_color_args(p_pkgs)
    p_pkgs.add_argument("--threshold", type=float, default=0.7, help="Min co-occurrence (0.0-1.0)")
    p_pkgs.add_argument("--min-cards", type=int, default=3, help="Min cards per package")

    # compare
    p_comp = subparsers.add_parser("compare", help="Compare two brackets")
    p_comp.add_argument("-c", "--colors", required=True, help="Color identity")
    p_comp.add_argument("--include", action="store_true",
                        help="Match any deck containing these colors")
    p_comp.add_argument("bracket_a", type=int, help="First bracket")
    p_comp.add_argument("bracket_b", type=int, help="Second bracket")

    # ramp
    p_ramp = subparsers.add_parser("ramp", help="Ramp analysis by commander CMC")
    p_ramp.add_argument("cmc", type=int, help="Commander CMC")

    # cmc-curve
    p_cmc = subparsers.add_parser("cmc-curve", help="Commander CMC vs curve correlation analysis")
    _add_color_args(p_cmc)
    p_cmc.add_argument("--spells", action="store_true",
                       help="Show top spells at each CMC slot")
    p_cmc.add_argument("--top-n", type=int, default=5,
                       help="Number of spells to show per CMC slot (default 5)")

    # categorize (LLM)
    p_cat = subparsers.add_parser("categorize", help="LLM-categorize uncategorized cards")
    p_cat.add_argument("-l", "--limit", type=int, default=200, help="Max cards to process")
    p_cat.add_argument("--dry-run", action="store_true", help="Show uncategorized cards without calling API")

    # tag (manual override)
    p_tag = subparsers.add_parser("tag", help="Manually tag card categories")
    p_tag.add_argument("action", choices=["set", "show", "list"],
                       help="set: assign categories, show: view card info, list: list cards by category")
    p_tag.add_argument("card_name", help="Card name (or category name for 'list' action)")
    p_tag.add_argument("categories", nargs="?", default="",
                       help="Comma-separated categories for 'set' (e.g., ramp,draw)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "init": cmd_init,
        "import": cmd_import,
        "list": cmd_list,
        "summary": cmd_summary,
        "staples": cmd_staples,
        "curve": cmd_curve,
        "categories": cmd_categories,
        "packages": cmd_packages,
        "compare": cmd_compare,
        "ramp": cmd_ramp,
        "cmc-curve": cmd_cmc_curve,
        "categorize": cmd_categorize,
        "tag": cmd_tag,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
