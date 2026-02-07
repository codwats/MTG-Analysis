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

```bash
# Database overview
python3 cli.py summary

# List imported decks (with optional filters)
python3 cli.py list
python3 cli.py list -c UR -b 2

# Staples by color identity and bracket
python3 cli.py staples -c UR -b 2

# Mana curve analysis
python3 cli.py curve -c WBG
python3 cli.py curve --cmc 4        # filter by commander CMC

# Category distribution (how many ramp/draw/removal slots)
python3 cli.py categories -c BG -b 2

# Detect card packages (cards that appear together)
python3 cli.py packages -c G --threshold 0.7

# Compare two bracket levels
python3 cli.py compare -c UB -b 2 3

# Ramp analysis by commander CMC
python3 cli.py ramp --cmc 5

# Run LLM categorization on uncategorized cards
python3 cli.py categorize
python3 cli.py categorize --dry-run   # preview without API calls

# View or override card categories
python3 cli.py tag show "Sol Ring"
python3 cli.py tag set "Sol Ring" ramp staple
python3 cli.py tag list ramp
```
