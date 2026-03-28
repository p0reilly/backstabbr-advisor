# backstabbr-advisor

A tool for [backstabbr.com](https://www.backstabbr.com) Diplomacy games that scrapes full game history and generates strategic advisory prompts powered by AI.

## Features

- **Full history scraping** — fetches all phases from Spring 1901 to the current phase, persisted as JSON; subsequent runs fetch only new phases
- **Press scraping** — captures diplomatic message threads
- **Advisory prompts** — generates per-power strategic analysis including unit context, supply centre trajectory, relationship history, and legal orders
- **ML move recommendations** — integrates with [diplomacy_searchbot](https://github.com/facebookresearch/diplomacy_research) for model-sampled order suggestions
- **Order validation** — checks proposed orders against the diplomacy engine

## Installation

```bash
pip install -r requirements.txt
```

Optional (only needed if using `--selenium` for JS-rendered pages):
```bash
pip install -r requirements-optional.txt
```

## Authentication

backstabbr requires a session cookie (Firebase JWT). To get it:

1. Log in to backstabbr.com in your browser
2. Open DevTools → Application → Cookies → `www.backstabbr.com`
3. Copy the value of the `session` cookie

Pass it as `--cookie "session=<value>"` on the command line.

## Quick start

```bash
# Scrape full game history (saves to game_data/<id>.json)
python scrape_backstabbr.py <game_url> --cookie "session=<jwt>"

# Generate advisory prompt for a power
python advise_backstabbr.py <game_id> ENGLAND

# ML move recommendations
python searchbot_recommend.py <game_id> ENGLAND

# Validate proposed orders
python advise_backstabbr.py <game_id> ENGLAND --validate "A LON - NTH" "F EDI - NWG"
```

## Usage

### Scraper

```bash
# Full history scrape (default)
python scrape_backstabbr.py <url> --cookie "session=<jwt>"

# Suppress press scraping
python scrape_backstabbr.py <url> --cookie "session=<jwt>" --no-press

# Single-phase only (skips history)
python scrape_backstabbr.py <url> --cookie "session=<jwt>" --no-history

# Dump raw HTML or parsed state for debugging
python scrape_backstabbr.py <url> --cookie "session=<jwt>" --dump-html debug.html
python scrape_backstabbr.py <url> --cookie "session=<jwt>" --dump-state
```

### Advisor

```bash
# Current phase advisory
python advise_backstabbr.py <game_id> <power>

# Historical phase
python advise_backstabbr.py <game_id> <power> --phase F1907M

# Adjust how many recent phases of history to include
python advise_backstabbr.py <game_id> <power> --recent 5

# Validate proposed orders (outputs JSON)
python advise_backstabbr.py <game_id> <power> --validate "A PAR - BUR" "F BRE H"
```

### Searchbot recommendations

Requires the [diplomacy_searchbot](https://github.com/facebookresearch/diplomacy_research) repo with downloaded models at `~/IdeaProjects/diplomacy_searchbot`.

```bash
python searchbot_recommend.py <game_id> <power>
python searchbot_recommend.py <game_id> <power> --phase F1907M
python searchbot_recommend.py <game_id> <power> --all-powers
```

## Project structure

```
backstabbr_advisor/
    scraper.py        # HTTP fetch + HTML parsing → RawGameState
    converter.py      # RawGameState → diplomacy dicts; order conversion
    loader.py         # construct diplomacy.Game from a single state dict
    history.py        # multi-phase scraping, persistence, and validation
    press.py          # press thread scraping
    order_context.py  # BFS-based per-unit context for advisory prompts
    analysis.py       # relationship accumulation with recency decay; SC trajectory
    advisor.py        # build_advisory_prompt() — assembles full advisory markdown
    press_context.py  # press thread context formatting
    province_map.py   # province name → 3-letter code + coast resolution
    exceptions.py     # custom error hierarchy
scrape_backstabbr.py  # scraper CLI
advise_backstabbr.py  # advisory CLI
searchbot_recommend.py  # ML recommendations CLI
game_data/            # auto-created; one <game_id>.json + <game_id>_press.json per game
```

## Data storage

Scraped game state is saved to `game_data/<id>.json`. This directory is gitignored — it contains your game data and should not be committed.

## License

MIT
