#!/usr/bin/env python3
"""
food_pantry.py — Sparsh's food memory (the "RAG over what I've eaten", done right).

The log-food skill used to re-research every item every time (12 MCP lookups + web
searches for one plate of momos), which is slow AND inconsistent — the same fairlife
shake came back 160 kcal one day, 180 the next. This caches every item Sparsh has
ALREADY confirmed, keyed by a normalized food identity, and reuses HIS numbers on a
repeat. New items still get looked up — then remembered, so they're a hit next time.

Why a cache and not a vector DB: he eats a few hundred distinct things, not millions.
Normalized-name + fuzzy-token matching covers the repeats at zero infra/latency/cost,
stays deterministic, and is human-editable. A semantic layer can bolt on later if
plain matching ever misses repeats — it doesn't need to yet.

Macros are stored PER ONE UNIT of the item (per egg, per cup, per bottle, per bowl),
so "3 eggs" scales the stored "egg" entry ×3. Stdlib only → runs on system python.

  food_pantry.py resolve --item "2 eggs" --item "fairlife" ...   # JSON: hits + misses
  food_pantry.py lookup "lucky charms bowl"                       # JSON one item
  food_pantry.py list                                             # human dump
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
from pathlib import Path


def _default_vault() -> Path:
    env = os.environ.get("HERMES_VAULT")
    if env:
        return Path(env)
    vps = Path("/home/hermes/vault")
    if vps.exists():
        return vps
    return Path.home() / "Documents" / "School Vault - UofT"


VAULT = _default_vault()
PANTRY = VAULT / "07 - Health" / "Food Log" / "pantry.json"
FUZZY_THRESHOLD = 0.6  # token-overlap (Jaccard) needed to count as the same food

# leading-quantity units to strip so "1 cup rice" and "rice" share an identity
_UNITS = {
    "cup", "cups", "slice", "slices", "bowl", "bowls", "glass", "glasses", "can",
    "cans", "bottle", "bottles", "scoop", "scoops", "piece", "pieces", "serving",
    "servings", "g", "gram", "grams", "oz", "ounce", "ounces", "tbsp", "tsp", "ml",
    "l", "plate", "plates", "handful", "bar", "bars", "pack", "packs", "stick", "sticks",
}
_STOP = {"a", "an", "the", "of", "with", "some", "my"}


def norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)        # drop punctuation
    return re.sub(r"\s+", " ", s).strip()


def parse_item(raw: str) -> tuple[float, str, str]:
    """'2 eggs' -> (2.0, 'egg', '2 eggs'); '1 cup white rice' -> (1.0, 'white rice', ...).
    Returns (qty, identity_key, original_display)."""
    display = raw.strip()
    toks = norm(raw).split()
    qty = 1.0
    # leading count: a number, or 'a'/'an'
    if toks and re.fullmatch(r"\d+(\.\d+)?", toks[0]):
        qty = float(toks[0]); toks = toks[1:]
    elif toks and toks[0] in ("a", "an"):
        toks = toks[1:]
    # drop a leading unit word ("cup", "slice", "bottle"…) and filler stopwords
    if toks and toks[0] in _UNITS:
        toks = toks[1:]
    toks = [t for t in toks if t not in _STOP and t not in _UNITS]
    # singularize a trailing plural so "eggs"/"egg" share a key (naive but effective)
    if toks and toks[-1].endswith("s") and len(toks[-1]) > 3:
        toks[-1] = toks[-1][:-1]
    return qty, " ".join(toks), display


def _load() -> dict:
    try:
        return json.loads(PANTRY.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"items": {}}


def _save(data: dict) -> None:
    PANTRY.parent.mkdir(parents=True, exist_ok=True)
    PANTRY.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _score(a: str, b: str) -> float:
    """Similarity of two normalized food keys. Blends Jaccard with a containment
    term so brand/partial shorthand still matches ('fairlife' → 'fairlife core
    power', 'chicken bowl' → 'cafeteria chicken bowl'). Containment is weighted just
    below an exact match; the skill's confirm step is the safety net for the rare
    over-eager fuzzy hit ('egg' → 'egg salad')."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    inter = sa & sb
    if not inter:
        return 0.0
    jac = len(inter) / len(sa | sb)
    cont = len(inter) / min(len(sa), len(sb))   # 1.0 when one is a subset of the other
    return max(jac, cont * 0.9)


def _best_match(key: str, items: dict) -> tuple[str, float] | None:
    """Exact key wins (1.0); else the best-scoring entry above threshold, tie-broken
    by most-eaten (a repeat you log often beats a one-off near-match)."""
    if key in items:
        return key, 1.0
    best, best_score, best_uses = None, 0.0, -1
    for k, e in items.items():
        s = _score(key, k)
        uses = e.get("uses", 0)
        if s >= FUZZY_THRESHOLD and (s > best_score or (s == best_score and uses > best_uses)):
            best, best_score, best_uses = k, s, uses
    return (best, round(best_score, 2)) if best else None


def lookup(raw: str) -> dict | None:
    """Resolve one logged item against the pantry, scaling stored per-unit macros to
    the requested quantity. None on a miss."""
    qty, key, display = parse_item(raw)
    if not key:
        return None
    items = _load().get("items", {})
    m = _best_match(key, items)
    if not m:
        return None
    mk, score = m
    e = items[mk]
    return {
        "input": display, "matched": e["name"], "key": mk, "qty": qty,
        "match": "exact" if score == 1.0 else "fuzzy", "score": score,
        "kcal": round(e["kcal"] * qty), "protein_g": round(e["protein_g"] * qty),
        "carbs_g": round(e["carbs_g"] * qty), "fat_g": round(e["fat_g"] * qty),
        "per_unit": {k: e[k] for k in ("kcal", "protein_g", "carbs_g", "fat_g")},
        "serving": e.get("serving", ""), "source": e.get("source", ""),
        "uses": e.get("uses", 0),
    }


def remember(raw: str, kcal: float, protein: float, carbs: float, fat: float,
             source: str = "", serving: str = "", today: str | None = None) -> str:
    """Upsert an item: store macros PER UNIT (the logged totals / the logged qty), so a
    later different quantity scales correctly. Re-logging the same item bumps uses and
    refreshes the per-unit macros to the latest confirmed values. Returns the key."""
    qty, key, _ = parse_item(raw)
    if not key:
        return ""
    qty = qty or 1.0
    today = today or datetime.date.today().isoformat()
    data = _load()
    items = data.setdefault("items", {})
    # reuse an existing fuzzy identity rather than spawning a near-duplicate key
    m = _best_match(key, items)
    tgt = m[0] if m else key
    prev = items.get(tgt, {})
    items[tgt] = {
        "name": prev.get("name", key),
        "kcal": round(kcal / qty, 1), "protein_g": round(protein / qty, 1),
        "carbs_g": round(carbs / qty, 1), "fat_g": round(fat / qty, 1),
        "serving": serving or prev.get("serving", "1 unit"),
        "source": source or prev.get("source", ""),
        "uses": prev.get("uses", 0) + 1,
        "first": prev.get("first", today), "last": today,
    }
    _save(data)
    return tgt


def resolve(items: list[str]) -> dict:
    """Batch: split a meal's items into pantry hits (reuse) and misses (must look up)."""
    hits, misses = [], []
    for it in items:
        h = lookup(it)
        (hits if h else misses).append(h or it)
    return {"hits": hits, "misses": misses,
            "summary": f"{len(hits)} from pantry, {len(misses)} to look up"}


# ----------------------------------------------------------------- cli
def main() -> int:
    ap = argparse.ArgumentParser(prog="food_pantry")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="split items into pantry hits vs misses (JSON)")
    r.add_argument("--item", action="append", default=[])
    lk = sub.add_parser("lookup", help="resolve one item (JSON)")
    lk.add_argument("query")
    sub.add_parser("list", help="dump the pantry, most-used first")
    a = ap.parse_args()

    if a.cmd == "resolve":
        print(json.dumps(resolve(a.item), ensure_ascii=False))
    elif a.cmd == "lookup":
        print(json.dumps(lookup(a.query) or {"hit": False}, ensure_ascii=False))
    elif a.cmd == "list":
        items = _load().get("items", {})
        for k, e in sorted(items.items(), key=lambda kv: -kv[1].get("uses", 0)):
            print(f"{e.get('uses',0):>3}× {e['name']}: {e['kcal']:g} kcal · "
                  f"{e['protein_g']:g}g P /{e.get('serving','unit')} [{e.get('source','')}]")
        if not items:
            print("(pantry empty)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
