#!/usr/bin/env python3
"""
exercise_playbook.py — Sparsh's movement memory (the food pantry, for exercises).

THE PROBLEM (measured, not guessed): the muscle card maps each logged exercise to
muscle groups via muscle_volume.MAP — an EXACT, normalized-name dict. But the agent
phrases names differently every session ("Triceps Pushdown" one day, "Tricep Pushdown
(barbell)" the next, "Single-Arm Triceps Pushdown" the next). Across the live logs,
~half of all distinct exercise names miss the MAP → their volume silently vanishes from
the card (triceps/shoulders/legs read as undertrained when they weren't).

THE FIX (same philosophy as food_pantry.py): resolve a logged name through layers
instead of demanding an exact key —

  1. exact   — normalized hit against the base MAP ∪ a learned overlay (no regression).
  2. fuzzy   — pantry-style token overlap (Jaccard + containment ≥ 0.6); catches the
               superset variants ("pec deck fly" → "pec deck", "shoulder press machine"
               → "shoulder press", "seated row" → "diverging seated row").
  3. keyword — the muscle is usually IN the name; an ordered, conservative rule list
               ("pushdown"/"tricep" → Triceps, "leg press" → Quads, "pulldown" → Lats).
               Ordered so conflicts resolve right (forearm/wrist BEFORE generic curl,
               "leg curl" → Hamstrings BEFORE "curl" → Biceps, rear-delt BEFORE fly).
  4. untracked — cardio / core / forearm ("hike", "sport", "crunch", "wrist") resolve
               to NO tracked muscle and are NOT flagged (they train nothing we score).
  5. None    — genuinely novel → the caller flags ⚠️ and the agent teach()es it once.

Why an OVERLAY and not a rewrite of MAP: MAP stays the canonical base (its muscle names
must match muscle_volume.LANDMARKS/ORDER). learned.json holds only what the playbook is
taught beyond MAP — human-editable, co-located with the workout files, grows from use.

Why a keyword layer on top of plain fuzzy (the one place this differs from food_pantry):
food brand names repeat verbatim, so containment alone catches them. Exercise names hide
the muscle behind the equipment, so "triceps pushdown" and "rope pushdown" share only one
token (0.45 < 0.6) — fuzzy can't bridge them, but the muscle is right there in the word.

Stdlib only → runs on the system python AND the fitness venv. lookup() is READ-ONLY
(pure resolution, no side effects); the overlay changes only via teach()/remember().

  exercise_playbook.py resolve --name "Triceps Pushdown" --name "Hike" ...  # JSON hits/misses
  exercise_playbook.py lookup "shoulder press machine"                       # JSON one
  exercise_playbook.py teach --name "Jefferson Curl" --primary "Hamstrings" --secondary "Mid-Back"
  exercise_playbook.py list                                                  # human dump
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
from pathlib import Path


def _default_vault() -> Path:
    """Same contract as muscle_volume/food_pantry: HERMES_VAULT wins; else the VPS
    path if it exists (production); else the Mac dev path — read/write never split-brain."""
    env = os.environ.get("HERMES_VAULT")
    if env:
        return Path(env)
    vps = Path("/home/hermes/vault")
    if vps.exists():
        return vps
    return Path.home() / "Documents" / "School Vault - UofT"


VAULT = _default_vault()
PLAYBOOK = VAULT / "07 - Health" / "Workouts" / "exercise_playbook.json"
FUZZY_THRESHOLD = 0.6  # token-overlap needed to count two exercise names as the same movement


# ── anatomical keyword rules (ORDERED — first substring hit wins) ──────────────
# Each entry: (patterns, primary[], secondary[]). Empty muscle lists = "trains
# nothing we track" (cardio/core/forearm) → resolved (NOT flagged), adds 0 volume.
# Order encodes precedence: the specific/over-eager-conflicting rules come FIRST so a
# later, broader rule can't steal them (e.g. "forearm curl" must hit forearm-untracked
# before the generic "curl" → Biceps rule; "leg curl" before "curl"; "rear delt fly"
# before "fly"). Muscle names MUST match muscle_volume.LANDMARKS exactly.
KEYWORD_RULES: list[tuple[list[str], list[str], list[str]]] = [
    # 1. cardio / conditioning → untracked (note: "rowing"/"erg" here, NOT bare "row")
    (["treadmill", "elliptical", "stairmaster", "stair master", "cycling", "spin bike",
      "jog", "jogging", "running", "hike", "hiking", "sport", "swim", "walk", "cardio",
      "rowing machine", "row erg", " erg"], [], []),
    # 2. forearm / wrist → untracked (we don't score forearms)
    (["wrist", "forearm"], [], []),
    # 3. core / abs → untracked
    (["crunch", "sit up", "situp", "sit-up", "plank", "leg raise", "knee raise",
      "hanging", "oblique", "ab machine", "ab wheel", "russian twist", "toes to bar",
      "l-sit", "l sit", "dead bug", "pallof"], [], []),
    # 4. rear delts (before "fly")
    (["rear delt", "reverse fly", "reverse pec", "reverse peck", "face pull"],
     ["Rear Delts"], []),
    # 5. side delts
    (["lateral raise", "side raise", "side lateral", "lateral machine", "upright row"],
     ["Side Delts"], []),
    # 6. front delts (isolation)
    (["front raise"], ["Front Delts"], []),
    # 7. overhead / shoulder press
    (["shoulder press", "overhead press", "military press", "ohp", "arnold press",
      "strict press", "z press"], ["Front Delts"], ["Side Delts", "Triceps"]),
    # 8. shrugs
    (["shrug"], ["Mid-Back"], ["Rear Delts"]),
    # 9. legs / glutes / calves (specific phrases before bare tokens)
    (["leg press"], ["Quads"], ["Glutes", "Hamstrings"]),
    (["leg extension", "knee extension"], ["Quads"], []),
    (["leg curl", "hamstring curl", "lying curl", "nordic"], ["Hamstrings"], []),
    (["squat", "hack squat", "lunge", "split squat", "bulgarian", "step up", "step-up",
      "leg day"], ["Quads"], ["Glutes", "Hamstrings"]),
    (["hip thrust", "glute bridge", "kickback", "glute"], ["Glutes"], ["Hamstrings"]),
    (["deadlift", "rdl", "romanian", "good morning", "hip hinge"],
     ["Hamstrings"], ["Glutes", "Mid-Back"]),
    (["calf", "calves", "soleus"], ["Calves"], []),
    # 10. vertical pulls
    (["pulldown", "pull down", "pull-down", "pull up", "pull-up", "pullup", "chin up",
      "chin-up", "chinup", "lat prayer"], ["Lats"], ["Biceps"]),
    (["pullover"], ["Lats"], ["Chest"]),
    # 11. horizontal pulls (after pulldown/cardio-rowing so only strength rows land here)
    (["row"], ["Lats", "Mid-Back"], ["Rear Delts", "Biceps"]),
    # 12. horizontal press / dips / pushups
    (["bench press", "chest press", "incline press", "decline press", "floor press",
      "smith press", "machine press"], ["Chest"], ["Front Delts", "Triceps"]),
    (["dip", "dips"], ["Chest"], ["Triceps", "Front Delts"]),
    (["push up", "push-up", "pushup"], ["Chest"], ["Front Delts", "Triceps"]),
    # 13. chest isolation
    (["fly", "flye", "pec deck", "peck deck", "pec fly", "chest fly", "cable cross",
      "crossover"], ["Chest"], ["Front Delts"]),
    # 14. triceps (before the generic "curl"→biceps rule)
    (["tricep", "triceps", "pushdown", "push down", "push-down", "pressdown",
      "skull crush", "skullcrusher", "overhead extension", "french press",
      "tate press", "kickback tri"], ["Triceps"], []),
    # 15. biceps (LAST — generic "curl" only reaches here after leg/wrist/forearm curls
    #     and tricep movements have been claimed above)
    (["bicep", "biceps", "curl", "preacher", "hammer", "concentration"],
     ["Biceps"], []),
]


def _norm(s: str) -> str:
    """Lowercase, punctuation → space, collapse whitespace. Keeps every word (no
    singularizing) so 'tricep'/'triceps' both retain the keyword stem, and parenthetical
    variants ('(single-arm)') become matchable tokens ('single arm')."""
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _score(a: str, b: str) -> float:
    """Token-overlap of two normalized names — pantry's _score, verbatim. Blends Jaccard
    with a containment term (weighted just below exact) so a longer name still matches the
    shorter movement it's built on ('cable lateral raise' → 'lateral raise')."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    inter = sa & sb
    if not inter:
        return 0.0
    jac = len(inter) / len(sa | sb)
    cont = len(inter) / min(len(sa), len(sb))
    return max(jac, cont * 0.9)


