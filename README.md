# MTG-Analysis
A tool to collect, store, and analyze Commander decks from experienced builders to identify patterns, common packages, and strategic insights that help me become a stronger deck builder.

## Setup

### Install dependencies

Create a virtual environment and install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Each time you open a new terminal, activate the venv before running commands:

```bash
source venv/bin/activate
```

### Initialize the database and download Scryfall card data

This creates `decks.db` and downloads the Scryfall bulk data (~35MB) for offline card lookups:

```bash
python3 cli.py init
```

If you need to re-download the Scryfall data later:

```bash
python3 cli.py init --force
```

### (Optional) LLM categorization

To use Claude for smarter card categorization, set your API key:

```bash
export ANTHROPIC_API_KEY=your-key-here
```

Or create a `.env` file in the project root with `ANTHROPIC_API_KEY=your-key-here`.

## Usage

### Adding decks

1. Save deck lists as text files in the `decks/` directory
2. Naming convention: `{Commander Name}--{Bracket}--{tag}.txt`
   - Partner commanders: `{Commander A}+{Commander B}--{Bracket}--{tag}.txt`
   - Bracket is 1-4 (power level)
   - Example: `baylen the haymaker--2--defcat.txt`

To convert a Moxfield export, you can use the helper script interactively:

```bash
python3 moxfield_save.py
```

Or point it at an export file:

```bash
python3 moxfield_save.py -f exported_deck.txt -c "Commander Name" -b 2
```

### Importing decks

Import all decks from the `decks/` directory:

```bash
python3 cli.py import -d decks/
```

Import a single deck:

```bash
python3 cli.py import -f decks/some-deck--2--defcat.txt
```

Re-import already imported decks with `--force`. Run LLM categorization during import with `--categorize`.

### Analysis commands

Most commands accept `-c` for color identity and `-b` for bracket filtering.

By default, `-c G` matches **only mono-Green** decks. Add `--include` to match **any deck containing Green** (RG, WRG, UG, etc.):

```bash
python3 cli.py staples -c G             # mono-Green only (2 decks)
python3 cli.py staples -c G --include   # all decks with Green (27 decks)
python3 cli.py staples -c RG --include  # all decks with both Red and Green
```

```bash
# Database overview
python3 cli.py summary

# List imported decks
python3 cli.py list
python3 cli.py list -c R --include     # all decks containing red

# Staples by color identity and bracket
python3 cli.py staples -c UR -b 2
python3 cli.py staples -c G --include  # green staples across all green decks

# Mana curve analysis
python3 cli.py curve -c WBG
python3 cli.py curve --cmc 4           # filter by commander CMC

# Commander CMC vs curve correlation
python3 cli.py cmc-curve -c G --include           # how curves shift with commander cost
python3 cli.py cmc-curve -c G --include --spells   # also show top spells at each CMC slot
python3 cli.py cmc-curve --spells --top-n 3        # across all decks, 3 spells per slot

# Category distribution (how many ramp/draw/removal slots)
python3 cli.py categories -c BG -b 2

# Detect card packages (cards that appear together)
python3 cli.py packages -c G --include --threshold 0.7

# Compare two bracket levels
python3 cli.py compare -c G --include 2 3

# Ramp analysis by commander CMC
python3 cli.py ramp 5

# Run LLM categorization on uncategorized cards
python3 cli.py categorize
python3 cli.py categorize --dry-run    # preview without API calls

# View or override card categories
python3 cli.py tag show "Sol Ring"
python3 cli.py tag set "Sol Ring" ramp,draw
python3 cli.py tag list ramp
```
