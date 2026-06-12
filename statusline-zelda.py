#!/usr/bin/env python3
"""Zelda-style status line for Claude Code.

Shows: git branch | model + effort | context as draining heart containers.

Claude Code pipes a JSON blob on stdin (model, workspace, transcript_path, ...).
Hearts = remaining context "health": you start full and lose hearts as the
context fills. The number is context *used* %. Window auto-detects from the
model id (1M for [1m] models, 200k otherwise).
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
RED = "\033[91m"           # full/half heart (bright red — pink in this theme)
GREY = "\033[90m"          # empty heart container
CYAN = "\033[36m"          # branch
PURPLE = "\033[38;5;141m"  # model (256-color purple)
PINK = "\033[38;5;211m"    # effort (256-color pink)
YELLOW = "\033[33m"        # caution percentage
COST = "\033[38;5;42m"     # session cost in USD (green with a touch of blue)
# Rainbow ramp for max-effort (bright 16-color hues — render anywhere, no 256 dep)
RAINBOW = ["\033[91m", "\033[93m", "\033[92m", "\033[96m", "\033[94m", "\033[95m"]

# Health thresholds (fraction of context remaining)
LOW_HEALTH = 0.20     # danger zone — bold-red percentage
CAUTION = 0.40        # caution zone — yellow percentage

HEARTS = 10          # number of containers
MAX_BRANCH = 24      # branch-name cap; longer names are middle-truncated with …
# Nerd Font heart glyphs (require a patched Nerd Font in the terminal).
FULL = "\U000F02D1"   # nf full heart  (U+F02D1, Material Design)
HALFG = "\U000F06DE"  # nf heart-half-full — a real half-filled heart (MDI family)
EMPTY = "\U000F02D5"  # nf heart-outline — empty container (same MDI family)


def read_stdin():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


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


def read_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {}


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


def context_segment(remaining_frac):
    """Heart bar + percentage; the percentage color escalates as context fills."""
    pct_used = round((1.0 - remaining_frac) * 100)
    bar = heart_bar(remaining_frac)
    if remaining_frac <= LOW_HEALTH:            # danger — context nearly full
        pct = f"{BOLD}{RED}{pct_used}%{RESET}"
    elif remaining_frac <= CAUTION:             # caution
        pct = f"{YELLOW}{pct_used}%{RESET}"
    else:                                       # healthy
        pct = f"{DIM}{pct_used}%{RESET}"
    return f"{bar} {pct}"


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
    """Model + effort. At max effort the whole thing gets the rainbow treatment."""
    if effort and effort.lower() == "max":
        return rainbow(f"{name}·{effort}")        # recreate the max-effort shimmer
    seg = f"{PURPLE}{name}{RESET}"
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


def layout(branch_seg, model_seg, ctx_seg):
    """One line when it fits the terminal; otherwise stack onto multiple rows."""
    sep = f"  {DIM}│{RESET}  "
    cols = term_cols()
    one = f"{branch_seg}{sep}{model_seg}{sep}{ctx_seg}"
    if vis_width(one) <= cols:
        return one
    top = f"{branch_seg}{sep}{model_seg}"          # try branch+model / hearts split
    if vis_width(top) <= cols and vis_width(ctx_seg) <= cols:
        return f"{top}\n{ctx_seg}"
    return f"{branch_seg}\n{model_seg}\n{ctx_seg}"  # very narrow: one row each


def main():
    data = read_stdin()
    cwd = data.get("cwd") or data.get("workspace", {}).get("current_dir") or os.getcwd()

    # ── branch ──
    branch = git_branch(cwd)
    if branch:
        branch = truncate_middle(branch, MAX_BRANCH)
    branch_seg = f"{CYAN}⎇ {branch}{RESET}" if branch else f"{GREY}⎇ no-git{RESET}"

    # ── model + effort ──
    model = data.get("model", {})
    name = model.get("display_name") or model.get("id") or "model"
    effort = effort_level(cwd)
    model_seg = model_segment(name, effort)

    # ── context hearts ──
    used = context_tokens(data.get("transcript_path"))
    window = window_size(model.get("id"))
    used_frac = min(1.0, used / window) if window else 0.0
    ctx_seg = context_segment(1.0 - used_frac)

    # ── session cost in USD (follows the context segment) ──
    cost = (data.get("cost") or {}).get("total_cost_usd")
    if cost is not None:
        ctx_seg += f"  {COST}${cost:.2f}{RESET}"

    print(layout(branch_seg, model_seg, ctx_seg))


def demo():
    print("Zelda status line — color & health preview:\n")
    print(f"  high effort:  {model_segment('Opus 4.8', 'high')}   (purple model, pink effort)")
    print(f"  MAX  effort:  {model_segment('Opus 4.8', 'max')}   (rainbow shimmer)\n")
    for used in (5, 45, 65, 82, 95):
        seg = context_segment(1.0 - used / 100)
        print(f"  {used:3}% used   {seg}  {COST}${used * 0.01:.2f}{RESET}")
    print("\n(percentage: dim → yellow ≤40% health → bold-red ≤20% health)")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        main()
