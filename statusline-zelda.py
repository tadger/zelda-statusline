#!/usr/bin/env python3
"""Zelda-style status line for Claude Code.

Shows: git branch | model + effort | context as draining heart containers | cost.

Claude Code pipes a JSON blob on stdin (model, workspace, transcript_path, cost…).
Hearts = remaining context "health": you start full and lose hearts as the
context fills. The number is context *used* %. Window auto-detects from the
model id (1M for [1m] models, 200k otherwise).

Optional config: ~/.config/zelda-statusline/config.json (or $ZELDA_STATUSLINE_CONFIG).
See README for the schema — hearts count, segment visibility, and colors.
"""
import json
import os
import re
import subprocess
import sys

# ── ANSI ────────────────────────────────────────────────────────────────────
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"


def _esc(param):
    """Build an SGR escape from a parameter string, e.g. '91' or '38;5;141'."""
    return f"\033[{param}m"


# Defaults — every one is overridable via the JSON config (see README).
DEFAULT_COLORS = {
    "red": "91",          # full/half heart (bright red)
    "grey": "90",         # empty heart container
    "branch": "36",       # git branch (cyan)
    "model": "38;5;141",  # model name (purple)
    "effort": "38;5;211",  # effort (pink)
    "caution": "33",      # caution percentage (yellow)
    "cost": "38;5;42",    # session cost (bluish green)
}
DEFAULT_RAINBOW = ["91", "93", "92", "96", "94", "95"]  # max-effort shimmer
DEFAULT_SHOW = {
    "branch": True, "model": True, "effort": True,
    "hearts": True, "percent": True, "cost": True,
}
DEFAULT_ORDER = ["branch", "model", "hearts", "percent", "cost"]
DEFAULT_SEPARATOR = "  "   # literal string placed between adjacent items
KNOWN_ITEMS = ("branch", "model", "hearts", "percent", "cost")
_BREAK = object()                          # sentinel: force a line break
ORDER_TOKENS = KNOWN_ITEMS + ("newline",)  # valid entries in "order"

# Live globals — seeded with defaults, replaced by configure() at runtime.
RED = _esc(DEFAULT_COLORS["red"])
GREY = _esc(DEFAULT_COLORS["grey"])
CYAN = _esc(DEFAULT_COLORS["branch"])
PURPLE = _esc(DEFAULT_COLORS["model"])
PINK = _esc(DEFAULT_COLORS["effort"])
YELLOW = _esc(DEFAULT_COLORS["caution"])
COST = _esc(DEFAULT_COLORS["cost"])
RAINBOW = [_esc(p) for p in DEFAULT_RAINBOW]
SHOW = dict(DEFAULT_SHOW)
ORDER = list(DEFAULT_ORDER)
SEPARATOR = DEFAULT_SEPARATOR
HEARTS = 10          # number of heart containers (config: "hearts")

# Source-only tunables (not part of the JSON config)
LOW_HEALTH = 0.20    # danger zone — bold-red percentage
CAUTION = 0.40       # caution zone — yellow percentage
MAX_BRANCH = 24      # branch-name cap; longer names middle-truncated with …

# Nerd Font glyphs (require a patched Nerd Font in the terminal)
FULL = "\U000F02D1"        # nf heart (U+F02D1)
HALFG = "\U000F06DE"       # nf heart-half-full (U+F06DE)
EMPTY = "\U000F02D5"       # nf heart-outline (U+F02D5)
MODEL_ICON = "\U000F04E5"  # nf-md-sword (U+F04E5) — model prefix
COST_ICON = "\uF219"       # nf-fa-gem (U+F219) — cost prefix


def read_stdin():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def read_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {}