def _base() -> dict[str, tuple[list[str], list[str]]]:
    """The canonical exercise→muscle base. Lazy-imported from muscle_volume so this
    module has no import-time dependency on it (muscle_volume imports US for lookup —
    lazy here breaks the cycle). Keys are already normalized lowercase."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from muscle_volume import MAP  # noqa: PLC0415
        return MAP
    except Exception:  # noqa: BLE001 — if the base can't load, fuzzy/keyword still work
        return {}


def _load_learned() -> dict:
    try:
        return json.loads(PLAYBOOK.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"items": {}}


def _save_learned(data: dict) -> None:
    PLAYBOOK.parent.mkdir(parents=True, exist_ok=True)
    PLAYBOOK.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _merged() -> dict[str, tuple[list[str], list[str]]]:
    """normkey → (primary, secondary), learned overlay winning over the base on collision.
    Base MAP keys are re-normalized with OUR _norm (which strips punctuation) so a
    hyphenated key like 'single-arm low pushdown' exact-matches a logged 'Single-Arm Low
    Pushdown' instead of falling through to fuzzy/keyword."""
    out: dict[str, tuple[list[str], list[str]]] = {}
    for k, (prim, sec) in _base().items():
        out[_norm(k)] = (list(prim), list(sec))
    for k, e in _load_learned().get("items", {}).items():
        out[_norm(k)] = (list(e.get("primary", [])), list(e.get("secondary", [])))
    return out


