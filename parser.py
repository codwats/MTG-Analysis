"""Parse deck text files into structured deck data."""
import os
import re


def parse_filename(filepath):
    """
    Parse deck metadata from filename.
    Format: {commander}--{bracket}--{deckname}.txt
    Optional: {commander}--{bracket}--{deckname}--{builder}.txt
    Partner: Commander A+Commander B--{bracket}--{deckname}.txt
    """
    basename = os.path.splitext(os.path.basename(filepath))[0]
    parts = basename.split("--")

    if len(parts) < 2:
        raise ValueError(
            f"Invalid filename format: '{basename}'\n"
            f"Expected: Commander Name--Bracket--Deck Name.txt"
        )

    commander_raw = parts[0].strip()
    bracket_raw = parts[1].strip() if len(parts) > 1 else None
    deck_name = parts[2].strip() if len(parts) > 2 else None
    builder = parts[3].strip() if len(parts) > 3 else None

    # Parse commander (handle partners with +)
    commander_name = None
    partner_name = None
    if "+" in commander_raw:
        commanders = commander_raw.split("+", 1)
        commander_name = commanders[0].strip()
        partner_name = commanders[1].strip()
    else:
        commander_name = commander_raw

    # Parse bracket
    bracket = None
    if bracket_raw:
        try:
            bracket = int(bracket_raw)
            if bracket not in (1, 2, 3, 4):
                print(f"  WARNING: Bracket {bracket} outside expected range 1-4")
        except ValueError:
            print(f"  WARNING: Could not parse bracket from '{bracket_raw}'")

    return {
        "commander_name": commander_name,
        "partner_name": partner_name,
        "bracket": bracket,
        "deck_name": deck_name or f"{commander_name} Deck",
        "builder": builder,
    }


def parse_card_list(text):
    """
    Parse a card list from text content.

    Supports formats:
        1 Sol Ring
        1x Sol Ring
        Sol Ring
        1 Sol Ring (SET) 123
        1 Sol Ring # comment
        
    Also handles section headers like:
        // Ramp
        # Creatures
        COMMANDER: Card Name
    """
    cards = []
    commander_from_text = None

    for line in text.strip().splitlines():
        line = line.strip()

        # Skip empty lines
        if not line:
            continue

        # Skip comments and section headers
        if line.startswith("//") or line.startswith("#") or line.startswith("---"):
            continue

        # Handle COMMANDER: prefix
        if line.upper().startswith("COMMANDER:"):
            commander_from_text = line.split(":", 1)[1].strip()
            # Remove quantity prefix if present
            commander_from_text = re.sub(r"^\d+x?\s+", "", commander_from_text)
            cards.append({
                "name": commander_from_text,
                "quantity": 1,
                "board": "commander",
            })
            continue

        # Handle COMPANION: prefix
        if line.upper().startswith("COMPANION:"):
            companion_name = line.split(":", 1)[1].strip()
            companion_name = re.sub(r"^\d+x?\s+", "", companion_name)
            cards.append({
                "name": companion_name,
                "quantity": 1,
                "board": "sideboard",
            })
            continue

        # Handle SIDEBOARD marker
        if line.upper().startswith("SIDEBOARD"):
            # Next cards are sideboard; we'll handle this if needed
            continue

        # Strip inline comments
        if " #" in line:
            line = line[:line.index(" #")].strip()

        # Parse "N Card Name" or "Nx Card Name" or just "Card Name"
        match = re.match(r"^(\d+)x?\s+(.+)$", line)
        if match:
            quantity = int(match.group(1))
            card_name = match.group(2).strip()
        else:
            quantity = 1
            card_name = line

        # Strip set/collector info: "Card Name (SET) 123" or "Card Name [SET]"
        card_name = re.sub(r"\s*[\(\[][A-Z0-9]{2,5}[\)\]].*$", "", card_name)
        # Strip trailing numbers that might be collector numbers
        card_name = re.sub(r"\s+\d+\s*$", "", card_name)
        # Strip leading/trailing whitespace
        card_name = card_name.strip()

        if not card_name:
            continue

        cards.append({
            "name": card_name,
            "quantity": quantity,
            "board": "mainboard",
        })

    return cards, commander_from_text


def parse_deck_file(filepath):
    """
    Parse a complete deck file (filename + contents).
    Returns structured deck data ready for enrichment and storage.
    """
    # Parse filename metadata
    meta = parse_filename(filepath)

    # Parse card list
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    cards, commander_in_text = parse_card_list(text)

    # If commander was specified in the card list, verify it matches filename
    if commander_in_text and commander_in_text != meta["commander_name"]:
        print(f"  NOTE: Commander in file '{commander_in_text}' differs from filename '{meta['commander_name']}'")
        print(f"  Using filename version: {meta['commander_name']}")

    # Ensure commander is in the card list (as commander board)
    commander_names = [meta["commander_name"]]
    if meta["partner_name"]:
        commander_names.append(meta["partner_name"])

    # Mark commander cards or add them
    existing_commander_names = {c["name"] for c in cards if c["board"] == "commander"}
    for cname in commander_names:
        if cname not in existing_commander_names:
            # Check if it's in mainboard and move it
            found = False
            for card in cards:
                if card["name"] == cname and card["board"] == "mainboard":
                    card["board"] = "commander"
                    found = True
                    break
            if not found:
                cards.append({"name": cname, "quantity": 1, "board": "commander"})

    return {
        "commander_name": meta["commander_name"],
        "partner_name": meta["partner_name"],
        "bracket": meta["bracket"],
        "name": meta["deck_name"],
        "builder": meta["builder"],
        "source_file": os.path.basename(filepath),
        "source": "manual",
        "cards": cards,
    }
