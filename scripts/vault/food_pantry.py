#!/usr/bin/env python3
"""
food_pantry.py — Sparsh's food memory (retrieval over past meals, done right).

The log-food skill used to re-research every item every time (12 MCP lookups + web
searches for one plate of dumplings), which is slow AND inconsistent — the same protein
shake came back 160 kcal one day, 180 the next. This caches every item Sparsh has
ALREADY confirmed, keyed by a normalized food identity, and reuses HIS numbers on a
repeat. New items still get looked up — then remembered, so they're a hit next time.

Why a cache and not a vector DB: he eats a few hundred distinct things, not millions.
Normalized-name + fuzzy-token matching covers the repeats at zero infra/latency/cost,
stays deterministic, and is human-editable. A semantic layer can bolt on later if
plain matching ever misses repeats — it doesn't need to yet.

Macros are stored PER ONE UNIT of the item, AND THE UNIT IS PART OF THE ENTRY (since
2026-11-16): "200 g white rice" is stored per gram under `white rice @g`, "1 cup white
rice" per cup under `white rice @cup`, "3 eggs" per egg under `egg`. Before that the
unit was thrown away, so after `200 g rice` (260 kcal) a later `1 cup rice` resolved to
1 kcal. Saves are EXACT-KEY ONLY: the old fuzzy merge let "egg salad sandwich" overwrite
"egg" (then "2 eggs" came back as 900 kcal). Fuzzy matching is for lookups only, and a
fuzzy hit is labelled so the skill can confirm it. Stdlib only → runs on system python.

  food_pantry.py resolve --item "2 eggs" --item "protein shake" ...   # JSON: hits + misses
  food_pantry.py lookup "cereal bowl"                             # JSON one item
  food_pantry.py list                                             # human dump
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path


def _default_vault() -> Path:
    env = os.environ.get("HERMES_VAULT")
    if env:
        return Path(env)
    vps = Path("/home/hermes/vault")
    if vps.exists():
        return vps
    return Path.home() / "Documents" / "Vault"


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

# Units that MEASURE an amount. A query in one of these only ever matches an entry saved
# in the same unit (grams never answer cups). Container words (bowl, slice, bottle…) are
# looser: with no same-unit entry they may fall back to an entry saved without a unit.
_CANON = {"gram": "g", "grams": "g", "ounce": "oz", "ounces": "oz", "cups": "cup",
          "kilogram": "kg", "kilograms": "kg", "litre": "l", "liter": "l", "lbs": "lb"}
_MEASURED = {"g", "kg", "ml", "l", "oz", "lb", "cup", "tbsp", "tsp"}
_UNITS |= {"kg", "lb", "lbs", "kilogram", "kilograms", "litre", "liter"}
_WORD_QTY = {"half": 0.5, "a": 1.0, "an": 1.0, "one": 1.0, "two": 2.0, "three": 3.0}
# A leading amount: 2 | 0.5 | 1/2 | half | a, optionally with a unit glued on (200g).
_LEAD = re.compile(r"^\s*(?:(?P<num>\d+(?:\.\d+)?)(?:\s*/\s*(?P<den>\d+(?:\.\d+)?))?"
                   r"(?P<unit>g|kg|ml|l|oz|lb)?(?![\w.])|(?P<word>half|an|a|one|two|three)\b)\s*", re.I)


class PantryError(RuntimeError):
    """pantry.json exists but cannot be read. Never treated as an empty pantry: the next
    save would overwrite every remembered food with one item."""


def canon_unit(u: str) -> str:
    u = (u or "").lower()
    u = _CANON.get(u, u)
    if u.endswith("s") and len(u) > 3 and u not in _MEASURED:
        u = u[:-1]                     # slices → slice, bowls → bowl
    return u


def store_key(key: str, unit: str) -> str:
    return f"{key} @{unit}" if unit else key


def norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)        # drop punctuation
    return re.sub(r"\s+", " ", s).strip()


def parse_full(raw: str) -> tuple[float, str, str, str]:
    """'2 eggs' -> (2.0, '', 'egg', '2 eggs'); '200 g white rice' and '200g white rice'
    -> (200.0, 'g', 'white rice', ...); '1 cup white rice' -> (1.0, 'cup', 'white rice', ...).
    Returns (qty, unit, identity_key, original_display)."""
    display = raw.strip()
    qty, unit = 1.0, ""
    # Read the leading amount from the RAW text, before norm() turns punctuation into
    # spaces. norm() made "0.5 bowl" into "0 5 bowl" (qty 0, a stray "5" in the name) and
    # "1/2" into "1 2": found 2026-11-16 when half a restaurant bowl was remembered as a
    # whole one. Old keys like "cheese 0 5" and "white rice 1 5 peas corn" are that bug.
    rest = display
    m = _LEAD.match(display)
    if m:
        if m["num"]:
            qty = float(m["num"]) / float(m["den"]) if m["den"] else float(m["num"])
        elif m["word"]:
            qty = _WORD_QTY[m["word"].lower()]
        unit = canon_unit(m["unit"]) if m["unit"] else ""
        rest = display[m.end():]
    toks = norm(rest).split()
    if toks and toks[0] == "of":                       # "half of a sandwich"
        toks = toks[1:]
    if toks and toks[0] in ("a", "an"):
        toks = toks[1:]
    # a leading unit word ("cup", "slice", "bottle"…) is the unit of the quantity
    if not unit and toks and toks[0] in _UNITS:
        unit = canon_unit(toks[0]); toks = toks[1:]
    toks = [t for t in toks if t not in _STOP and t not in _UNITS]
    # singularize a trailing plural so "eggs"/"egg" share a key (naive but effective)
    if toks and toks[-1].endswith("s") and len(toks[-1]) > 3:
        toks[-1] = toks[-1][:-1]
    return qty, unit, " ".join(toks), display


def parse_item(raw: str) -> tuple[float, str, str]:
    """Back-compat view of parse_full without the unit: (qty, identity_key, display)."""
    qty, _unit, key, display = parse_full(raw)
    return qty, key, display


def _load() -> dict:
    """{} pantry when the file does not exist yet; PantryError when it exists and is
    unreadable (it used to come back as empty, and the next save wiped the pantry)."""
    if not PANTRY.exists():
        return {"items": {}}
    try:
        data = json.loads(PANTRY.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
            raise ValueError("no 'items' object")
        return data
    except Exception as e:  # noqa: BLE001
        raise PantryError(f"{PANTRY} is unreadable ({type(e).__name__}: {e}); "
                          f"not touching it") from e


def _save(data: dict) -> None:
    """Atomic: write a sibling temp file, then swap it in."""
    PANTRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = PANTRY.with_name(PANTRY.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PANTRY)


def _today() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:  # noqa: BLE001
        return datetime.date.today().isoformat()


def _score(a: str, b: str) -> float:
    """Similarity of two normalized food keys. Blends Jaccard with a containment
    term so brand/partial shorthand still matches ('northstar' → 'northstar protein
    shake', 'chicken bowl' → 'grilled chicken bowl'). Containment is weighted just
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


def _entry_key(k: str) -> str:
    return k.split(" @", 1)[0]


def _best_match(key: str, unit: str, items: dict) -> tuple[str, float] | None:
    """Exact (key, unit) wins (1.0). Otherwise the best-scoring entry above threshold
    AMONG ENTRIES THAT CAN ANSWER THIS UNIT, tie-broken by most-eaten:
      * same unit always qualifies;
      * a container/count query ("1 bowl …", "2 …") may also use an entry saved with no
        unit (how everything was saved before 2026-11-16);
      * a measured query (g, ml, cup, oz…) never borrows another unit's numbers."""
    sk = store_key(key, unit)
    if sk in items and items[sk].get("unit", "") == unit:
        return sk, 1.0
    if unit not in _MEASURED and key in items and not items[key].get("unit"):
        return key, 1.0
    best, best_score, best_uses = None, 0.0, -1
    for k, e in items.items():
        eu = e.get("unit", "")
        if eu != unit and not (unit not in _MEASURED and not eu):
            continue
        s = _score(key, _entry_key(k))
        uses = e.get("uses", 0)
        if s >= FUZZY_THRESHOLD and (s > best_score or (s == best_score and uses > best_uses)):
            best, best_score, best_uses = k, s, uses
    return (best, round(best_score, 2)) if best else None


def lookup(raw: str) -> dict | None:
    """Resolve one logged item against the pantry, scaling stored per-unit macros to
    the requested quantity. None on a miss."""
    qty, unit, key, display = parse_full(raw)
    if not key:
        return None
    items = _load().get("items", {})
    m = _best_match(key, unit, items)
    if not m:
        return None
    mk, score = m
    e = items[mk]
    return {
        "input": display, "matched": e["name"], "key": mk, "qty": qty, "unit": unit,
        "match": "exact" if score == 1.0 else "fuzzy", "score": score,
        "kcal": round(e["kcal"] * qty), "protein_g": round(e["protein_g"] * qty),
        "carbs_g": round(e["carbs_g"] * qty), "fat_g": round(e["fat_g"] * qty),
        "per_unit": {k: e[k] for k in ("kcal", "protein_g", "carbs_g", "fat_g")},
        "serving": e.get("serving", ""), "source": e.get("source", ""),
        "uses": e.get("uses", 0),
    }


def remember(raw: str, kcal: float, protein: float, carbs: float, fat: float,
             source: str = "", serving: str = "", today: str | None = None) -> str:
    """Upsert an item under its EXACT (identity, unit) key. Macros are stored per one of
    that unit (logged totals / logged qty), so a later different quantity in the same
    unit scales correctly. Re-logging bumps uses and refreshes the numbers. Never merges
    into a merely similar name. Negative macros are refused. Returns the stored key."""
    qty, unit, key, _ = parse_full(raw)
    if not key or min(kcal, protein, carbs, fat) < 0:
        return ""
    qty = qty or 1.0
    today = today or _today()
    data = _load()
    items = data.setdefault("items", {})
    tgt = store_key(key, unit)
    prev = items.get(tgt, {})
    nd = 4 if unit in _MEASURED else 1          # per-gram values need the precision
    items[tgt] = {
        "name": prev.get("name", key),
        "kcal": round(kcal / qty, nd), "protein_g": round(protein / qty, nd),
        "carbs_g": round(carbs / qty, nd), "fat_g": round(fat / qty, nd),
        "unit": unit,
        "serving": serving or prev.get("serving") or (f"1 {unit}" if unit else "1 unit"),
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

    try:
        _load()
    except PantryError as e:
        # Loud, and distinguishable from "nothing matched": the skill must not read a
        # broken pantry as a pantry with no hits.
        print(json.dumps({"error": "pantry-unreadable", "detail": str(e)}, ensure_ascii=False))
        print(f"food_pantry: {e}", file=sys.stderr)
        return 2

    if a.cmd == "resolve":
        print(json.dumps(resolve(a.item), ensure_ascii=False))
    elif a.cmd == "lookup":
        print(json.dumps(lookup(a.query) or {"hit": False}, ensure_ascii=False))
    elif a.cmd == "list":
        items = _load().get("items", {})
        for k, e in sorted(items.items(), key=lambda kv: -kv[1].get("uses", 0)):
            print(f"{e.get('uses',0):>3}× {e['name']}: {e['kcal']:g} kcal · "
                  f"{e['protein_g']:g}g P /{e.get('serving','unit')} [{e.get('source','')}]"
                  + (f" key={k!r}" if k != e["name"] else ""))
        if not items:
            print("(pantry empty)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
