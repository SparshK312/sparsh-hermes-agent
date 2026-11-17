#!/usr/bin/env python3
"""
upstream_watch.py — tell him, once, when the framework fix the upgrade is waiting for ships.

The Hermes framework upgrade (v0.20.5 -> v0.23.x) is held on one upstream bug: on v0.23.1 a
Telegram photo sent WITH a caption reaches the agent as text only, which breaks meal-photo
logging. Fix: NousResearch/hermes-agent PR #156233 (issue #155902). Nobody should have to
check GitHub by hand, so this runs once a day and sends ONE Telegram message per stage:

  merged     the fix PR was merged (heads-up; not installable from a stable release yet)
  released   a STABLE release contains the merge commit (the upgrade can be scheduled)

No model call. Public GitHub API, unauthenticated (a few requests a day). A stage is only
marked as announced after the message was actually sent. Any API failure = no state change
and a log line; three failures in a row are announced once (a watcher that silently stops
looking is the failure this exists to prevent).

  upstream_watch.py [--dry-run]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = "NousResearch/hermes-agent"
FIX_PR = 156233
INSTALLED = "v0.20.5"
STATE = Path(os.environ.get("UPSTREAM_WATCH_STATE", str(Path.home() / ".hermes" / "upstream_watch_state.json")))
HERMES = Path.home() / ".local" / "bin" / "hermes"
API = "https://api.github.com/repos/" + REPO
FAILS_BEFORE_ALERT = 3


def log(msg: str) -> None:
    print(f"[upstream-watch] {msg}", file=sys.stderr)


def get(path: str):
    req = urllib.request.Request(API + path, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": "upstream-watch"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def check(fetch=get) -> dict:
    """{"merged": bool, "merge_sha": str, "release": tag-or-"" }. Raises on any API problem."""
    pr = fetch(f"/pulls/{FIX_PR}")
    merged = bool(pr.get("merged"))
    sha = pr.get("merge_commit_sha") or ""
    out = {"merged": merged, "merge_sha": sha if merged else "", "release": ""}
    if not merged:
        return out
    rels = fetch("/releases?per_page=30")
    for rel in rels:                                   # newest first
        if rel.get("prerelease") or rel.get("draft"):
            continue
        tag = rel["tag_name"]
        cmp_ = fetch(f"/compare/{tag}...{sha}")        # is the fix commit an ancestor of the tag?
        if cmp_.get("status") in ("behind", "identical"):
            out["release"] = tag                       # keep going: the OLDEST stable containing it is not needed,
            break                                      # the newest one that has it is the upgrade target
    return out


def send(subject: str, body: str) -> bool:
    try:
        subprocess.run([str(HERMES), "send", "-t", "telegram", "-q", "-s", subject, body], check=True, timeout=30)
        return True
    except Exception as e:  # noqa: BLE001
        log(f"send failed: {e}")
        return False


def load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:  # noqa: BLE001
        return {"announced": [], "fails": 0, "fail_announced": False}


def save(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name(STATE.name + ".tmp")
    tmp.write_text(json.dumps(st))
    os.replace(tmp, STATE)


def run(fetch=get, sender=send, dry_run: bool = False) -> dict:
    st = load()
    try:
        res = check(fetch)
    except Exception as e:  # noqa: BLE001
        st["fails"] = st.get("fails", 0) + 1
        log(f"check failed ({st['fails']} in a row): {type(e).__name__}: {e}")
        if st["fails"] >= FAILS_BEFORE_ALERT and not st.get("fail_announced") and not dry_run:
            if sender("⚠️ Upstream watcher cannot reach GitHub",
                      f"The daily check for the Hermes photo-caption fix has failed {st['fails']} times in a row "
                      f"({type(e).__name__}). It will keep trying; nothing else is affected."):
                st["fail_announced"] = True
        if not dry_run:
            save(st)
        return {"error": str(e), **st}
    st["fails"], st["fail_announced"] = 0, False
    stage = "released" if res["release"] else ("merged" if res["merged"] else "")
    log(json.dumps(res) + f" stage={stage or 'open'} announced={st.get('announced')}")
    if stage and stage not in st.get("announced", []) and not dry_run:
        if stage == "released":
            ok = sender("✅ Hermes upgrade is unblocked",
                        f"Stable release {res['release']} of the Hermes framework includes the fix for photos "
                        f"with captions (PR #{FIX_PR}). You are on {INSTALLED}. The upgrade can be scheduled now; "
                        f"the step-by-step plan is in the vault (Hermes Audit & Rework Plan, section 3b).")
        else:
            ok = sender("ℹ️ Hermes photo-caption fix merged upstream",
                        f"PR #{FIX_PR} was merged. It is not in a stable release yet, so nothing to do: "
                        f"I'll tell you when one ships.")
        if ok:
            st.setdefault("announced", []).append(stage)
    if not dry_run:
        save(st)
    return {**res, "stage": stage, "announced": st.get("announced", [])}


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
