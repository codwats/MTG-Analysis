#!/usr/bin/env python3
"""
Quick import helper for Moxfield deck exports.

Workflow:
  1. On Moxfield, click Export → Copy for MTGO (or similar text format)
  2. Run: python moxfield_save.py
  3. Paste the deck, press Enter then Ctrl+D (or Ctrl+Z on Windows)
  4. Enter bracket rating when prompted
  5. File auto-saves to decks/ with correct naming

Alternatively, pipe from a file:
  python moxfield_save.py < exported_deck.txt
  python moxfield_save.py --bracket 2 < exported_deck.txt
  python moxfield_save.py --bracket 2 --name "Wheels" --builder "CovertGoBlue" < exported_deck.txt

Or batch-process a folder of raw exports:
  python moxfield_save.py --batch raw_exports/ --bracket 2
"""
import argparse
import os
import re
import sys


DECKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decks")


def detect_commander(lines):
    """
    Detect commander from Moxfield export format.
    
    Moxfield text exports typically look like:
    
    Format A (MTGO/Arena export with sections):
        1 Commander Name
        
        1 Card A
        1 Card B
        ...
    
    Format B (with section headers):
        // Commander
        1 Commander Name
        
        // Mainboard
        1 Card A
        ...
    
    Format C (Moxfield "Copy to clipboard"):
        COMMANDER
        1 Commander Name
        
        MAINBOARD
        1 Card A
        ...
    """
    commander = None
    partner = None
    in_commander_section = False
    commander_cards = []

    for line in lines:
        stripped = line.strip()
        
        # Section header detection
        if stripped.upper() in ("COMMANDER", "COMMANDER(S)", "// COMMANDER", "// COMMANDERS"):
            in_commander_section = True
            continue
        
        if stripped.upper() in ("MAINBOARD", "DECK", "// MAINBOARD", "// DECK",
                                 "COMPANION", "// COMPANION", "SIDEBOARD", "// SIDEBOARD",
                                 "MAYBEBOARD", "// MAYBEBOARD", "CONSIDERING", "// CONSIDERING"):
            in_commander_section = False
            continue

        # If we're in a commander section, collect cards
        if in_commander_section and stripped:
            name = re.sub(r"^\d+x?\s+", "", stripped)
            name = re.sub(r"\s*[\(\[][A-Z0-9]{2,5}[\)\]].*$", "", name).strip()
            if name:
                commander_cards.append(name)
            continue

        # If no section headers found, the first card before an empty line is often the commander
        # (MTGO format: commander is first, separated by blank line)
        if not commander and stripped and not in_commander_section:
            # Check if we haven't seen any section headers at all
            pass  # We'll handle this below

    if commander_cards:
        commander = commander_cards[0]
        if len(commander_cards) > 1:
            partner = commander_cards[1]
    else:
        # Fallback: first card in the file is likely the commander
        # (common in simple MTGO exports)
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("//") and not stripped.startswith("#"):
                name = re.sub(r"^\d+x?\s+", "", stripped)
                name = re.sub(r"\s*[\(\[][A-Z0-9]{2,5}[\)\]].*$", "", name).strip()
                if name:
                    commander = name
                    break

    return commander, partner


def extract_mainboard(text):
    """
    Extract just the card list from a Moxfield export,
    stripping section headers but preserving everything.
    Returns cleaned lines ready for our deck file format.
    """
    lines = text.strip().splitlines()
    output_lines = []
    skip_sections = {"MAYBEBOARD", "CONSIDERING", "// MAYBEBOARD", "// CONSIDERING"}
    in_skip = False

    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()

        # Skip maybeboard/considering sections
        if upper in skip_sections:
            in_skip = True
            continue

        # Re-enable on known sections
        if upper in ("MAINBOARD", "DECK", "COMMANDER", "COMMANDER(S)",
                      "SIDEBOARD", "COMPANION",
                      "// MAINBOARD", "// DECK", "// COMMANDER", "// COMMANDERS",
                      "// SIDEBOARD", "// COMPANION"):
            in_skip = False
            # Don't include section headers in output
            continue

        if in_skip:
            continue

        # Include the line (cards, blank lines)
        if stripped:
            output_lines.append(stripped)

    return "\n".join(output_lines)


def sanitize_filename(name):
    """Make a string safe for filenames while preserving readability."""
    # Remove commas (not allowed in filenames on some systems)
    name = name.replace(",", "")
    # Keep apostrophes (they're in card names)
    # Remove or replace truly problematic chars
    name = name.replace("/", "-").replace("\\", "-")
    name = name.replace(":", " -").replace('"', "'")
    name = re.sub(r"[<>|?*]", "", name)
    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def build_filename(commander, bracket, deck_name=None, partner=None, builder=None):
    """Build the standard filename."""
    parts = []

    # Commander
    cmd = sanitize_filename(commander)
    if partner:
        cmd += "+" + sanitize_filename(partner)
    parts.append(cmd)

    # Bracket
    parts.append(str(bracket))

    # Deck name
    if deck_name:
        parts.append(sanitize_filename(deck_name))
    else:
        parts.append("Deck")

    # Builder
    if builder:
        parts.append(sanitize_filename(builder))

    return "--".join(parts) + ".txt"


