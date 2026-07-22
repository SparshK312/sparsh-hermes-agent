"""vault-write-guard — two tiers of protection on agent writes to the vault.

TIER 1 — HARD BLOCK (correctness): health logging must go through vault_log.py.
The log-* skills say "never hand-edit the vault" — but that's prose the model can
ignore (Claude hand-patched the daily note 18× in one session, landing food on the
wrong day + corrupting totals). Food Log/, Workouts/, and Daily-Note health fields
are vault_log's exclusive domain → blocked outright, with a pointer to the writer.

TIER 2 — ASK PERMISSION (consent): destructive rewrites of high-value docs.
On 2026-07-22 a "just analyze these notes" turn escalated to Sonnet and then
*rewrote* `00 - Dashboard/Action Items.md` (deleting four detailed Peru items) and
*replaced* a hand-written final draft in `06 - Internships/`. Nothing stopped it —
the guard only covered health files. Now: an edit to those dirs that NET-REMOVES a
meaningful amount of existing content returns {"action": "approve"}, which routes a
confirmation prompt to Telegram before it lands. Sparsh can approve a genuine
rewrite; nothing gets silently destroyed.

Deliberately narrow + fail-open so it can NEVER break logging or normal work:
  - Only WRITE tools are guarded (patch/write_file/execute_code). Reads, `terminal`
    (how vault_log itself runs), and every other tool always pass.
  - ADDITIVE edits pass silently — appending a task, ticking a checkbox, or any
    patch that doesn't net-remove much never prompts. Only real deletions ask.
  - Health dirs and Daily Notes stay on Tier 1 semantics (Daily Notes writes are
    still allowed for the 6:50am prefill cron; Coach Memory stays cron-writable).
  - Any error in the decision → ALLOW (fail open). A guard bug must not block work.
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

# Tier 2: high-value docs where a destructive rewrite must be confirmed first.
# Scoped to dirs no cron destructively rewrites (the daily-note prefill writes
# 04 - Daily Notes/, coach-refresh rewrites 07 - Health/Coach Memory.md — both
# stay out of this tier so their crons keep working under approvals.cron_mode=deny).
_APPROVE_DIRS = ("00 - Dashboard/", "06 - Internships/")
# Net characters removed before an edit counts as "destructive". Appends and
# checkbox flips are well under this; wholesale section rewrites are well over.
_DESTRUCTIVE_NET_CHARS = 200

_MSG = (
    "🚫 vault-write-guard: don't hand-edit health files (it corrupts totals and lands on "
    "the wrong day). Use the deterministic writer via `terminal` instead:\n"
    "  /usr/bin/python3 /home/hermes/.hermes/scripts/vault/vault_log.py "
    "<food|water|weight|sleep|vitamins|workout> [--date YYYY-MM-DD] ...\n"
    "Corrections: run `vault_log.py undo-last-meal` then re-log the corrected version. "
    "vault_log owns all writes to Food Log, Workouts, and the Daily-Note health fields."
)


def _extract_path(args: dict) -> str:
    for k in ("file_path", "path", "file", "filename", "target_file", "target"):
        v = args.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def decide(tool_name, args) -> str | None:
    """Return a block message if this tool call is a health-file hand-write, else None.
    Pure + side-effect-free so it can be unit-tested."""
    if tool_name not in _BLOCKED_TOOLS:
        return None
    args = args if isinstance(args, dict) else {}
    path = _extract_path(args)
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


def needs_approval(tool_name, args) -> str | None:
    """Return an approval-request message if this is a DESTRUCTIVE edit to a high-value
    doc (dashboards / internships), else None. Additive edits return None (pass silently).
    Pure + side-effect-free so it can be unit-tested."""
    if tool_name not in _BLOCKED_TOOLS:
        return None
    args = args if isinstance(args, dict) else {}
    path = _extract_path(args)

    # Match on the real path when we have one; fall back to the arg blob only for
    # execute_code (which carries no path) so we don't over-trigger on mere mentions.
    if path:
        in_protected = any(d.lower() in path.lower() for d in _APPROVE_DIRS)
    else:
        blob = json.dumps(args, default=str).lower()
        in_protected = any(d.lower() in blob for d in _APPROVE_DIRS)
    if not in_protected:
        return None

    target = path or "a protected vault doc"

    if tool_name == "patch":
        old = args.get("old_string") or ""
        new = args.get("new_string") or ""
        removed = len(old) - len(new)
        if removed < _DESTRUCTIVE_NET_CHARS:
            return None  # additive / small edit — append, tick a box, fix a typo
        return (
            f"✋ Destructive edit to {target}: this patch removes ~{removed} characters of "
            "existing content (a rewrite, not an append). Approve only if you actually want "
            "that content replaced — otherwise deny and append the new material instead. "
            "(Guard added after the 2026-07-22 incident where four Peru action items and a "
            "hand-written draft were silently overwritten.)"
        )

    # write_file replaces the whole file; execute_code is opaque. Both need a yes.
    verb = "overwrite the entire file" if tool_name == "write_file" else "run opaque code against"
    return (
        f"✋ This will {verb} {target}. That destroys whatever is currently there. "
        "Approve only if you intend a full replacement."
    )


def _guard(tool_name=None, args=None, **kwargs):
    """pre_tool_call hook: hard-block health hand-writes, ask permission before a
    destructive rewrite of a high-value doc (fail open on any error)."""
    try:
        msg = decide(tool_name, args)
        if msg:
            return {"action": "block", "message": msg}
        ask = needs_approval(tool_name, args)
        if ask:
            # No rule_key on purpose: every destructive edit asks, so a single "yes"
            # never becomes a standing permission to overwrite this dir.
            return {"action": "approve", "message": ask}
    except Exception:  # noqa: BLE001 — never let a guard bug block real work
        return None
    return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _guard)