def _result(name: str, prim: list[str], sec: list[str], match: str, score: float) -> dict:
    return {"input": name, "primary": list(prim), "secondary": list(sec),
            "match": match, "score": round(score, 2),
            "tracked": bool(prim or sec)}


def lookup(name: str) -> dict | None:
    """Resolve one exercise name to its muscle groups. READ-ONLY — no side effects.
    Returns {primary, secondary, match, tracked} or None for a genuinely novel movement
    the caller should flag (and the agent should teach()). A result with empty primary
    AND secondary (tracked=False) means 'recognized, trains nothing we score' (cardio/
    core/forearm) — resolved, NOT a gap."""
    key = _norm(name)
    if not key:
        return None
    merged = _merged()
    # 1. exact
    if key in merged:
        prim, sec = merged[key]
        return _result(name, prim, sec, "exact", 1.0)
    # 2. fuzzy — pick the best score, tie-broken by MOST shared tokens (the most
    #    specific known movement: 'cable rear delt fly' → 'rear delt fly', not 'cable
    #    fly'), then by sorted key for full determinism.
    qtok = set(key.split())
    best_k, best_rank = None, (FUZZY_THRESHOLD - 1e-9, -1)
    for k in sorted(merged):
        s = _score(key, k)
        if s < FUZZY_THRESHOLD:
            continue
        rank = (s, len(qtok & set(k.split())))
        if rank > best_rank:
            best_k, best_rank = k, rank
    if best_k is not None:
        prim, sec = merged[best_k]
        return _result(name, prim, sec, "fuzzy", best_rank[0])
    # 3 + 4. anatomical keyword / untracked (ordered; first substring hit wins)
    for patterns, prim, sec in KEYWORD_RULES:
        if any(p in key for p in patterns):
            return _result(name, prim, sec, "keyword" if (prim or sec) else "untracked", 0.0)
    # 5. genuinely novel
    return None


