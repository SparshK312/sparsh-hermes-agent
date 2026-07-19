"""vault-write-guard — force health logging through vault_log.py.

The log-* skills say "never hand-edit the vault, use vault_log.py" — but that's prose
the model can ignore (Claude hand-patched the daily note 18× in one session, landing
food on the wrong day + corrupting totals). This makes it a hard rule: BLOCK direct
`patch`/`write_file`/`execute_code` writes to the health files via the `pre_tool_call`
hook, and tell the agent to use vault_log instead.

Deliberately narrow + fail-open so it can NEVER break logging:
  - Only WRITE tools are guarded (patch/write_file/execute_code). Reads, `terminal`
    (how vault_log itself runs), and every other tool always pass.
  - Food Log/ and Workouts/ are vault_log's exclusive domain → always block hand-writes.
  - Daily Notes are blocked ONLY when the edit touches a health/macro field — so mood,
    energy, and `## Notes` body edits still work.
  - Any error in the decision → ALLOW (fail open). A guard bug must not block real work.
"""
from __future__ import annotations

import json

_BLOCKED_TOOLS = {"patch", "write_file", "execute_code"}
# Dirs that are 100% vault_log's domain — hand-writes always blocked.
_OWNED_DIRS = ("07 - Health/Food Log/", "07 - Health/Workouts/")
_DAILY_DIR = "04 - Daily Notes/"
# Health/macro markers — a Daily Notes write touching any of these = a logging hand-edit.
_HEALTH_KEYS = (
    "kcal:", "protein_g:", "carbs_g:", "fat_g:", "water_l:", "weight:",
    "sleep_hours:", "sleep_quality:", "vitamins_taken:", "supplements_today:",
    "lifted:", "total_kcal:", "total_protein_g:", "exercises:", "macros:",
)

_MSG = (
    "🚫 vault-write-guard: don't hand-edit health files (it corrupts totals and lands on "
    "the wrong day). Use the deterministic writer via `terminal` instead:\n"
    "  /usr/bin/python3 /home/hermes/.hermes/scripts/vault/vault_log.py "
    "<food|water|weight|sleep|vitamins|workout> [--date YYYY-MM-DD] ...\n"
    "Corrections: run `vault_log.py undo-last-meal` then re-log the corrected version. "
    "vault_log owns all writes to Food Log, Workouts, and the Daily-Note health fields."
)


def decide(tool_name, args) -> str | None:
    """Return a block message if this tool call is a health-file hand-write, else None.
    Pure + side-effect-free so it can be unit-tested."""
    if tool_name not in _BLOCKED_TOOLS:
        return None
    args = args if isinstance(args, dict) else {}
    path = ""
    for k in ("file_path", "path", "file", "filename", "target_file", "target"):
        v = args.get(k)
        if isinstance(v, str) and v:
            path = v
            break
    # Everything stringy in the args (path + old/new_string + content + code) — for
    # execute_code (no path arg) and for inspecting WHAT a daily-note edit touches.
    blob = json.dumps(args, default=str).lower()

    # Food Log + Workouts are vault_log's exclusive domain (it script-creates them, not
    # via an agent tool) → block ALL guarded hand-writes there.
    in_owned = any(d.lower() in path.lower() for d in _OWNED_DIRS) or \
        any(d.lower() in blob for d in _OWNED_DIRS)
    if in_owned:
        return _MSG

    # Daily Notes: block macro EDITS (patch/execute_code touching a health field) — that's
    # the hand-logging that corrupts totals. ALLOW write_file: the 6:50am prefill cron
    # CREATES the daily note (with empty health fields) via write_file, and vault_log
    # auto-creates it too; blocking creation would break the morning note + first log.
    in_daily = _DAILY_DIR.lower() in path.lower() or _DAILY_DIR.lower() in blob
    if in_daily and tool_name in ("patch", "execute_code") and any(key in blob for key in _HEALTH_KEYS):
        return _MSG

    return None


def _guard(tool_name=None, args=None, **kwargs):
    """pre_tool_call hook: block health-file hand-writes (fail open on any error)."""
    try:
        msg = decide(tool_name, args)
        if msg:
            return {"action": "block", "message": msg}
    except Exception:  # noqa: BLE001 — never let a guard bug block real work
        return None
    return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _guard)
