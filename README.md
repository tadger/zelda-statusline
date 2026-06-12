# zelda-statusline

A Zelda-themed custom status line for [Claude Code](https://claude.com/claude-code).

It shows your **git branch**, the active **model + effort**, and **context-window
usage** rendered as draining heart containers — plus the session cost in USD.

```
⎇ main  │  Opus 4.8·high  │  ♥ ♥ ♥ ♥ ♥ ♥ ♥ ♥ ♡ ♡ 21%  $0.42
```

(Hearts are Material Design Nerd Font glyphs; the ASCII above is just an
approximation.)

## Features

- **Heart containers as context health** — you start with 10 full hearts and lose
  them as the context fills. Real **half-heart** glyph for the boundary container.
- **Warning tiers on the percentage** — dim → yellow (≤40% health) →
  bold red (≤20% health, i.e. nearly full / about to compact).
- **Auto-detecting window** — 1,000,000 tokens for `[1m]` models, 200,000 otherwise.
- **Model in purple, effort in pink.** At `max` effort the model text gets a
  **rainbow shimmer**, recreating Claude Code's effort-selector effect.
- **Session cost in USD** (turquoise), shown when Claude Code provides it.
- **Responsive layout** — one line when it fits, automatically stacking onto two
  or three rows on narrow terminals (reads `$COLUMNS`).
- **Middle-truncated branch names** — long branches become `feature/JIRA…desc`
  so they never blow out the line.

## Requirements

- **Claude Code v2.1.153+** (for `$COLUMNS`-based responsive wrapping).
- **Python 3** (standard library only — no dependencies).
- A **patched Nerd Font** in your terminal for the heart glyphs.
- A terminal that supports **256-color** for the purple/pink/turquoise (the
  rainbow uses plain 16-color, so it always renders).

## Install

1. Copy or symlink the script somewhere stable, e.g.:

   ```sh
   ln -s "$PWD/statusline-zelda.py" ~/.claude/statusline-zelda.py
   ```

2. Point your Claude Code `statusLine` at it in `~/.claude/settings.json`:

   ```json
   {
     "statusLine": {
       "type": "command",
       "command": "python3 /Users/you/.claude/statusline-zelda.py",
       "padding": 0
     }
   }
   ```

3. Start (or reload) Claude Code.

## Preview

See every color and health state without touching your real session:

```sh
python3 statusline-zelda.py --demo
```

## Configuration

All knobs live at the top of `statusline-zelda.py`:

| Constant | Purpose |
|---|---|
| `RED`, `GREY`, `PURPLE`, `PINK`, `YELLOW`, `COST`, `RAINBOW` | colors |
| `LOW_HEALTH` / `CAUTION` | percentage warning thresholds (fraction *remaining*) |
| `HEARTS` | number of containers |
| `MAX_BRANCH` | branch-name length cap before middle-truncation |
| `FULL` / `HALFG` / `EMPTY` | the three Nerd Font heart glyphs |

### Finding heart glyphs for your font

Nerd Fonts remap icons to font-specific codepoints, so the "right" heart
codepoint varies by font/version. Read your actual font's cmap by glyph name:

```python
from fontTools.ttLib import TTFont
f = TTFont("/path/to/YourNerdFont.ttf")
for cp, name in sorted(f.getBestCmap().items()):
    if "heart" in name.lower():
        print(f"U+{cp:05X}  {name}")
```

## How context usage is measured

The script reads the session transcript (`transcript_path` from the stdin JSON),
finds the latest main-chain message, and sums
`input_tokens + cache_read_input_tokens + cache_creation_input_tokens` — the full
prompt size, i.e. current context occupancy — then divides by the model's window.

## License

MIT