def save_deck(text, commander, bracket, deck_name=None, partner=None, builder=None, output_dir=None):
    """Save deck text to a properly named file."""
    output_dir = output_dir or DECKS_DIR
    os.makedirs(output_dir, exist_ok=True)

    filename = build_filename(commander, bracket, deck_name, partner, builder)
    filepath = os.path.join(output_dir, filename)

    # Don't overwrite
    if os.path.exists(filepath):
        base, ext = os.path.splitext(filename)
        i = 2
        while os.path.exists(os.path.join(output_dir, f"{base} ({i}){ext}")):
            i += 1
        filename = f"{base} ({i}){ext}"
        filepath = os.path.join(output_dir, filename)

    card_list = extract_mainboard(text)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(card_list + "\n")

    return filepath


def interactive_save(text, bracket=None, name=None, builder=None, piped=False):
    """Interactive mode: detect commander, prompt for missing info."""
    lines = text.strip().splitlines()
    commander, partner = detect_commander(lines)

    if piped:
        # Non-interactive: use detected commander or fail
        if commander:
            print(f"  Detected commander: {commander}")
            if partner:
                print(f"  Detected partner:   {partner}")
        else:
            print("  ERROR: Could not detect commander. Use interactive mode or fix export.")
            return None
    else:
        if commander:
            print(f"  Detected commander: {commander}")
            if partner:
                print(f"  Detected partner:   {partner}")
            confirm = input("  Correct? [Y/n] ").strip().lower()
            if confirm == "n":
                commander = input("  Commander name: ").strip()
                partner_input = input("  Partner (blank for none): ").strip()
                partner = partner_input if partner_input else None
        else:
            print("  Could not detect commander from export.")
            commander = input("  Commander name: ").strip()
            partner_input = input("  Partner (blank for none): ").strip()
            partner = partner_input if partner_input else None

    if not commander:
        print("Error: Commander name is required.")
        return None

    if bracket is None and not piped:
        bracket_input = input("  Bracket (1-4): ").strip()
        try:
            bracket = int(bracket_input)
        except ValueError:
            print(f"  Invalid bracket '{bracket_input}', defaulting to None")
            bracket = 0
    elif bracket is None:
        bracket = 0

    if name is None and not piped:
        name = input("  Deck name (optional): ").strip() or None

    if builder is None and not piped:
        builder = input("  Builder (optional): ").strip() or None

    filepath = save_deck(text, commander, bracket, name, partner, builder)
    print(f"\n  Saved: {os.path.basename(filepath)}")
    return filepath


def batch_process(input_dir, bracket=None, builder=None):
    """Process a folder of raw export files."""
    files = sorted(f for f in os.listdir(input_dir) if f.endswith(".txt"))
    if not files:
        print(f"No .txt files found in {input_dir}")
        return

    print(f"Processing {len(files)} files from {input_dir}\n")

    for filename in files:
        filepath = os.path.join(input_dir, filename)
        print(f"--- {filename} ---")

        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()

        interactive_save(text, bracket=bracket, builder=builder)
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Convert Moxfield exports to properly named deck files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Workflow:
  1. On Moxfield, click Export → Copy for MTGO
  2. Paste into a text file (e.g. Notepad, TextEdit) and save as .txt
  3. Run one of:

  # Single file:
  python moxfield_save.py -f export.txt -b 2 -n "Wheels"

  # Batch — dump all exports into a folder:
  python moxfield_save.py --batch raw_exports/ -b 2

  # Interactive (type/paste + Ctrl+D):
  python moxfield_save.py
""",
    )
    parser.add_argument("-f", "--file", help="Path to a Moxfield export .txt file")
    parser.add_argument("--bracket", "-b", type=int, help="Bracket rating (1-4)")
    parser.add_argument("--name", "-n", help="Deck name")
    parser.add_argument("--builder", help="Builder name")
    parser.add_argument("--batch", help="Batch process all .txt files in directory")
    parser.add_argument("--output-dir", "-o", help=f"Output directory (default: {DECKS_DIR})")

    args = parser.parse_args()

    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = DECKS_DIR

    if args.batch:
        batch_process(args.batch, bracket=args.bracket, builder=args.builder)
        return

    # Single file mode (recommended)
    if args.file:
        if not os.path.exists(args.file):
            print(f"File not found: {args.file}")
            return
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
        if not text.strip():
            print(f"File is empty: {args.file}")
            return
        interactive_save(text, bracket=args.bracket, name=args.name, builder=args.builder, piped=True)
        return

    # Stdin mode (interactive paste or pipe)
    is_piped = not sys.stdin.isatty()
    if not is_piped:
        print("Paste your Moxfield export below, then press Enter + Ctrl+D (Ctrl+Z on Windows):")
        print("(Tip: if paste loses linebreaks, save to a .txt file and use -f flag instead)\n")

    text = sys.stdin.read()

    if not text.strip():
        print("No input received.")
        return

    interactive_save(text, bracket=args.bracket, name=args.name, builder=args.builder, piped=is_piped)


if __name__ == "__main__":
    main()