def configure():
    """Load the optional JSON config and override colors / hearts / visibility."""
    global RED, GREY, CYAN, PURPLE, PINK, YELLOW, COST, RAINBOW, HEARTS, SHOW
    global ORDER, SEPARATOR
    path = (os.environ.get("ZELDA_STATUSLINE_CONFIG")
            or os.path.expanduser("~/.config/zelda-statusline/config.json"))
    cfg = read_json(path)
    if not cfg:
        return

    colors = cfg.get("colors") or {}
    RED = _esc(colors.get("red", DEFAULT_COLORS["red"]))
    GREY = _esc(colors.get("grey", DEFAULT_COLORS["grey"]))
    CYAN = _esc(colors.get("branch", DEFAULT_COLORS["branch"]))
    PURPLE = _esc(colors.get("model", DEFAULT_COLORS["model"]))
    PINK = _esc(colors.get("effort", DEFAULT_COLORS["effort"]))
    YELLOW = _esc(colors.get("caution", DEFAULT_COLORS["caution"]))
    COST = _esc(colors.get("cost", DEFAULT_COLORS["cost"]))

    if cfg.get("rainbow"):
        RAINBOW = [_esc(p) for p in cfg["rainbow"]]

    if "hearts" in cfg:
        try:
            HEARTS = max(1, int(cfg["hearts"]))
        except (ValueError, TypeError):
            pass

    SHOW = {**DEFAULT_SHOW, **(cfg.get("show") or {})}

    if isinstance(cfg.get("separator"), str):
        SEPARATOR = cfg["separator"]

    order = cfg.get("order")
    if isinstance(order, list):
        filtered = [k for k in order if k in ORDER_TOKENS]
        if filtered:
            ORDER = filtered


