# edh-parse

Personal Commander (EDH) toolkit: pull EDHREC data, price via Scryfall, compare ownership from a ManaBox/stock CSV, and emit shopping lists + HTML reports.

## Install

```bash
pip install -e .
```

Then:

```bash
edhparse --help
edhparse web          # local UI at http://127.0.0.1:8765
```

Or without install (from repo root):

```bash
python -m edhparse --help
```

## Prefer the web UI

Flags used to be the whole product. Open the UI instead:

```bash
edhparse web
```

Each workflow is a form with sensible defaults (`inputs/Stock.csv`, `outputs/…`). Generated HTML reports are linked from the result page and also served under `/outputs/…`.

## CLI cookbook

### Collection summary (EDHREC averages + ownership)

```bash
edhparse summary --commanders inputs/comlist --stock inputs/Stock.csv --write-html
edhparse summary -c "Atraxa, Praetors' Voice" --stock inputs/Stock.csv --html outputs/summary.html
```

### Shopping list (ManaPool + Zulus)

```bash
edhparse shopping-list inputs/new --stock inputs/Stock.csv --binders Stock Binder "secret binder"
```

Or use **Shopping list** in `edhparse web`. Drops basic lands, merges counts, subtracts owned copies, prices buys on ManaPool (cheapest NM), and checks Zulus with rate limiting.


```bash
edhparse convert inputs/old inputs/new --stock inputs/Stock.csv --binders Stock Binder
```

### Safe shopping / new cards / pivot

```bash
edhparse safe-shopping --commanders inputs/comlist --stock inputs/Stock.csv --binders stock binder
edhparse new-cards --commanders inputs/comlist
edhparse pivot --list path/to/deck.txt
```

### Evaluate a deck (cuts, adds, 1-for-1 swaps)

```bash
edhparse evaluate inputs/my-deck.txt
edhparse evaluate inputs/my-deck.txt -c "Tymna the Weaver; Kraum, Ludevic's Opus" --max-add-budget 15 --export-csv outputs/scores.csv
```

Or use **Evaluate deck** in `edhparse web` (paste a list or give a path). Accepts Moxfield / Archidekt / MTGO / Arena
exports; lines tagged `#!protect` are never suggested as cuts. Every non-land card gets five scores on a −100…+100
scale where 0 is the median card already in your deck:

| Score | What it measures | Data |
|-------|------------------|------|
| S1 Bag | Inclusion in decks that share your build path (pairs of your anchor cards), leave-one-out | edhrec.com "With card" filters (HTML), theme pages as fallback |
| S2 Surv | Cheap cards that survive in expensive decks vs. budget-only cards | `/expensive` vs `/budget` pages |
| S3 Hub | Co-occurrence with your 5 strongest engine cards across all commanders | `/cards/<engine>` pages (lift) |
| S4 Struct | Mana-curve / card-type fit and starved roles (ramp, draw, removal, wipes) | optimised average deck + Scryfall oracle tags |
| S5 | Weighted ensemble with trap / secret-tech / role guardrails, then Hungarian 1-for-1 matching | |

A cold run makes at most `--max-requests` (22) EDHREC requests with 1.8–3.2 s polite gaps, plus a handful of Scryfall
tag searches; re-runs inside `--cache-ttl-days` (7) make none. Card metadata comes from the local Scryfall bulk DB and
prices from the ManaPool feed (Scryfall as fallback); cards with no known price are never suggested as adds.

### Artist signings / compare / locate / diff

```bash
edhparse artist-signings --stock inputs/Stock.csv --artists inputs/artists --binders Stock Binder
edhparse compare atraxa-praetors-voice giada-font-of-hope
edhparse locate names.txt --stock inputs/Stock.csv
edhparse diff path/to/pairs --stock inputs/Stock.csv
```

### WIP commander pipeline

```bash
edhparse wipcoms fetch -i inputs/wantcoms -o wipcoms
edhparse wipcoms singleton wipcoms/some-commander --in-place --commander-last
```

### Legacy argparse

The old flat-flag CLI still exists if you need it:

```bash
python -m edhparse.cli -cl inputs/comlist -mb inputs/Stock.csv -html
```

## Layout

| Path | Role |
|------|------|
| `edhparse/services/` | Shared business logic (CLI + web) |
| `edhparse/evaluate/` | Five-score deck evaluator (EDHREC cohorts, S1–S5, swap matcher) |
| `edhparse/cli_app.py` | Typer subcommands |
| `edhparse/web/` | FastAPI + Jinja2 UI |
| `core/` | Deck, Card, Collection, stock, config |
| `clients/` | Scryfall / EDHREC / session / bulk |
| `renderers/` | Self-contained HTML reports |
| `inputs/` | Commander lists, Stock.csv, artists |
| `outputs/` | Generated reports |
| `wipcoms/` | Fetched EDHREC dumps |

## Tests

```bash
python -m pytest tests/ -q
```

Unit tests do not require network.
