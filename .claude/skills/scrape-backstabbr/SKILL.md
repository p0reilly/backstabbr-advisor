---
name: scrape-backstabbr
description: Scrape a backstabbr game and save/update the phase history and press threads in game_data/. Use when the user wants to fetch or refresh a backstabbr game.
disable-model-invocation: true
---

Scrape a backstabbr game and save/update the phase history in game_data/.

Arguments provided: $ARGUMENTS

Parse the following from $ARGUMENTS (positional or named):
- `url`: the backstabbr game URL (e.g. https://www.backstabbr.com/game/…)
- `cookie`: session cookie value (the JWT, NOT including "session=")

If either is missing, ask the user for it before proceeding. The cookie is
sensitive — do not log or display it beyond what is necessary.

Once you have both, run:
```bash
python3 scrape_backstabbr.py "<url>" --cookie "session=<cookie>"
```

Report the phase that was saved, number of press threads saved, and any errors.
