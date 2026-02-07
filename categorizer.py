"""Card categorization via oracle text pattern matching + type line analysis."""
import re

# Pattern-based categorization rules
# Each category has a list of (pattern, weight) tuples
# Weight isn't used yet but allows future confidence scoring
CATEGORY_PATTERNS = {
    "ramp": [
        (r"add\b.{0,30}\bmana\b", 1.0),
        (r"search your library for.{0,30}\bland\b.{0,30}\bonto the battlefield\b", 1.0),
        (r"put.{0,30}\bland.{0,30}\bonto the battlefield\b", 1.0),
        (r"add \{[WUBRGC]\}", 0.9),
        (r"add .{0,15}one mana of any", 1.0),
        (r"mana of any color", 0.7),
        (r"Treasure token", 0.6),
    ],
    "draw": [
        (r"draw.{0,15}\bcards?\b", 1.0),
        (r"\bdraw a card\b", 1.0),
        (r"\bscry \d", 0.5),
        (r"look at the top.{0,20}cards? of your library", 0.6),
        (r"whenever.{0,40}draw", 0.8),
        (r"impulse draw", 0.8),
        (r"exile the top.{0,30}you may (play|cast)", 0.8),
    ],
    "removal": [
        (r"destroy target.{0,30}(creature|artifact|enchantment|planeswalker|permanent|nonland)", 1.0),
        (r"exile target.{0,30}(creature|artifact|enchantment|planeswalker|permanent|nonland)", 1.0),
        (r"target.{0,20}gets? -\d+/-\d+", 0.9),
        (r"deals? \d+ damage to (target|any target)", 0.7),
        (r"return target.{0,20}to (its owner's hand|the top)", 0.7),
        (r"sacrifice.{0,15}(creature|permanent)", 0.6),
        (r"fight", 0.5),
    ],
    "board_wipe": [
        (r"destroy all.{0,20}(creature|nonland|permanent|artifact|enchantment)", 1.0),
        (r"exile all.{0,20}(creature|nonland|permanent|artifact|enchantment)", 1.0),
        (r"all creatures get -\d+/-\d+", 1.0),
        (r"each (creature|player).{0,20}sacrifice", 0.7),
        (r"deals? \d+ damage to each creature", 0.8),
    ],
    "counterspell": [
        (r"counter target spell", 1.0),
        (r"counter target.{0,30}(instant|sorcery|creature|artifact|enchantment|activated)", 0.9),
        (r"counter it\b", 0.8),
    ],
    "tutor": [
        (r"search your library for.{0,30}(card|creature|instant|sorcery|artifact|enchantment)", 1.0),
        (r"search your library.{0,40}(put it|reveal)", 1.0),
    ],
    "protection": [
        (r"\b(hexproof|shroud|indestructible|ward)\b", 0.8),
        (r"(gain|have|gets?) protection from", 0.8),
        (r"can't be (countered|the target)", 0.7),
        (r"phase out", 0.6),
    ],
    "recursion": [
        (r"return.{0,30}from.{0,15}graveyard.{0,20}(to|onto)", 1.0),
        (r"put.{0,30}from.{0,15}graveyard.{0,20}(onto|into your hand)", 1.0),
        (r"cast.{0,20}from.{0,10}graveyard", 0.9),
        (r"reanimate", 0.9),
        (r"flashback", 0.7),
    ],
}

# Cards that are universally staples — override pattern matching
KNOWN_STAPLES = {
    "ramp": [
        "Sol Ring", "Arcane Signet", "Commander's Sphere", "Mind Stone",
        "Fellwar Stone", "Thought Vessel", "Wayfarer's Bauble",
        "Burnished Hart", "Solemn Simulacrum",
        # Signets
        "Azorius Signet", "Dimir Signet", "Rakdos Signet", "Gruul Signet",
        "Selesnya Signet", "Orzhov Signet", "Izzet Signet", "Golgari Signet",
        "Boros Signet", "Simic Signet",
        # Talismans
        "Talisman of Progress", "Talisman of Dominance", "Talisman of Indulgence",
        "Talisman of Impulse", "Talisman of Unity", "Talisman of Hierarchy",
        "Talisman of Creativity", "Talisman of Resilience", "Talisman of Conviction",
        "Talisman of Curiosity",
        # Green ramp
        "Rampant Growth", "Cultivate", "Kodama's Reach", "Farseek",
        "Nature's Lore", "Three Visits", "Sakura-Tribe Elder",
        "Birds of Paradise", "Llanowar Elves", "Elvish Mystic",
    ],
    "draw": [
        "Rhystic Study", "Mystic Remora", "Sylvan Library",
        "Brainstorm", "Ponder", "Preordain", "Phyrexian Arena",
        "Beast Whisperer", "Harmonize", "Night's Whisper", "Sign in Blood",
        "Read the Bones", "Painful Truths",
    ],
    "removal": [
        "Swords to Plowshares", "Path to Exile", "Generous Gift",
        "Beast Within", "Chaos Warp", "Reality Shift",
        "Abrupt Decay", "Assassin's Trophy", "Anguished Unmaking",
        "Despark", "Vindicate", "Cyclonic Rift",
        "Feed the Swarm", "Ravenform",
    ],
    "board_wipe": [
        "Wrath of God", "Damnation", "Supreme Verdict",
        "Blasphemous Act", "Vanquish the Horde", "Farewell",
        "Toxic Deluge", "Austere Command", "Merciless Eviction",
        "Cyclonic Rift",  # Also removal, but its overload is a wipe
    ],
    "counterspell": [
        "Counterspell", "Swan Song", "Negate", "Arcane Denial",
        "Dovin's Veto", "Fierce Guardianship", "Force of Will",
        "Force of Negation", "Mana Drain", "An Offer You Can't Refuse",
    ],
    "tutor": [
        "Demonic Tutor", "Vampiric Tutor", "Enlightened Tutor",
        "Mystical Tutor", "Worldly Tutor", "Gamble",
        "Diabolic Intent", "Imperial Seal",
    ],
}

# Build reverse lookup from known staples
_KNOWN_CARD_CATEGORIES = {}
for cat, cards in KNOWN_STAPLES.items():
    for card_name in cards:
        if card_name not in _KNOWN_CARD_CATEGORIES:
            _KNOWN_CARD_CATEGORIES[card_name] = []
        _KNOWN_CARD_CATEGORIES[card_name].append(cat)


def categorize_card(card_data):
    """
    Categorize a card into functional categories.
    Returns a list of categories (a card can belong to multiple).
    """
    name = card_data.get("name", "")
    type_line = card_data.get("type_line", "")
    oracle_text = card_data.get("oracle_text", "")

    categories = set()

    # Check known staples first
    if name in _KNOWN_CARD_CATEGORIES:
        categories.update(_KNOWN_CARD_CATEGORIES[name])

    # Lands get tagged immediately
    if card_data.get("is_land"):
        categories.add("land")
        # Some lands are also ramp (fetch lands that put onto battlefield, etc.)
        # But basic/utility lands are just "land"
        if not categories - {"land"}:
            return list(categories)

    # Pattern matching on oracle text
    for category, patterns in CATEGORY_PATTERNS.items():
        for pattern, weight in patterns:
            if re.search(pattern, oracle_text, re.IGNORECASE):
                categories.add(category)
                break  # One match per category is enough

    # Type-line heuristics
    if "Land" in type_line and "land" not in categories:
        categories.add("land")

    # If no categories matched and it's not a land, label as 'other'
    if not categories:
        categories.add("other")

    return sorted(categories)