def git_branch(cwd):
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=1,
        )
        if out.returncode != 0:
            return None
        branch = out.stdout.strip()
        if branch == "HEAD":  # detached
            sha = subprocess.run(
                ["git", "-C", cwd, "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=1,
            ).stdout.strip()
            return f"detached@{sha}" if sha else "detached"
        return branch or None
    except Exception:
        return None


def effort_level(cwd):
    """Last writer wins: user settings -> project -> project local."""
    level = None
    candidates = [
        os.path.expanduser("~/.claude/settings.json"),
        os.path.join(cwd, ".claude", "settings.json"),
        os.path.join(cwd, ".claude", "settings.local.json"),
    ]
    for p in candidates:
        val = read_json(p).get("effortLevel")
        if val:
            level = val
    return level


def context_tokens(transcript_path):
    """Latest main-chain message's full prompt size = current context occupancy."""
    if not transcript_path or not os.path.exists(transcript_path):
        return 0
    try:
        with open(transcript_path) as fh:
            lines = fh.readlines()
    except Exception:
        return 0
    for line in reversed(lines):
        line = line.strip()
        if not line or '"usage"' not in line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("isSidechain"):
            continue
        usage = (obj.get("message") or {}).get("usage")
        if not usage:
            continue
        return (
            usage.get("input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
        )
    return 0


def window_size(model_id):
    mid = (model_id or "").lower()
    if "1m" in mid or "[1m]" in mid:
        return 1_000_000
    return 200_000


def heart_bar(remaining_frac):
    # Three states per container (Zelda-style): full / half / empty.
    # The boundary heart becomes a "half" when the fractional part lands
    # in the middle third; otherwise it rounds to full or empty.
    exact = max(0.0, min(1.0, remaining_frac)) * HEARTS
    full = int(exact)              # whole filled hearts
    rem = exact - full             # leftover fraction on the boundary heart
    half = 0
    if rem >= 0.75:
        full += 1
    elif rem >= 0.25:
        half = 1
    full = min(full, HEARTS)
    empty = HEARTS - full - half

    cells = [f"{RED}{FULL}{RESET}"] * full
    cells += [f"{RED}{HALFG}{RESET}"] * half
    cells += [f"{GREY}{EMPTY}{RESET}"] * empty
    return " ".join(cells)


def percent_segment(remaining_frac):
    """Context-used percentage; color escalates as the context fills."""
    pct_used = round((1.0 - remaining_frac) * 100)
    if remaining_frac <= LOW_HEALTH:        # danger — context nearly full
        return f"{BOLD}{RED}{pct_used}%{RESET}"
    if remaining_frac <= CAUTION:           # caution
        return f"{YELLOW}{pct_used}%{RESET}"
    return f"{DIM}{pct_used}%{RESET}"       # healthy


def context_segment(remaining_frac, show_hearts=True, show_percent=True):
    """Hearts and/or percentage joined by a space (used by --demo)."""
    parts = []
    if show_hearts:
        parts.append(heart_bar(remaining_frac))
    if show_percent:
        parts.append(percent_segment(remaining_frac))
    return " ".join(parts)


def rainbow(text):
    """Color each visible character along the RAINBOW ramp (static gradient)."""
    out, i = [], 0
    for ch in text:
        if ch == " ":
            out.append(ch)
            continue
        out.append(f"{RAINBOW[i % len(RAINBOW)]}{ch}{RESET}")
        i += 1
    return "".join(out)


def model_segment(name, effort):
    """Model + effort, prefixed with the model icon. Rainbow at max effort."""
    label = f"{MODEL_ICON} {name}"
    if effort and effort.lower() == "max":
        return rainbow(f"{label}·{effort}")       # recreate the max-effort shimmer
    seg = f"{PURPLE}{label}{RESET}"
    if effort:
        seg += f"{DIM}·{RESET}{PINK}{effort}{RESET}"
    return seg


def truncate_middle(s, n):
    """Shorten s to n chars, eliding the middle with … (keeps head and tail)."""
    if len(s) <= n:
        return s
    if n <= 1:
        return "…"
    keep = n - 1               # leave room for the ellipsis
    head = (keep + 1) // 2     # head takes the extra char when keep is odd
    tail = keep - head
    return s[:head] + "…" + (s[-tail:] if tail else "")


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def vis_width(s):
    """Visible column width: strip ANSI; count PUA/Nerd glyphs as 2 columns."""
    w = 0
    for ch in _ANSI_RE.sub("", s):
        cp = ord(ch)
        if 0xE000 <= cp <= 0xF8FF or 0xF0000 <= cp <= 0xFFFFD:
            w += 2          # Nerd Font icons usually render double-width
        else:
            w += 1
    return w


def term_cols(default=120):
    """Terminal width via $COLUMNS (Claude Code sets it, v2.1.153+); wide if unset."""
    try:
        return int(os.environ.get("COLUMNS") or default)
    except ValueError:
        return default


def layout(segments):
    """Join SEPARATOR-delimited segments into rows.

    A _BREAK sentinel forces a new row. Otherwise segments pack greedily
    left-to-right, wrapping when the current row would exceed $COLUMNS. A
    segment wider than the terminal lands on its own row (unavoidable overflow).
    """
    cols = term_cols()
    sep_w = vis_width(SEPARATOR)
    lines, cur, cur_w = [], "", 0
    for seg in segments:
        if seg is _BREAK:                 # explicit line break
            if cur:
                lines.append(cur)
            cur, cur_w = "", 0
            continue
        sw = vis_width(seg)
        if not cur:
            cur, cur_w = seg, sw
        elif cur_w + sep_w + sw <= cols:  # fits on the current row
            cur += SEPARATOR + seg
            cur_w += sep_w + sw
        else:                             # too wide — wrap
            lines.append(cur)
            cur, cur_w = seg, sw
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def main():
    configure()
    data = read_stdin()
    cwd = data.get("cwd") or data.get("workspace", {}).get("current_dir") or os.getcwd()
    model = data.get("model", {})

    used = context_tokens(data.get("transcript_path"))
    window = window_size(model.get("id"))
    remaining = 1.0 - (min(1.0, used / window) if window else 0.0)

    # Render each item; collect into a dict keyed by item name.
    pieces = {}
    if SHOW["branch"]:
        branch = git_branch(cwd)
        if branch:
            branch = truncate_middle(branch, MAX_BRANCH)
        pieces["branch"] = (f"{CYAN}⎇ {branch}{RESET}" if branch
                            else f"{GREY}⎇ no-git{RESET}")
    if SHOW["model"]:
        name = model.get("display_name") or model.get("id") or "model"
        effort = effort_level(cwd) if SHOW["effort"] else None
        pieces["model"] = model_segment(name, effort)
    if SHOW["hearts"]:
        pieces["hearts"] = heart_bar(remaining)
    if SHOW["percent"]:
        pieces["percent"] = percent_segment(remaining)
    if SHOW["cost"]:
        cost = (data.get("cost") or {}).get("total_cost_usd")
        if cost is not None:
            pieces["cost"] = f"{COST}{COST_ICON} ${cost:.2f}{RESET}"

    # Emit in the configured order; "newline" forces a row break.
    segments = []
    for k in ORDER:
        if k == "newline":
            segments.append(_BREAK)
        elif pieces.get(k):
            segments.append(pieces[k])
    print(layout(segments))


def demo():
    configure()
    print("Zelda status line — color & health preview:\n")
    print(f"  high effort:  {model_segment('Opus 4.8', 'high')}   (model + effort)")
    print(f"  MAX  effort:  {model_segment('Opus 4.8', 'max')}   (rainbow shimmer)\n")
    for used in (5, 45, 65, 82, 95):
        seg = context_segment(1.0 - used / 100)
        print(f"  {used:3}% used   {seg}  {COST}{COST_ICON} ${used * 0.01:.2f}{RESET}")
    print("\n(percentage: dim → yellow ≤40% health → bold-red ≤20% health)")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        main()