def teach(name: str, primary: list[str], secondary: list[str] | None = None,
          source: str = "taught", today: str | None = None) -> str:
    """Add/overwrite a learned mapping for a movement the resolver couldn't place (the
    'append truly new' path). Stored normalized; bumps a uses counter. Returns the key."""
    key = _norm(name)
    if not key:
        return ""
    today = today or datetime.date.today().isoformat()
    data = _load_learned()
    items = data.setdefault("items", {})
    prev = items.get(key, {})
    items[key] = {
        "name": prev.get("name", name.strip()),
        "primary": list(primary or []),
        "secondary": list(secondary or []),
        "source": source or prev.get("source", "taught"),
        "uses": prev.get("uses", 0) + 1,
        "first": prev.get("first", today), "last": today,
    }
    _save_learned(data)
    return key


# remember() is an alias kept for symmetry with food_pantry.remember (same self-fill role).
def remember(name: str, primary: list[str], secondary: list[str] | None = None,
             source: str = "logged", today: str | None = None) -> str:
    return teach(name, primary, secondary, source=source, today=today)


def resolve(names: list[str]) -> dict:
    """Batch: split exercise names into resolved (with muscles) vs misses (novel → teach)."""
    hits, misses = [], []
    for nm in names:
        h = lookup(nm)
        (hits if h is not None else misses).append(h if h is not None else nm)
    return {"hits": hits, "misses": misses,
            "summary": f"{len(hits)} resolved, {len(misses)} novel (teach these)"}


# ----------------------------------------------------------------- cli
def main() -> int:
    ap = argparse.ArgumentParser(prog="exercise_playbook")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="split names into resolved vs novel (JSON)")
    r.add_argument("--name", action="append", default=[])
    lk = sub.add_parser("lookup", help="resolve one exercise (JSON)")
    lk.add_argument("query")
    t = sub.add_parser("teach", help="learn a novel movement's muscles")
    t.add_argument("--name", required=True)
    t.add_argument("--primary", required=True, help="comma-separated muscle groups")
    t.add_argument("--secondary", default="", help="comma-separated (optional)")
    sub.add_parser("list", help="dump the learned overlay, most-used first")
    a = ap.parse_args()

    def _split(s: str) -> list[str]:
        return [x.strip() for x in s.split(",") if x.strip()]

    if a.cmd == "resolve":
        print(json.dumps(resolve(a.name), ensure_ascii=False))
    elif a.cmd == "lookup":
        print(json.dumps(lookup(a.query) or {"hit": False, "input": a.query}, ensure_ascii=False))
    elif a.cmd == "teach":
        key = teach(a.name, _split(a.primary), _split(a.secondary))
        print(json.dumps({"ok": bool(key), "key": key,
                          "primary": _split(a.primary), "secondary": _split(a.secondary)}))
    elif a.cmd == "list":
        items = _load_learned().get("items", {})
        for k, e in sorted(items.items(), key=lambda kv: -kv[1].get("uses", 0)):
            sec = (" + " + ", ".join(e.get("secondary", []))) if e.get("secondary") else ""
            prim = ", ".join(e.get("primary", [])) or "(untracked)"
            print(f"{e.get('uses', 0):>3}× {e['name']}: {prim}{sec} [{e.get('source', '')}]")
        if not items:
            print("(learned overlay empty — all resolution via base MAP + fuzzy + keyword)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
