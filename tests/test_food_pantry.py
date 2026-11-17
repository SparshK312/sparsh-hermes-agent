"""Tests for food_pantry.py — the food memory that makes repeat logs instant + consistent.

Pins the matching/scaling logic that, if wrong, would silently reuse the wrong macros:
  * parse_item: quantity + unit stripping + singularization → a stable identity key
  * lookup: exact key, and quantity SCALING of stored per-unit macros
  * fuzzy/containment match: brand/partial shorthand ("northstar" → "Northstar Protein Shake")
  * remember: stores PER-UNIT, bumps uses, and reuses a fuzzy identity (no dup keys)
  * resolve: splits a meal into pantry hits vs lookup-needed misses
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

VAULT_DIR = Path(__file__).resolve().parent.parent / "scripts" / "vault"


def _load(tmp_path):
    spec = importlib.util.spec_from_file_location("food_pantry", VAULT_DIR / "food_pantry.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.PANTRY = tmp_path / "pantry.json"
    return mod


def test_parse_item_qty_units_singular(tmp_path):
    P = _load(tmp_path)
    assert P.parse_item("2 eggs")[:2] == (2.0, "egg")
    assert P.parse_item("1 cup white rice")[:2] == (1.0, "white rice")
    assert P.parse_full("1 cup white rice")[:3] == (1.0, "cup", "white rice")
    assert P.parse_full("200g rice")[:3] == (200.0, "g", "rice")
    assert P.parse_item("a banana")[:2] == (1.0, "banana")
    assert P.parse_item("Northstar Protein Shake")[:2] == (1.0, "northstar protein shake")


def test_remember_stores_per_unit_and_lookup_scales(tmp_path):
    P = _load(tmp_path)
    P.remember("3 eggs", 234, 18, 0, 15, source="mcp")   # → per-egg 78/6/0/5
    one = P.lookup("egg")
    assert one["kcal"] == 78 and one["protein_g"] == 6
    two = P.lookup("2 eggs")
    assert two["kcal"] == 156 and two["protein_g"] == 12 and two["match"] == "exact"


def test_fuzzy_containment_brand_shorthand(tmp_path):
    P = _load(tmp_path)
    P.remember("northstar protein shake", 170, 27, 4, 4, source="label")
    hit = P.lookup("northstar")
    assert hit is not None and hit["matched"] == "northstar protein shake"
    assert hit["match"] == "fuzzy" and hit["kcal"] == 170


def test_save_is_exact_key_never_a_fuzzy_merge(tmp_path):
    """2026-11-16: remember() used to merge into the closest name, so logging
    "egg salad sandwich" overwrote "egg" and "2 eggs" came back as 900 kcal."""
    P = _load(tmp_path)
    P.remember("egg", 78, 6, 0, 5)
    P.remember("egg salad sandwich", 450, 20, 30, 28)
    items = P._load()["items"]
    assert set(items) == {"egg", "egg salad sandwich"}
    assert P.lookup("2 eggs")["kcal"] == 156
    assert P.lookup("egg salad sandwich")["kcal"] == 450
    # re-logging the SAME item still refreshes + bumps, without a duplicate
    P.remember("egg", 80, 6, 0, 5)
    items = P._load()["items"]
    assert len(items) == 2 and items["egg"]["uses"] == 2 and items["egg"]["kcal"] == 80


def test_units_are_part_of_the_identity(tmp_path):
    """2026-11-16: the unit was dropped, so after `200 g white rice` (260 kcal) a later
    `1 cup white rice` resolved 'exact' at 1 kcal."""
    P = _load(tmp_path)
    P.remember("200 g white rice", 260, 5, 56, 1)
    assert P.lookup("1 cup white rice") is None          # grams never answer cups
    assert P.lookup("white rice") is None                # nor a bare count
    assert P.lookup("150 g white rice")["kcal"] == 195   # same unit scales
    assert P.lookup("150g white rice")["kcal"] == 195    # attached unit parses the same
    assert P.lookup("100 grams white rice")["protein_g"] == 2   # per-gram precision kept (2.5 -> 2)
    P.remember("1 cup white rice", 205, 4, 45, 0)
    assert P.lookup("2 cups white rice")["kcal"] == 410
    assert P.lookup("200 g white rice")["kcal"] == 260   # the gram entry is untouched


def test_legacy_entries_without_a_unit_still_hit_for_counts_and_containers(tmp_path):
    """Everything saved before 2026-11-16 has no `unit`. Those must keep answering
    count/container queries, and must never answer a measured one."""
    import json
    P = _load(tmp_path)
    P.PANTRY.write_text(json.dumps({"items": {"rice cake": {
        "name": "rice cake", "kcal": 220, "protein_g": 4, "carbs_g": 44, "fat_g": 4, "uses": 3}}}))
    assert P.lookup("rice cakes")["kcal"] == 220
    assert P.lookup("2 packs rice cakes")["kcal"] == 440
    assert P.lookup("40 g rice cakes") is None


def test_corrupt_pantry_is_loud_and_never_overwritten(tmp_path):
    """2026-11-16: an unreadable pantry.json loaded as EMPTY, and the next save replaced
    every remembered food with one item."""
    import pytest
    P = _load(tmp_path)
    P.PANTRY.write_text("{broken")
    with pytest.raises(P.PantryError):
        P.lookup("egg")
    with pytest.raises(P.PantryError):
        P.remember("egg", 78, 6, 0, 5)
    assert P.PANTRY.read_text() == "{broken"


def test_negative_macros_are_not_saved(tmp_path):
    P = _load(tmp_path)
    assert P.remember("pad thai sauce", 91, -1, 10, 3) == ""
    assert P.lookup("pad thai sauce") is None


def test_lookup_miss_returns_none(tmp_path):
    P = _load(tmp_path)
    assert P.lookup("dragonfruit smoothie") is None


def test_resolve_splits_hits_and_misses(tmp_path):
    P = _load(tmp_path)
    P.remember("honey oat cereal bowl", 220, 4, 44, 4)
    out = P.resolve(["honey oat cereal", "2 slices pizza", "northstar"])
    matched = {h["input"] for h in out["hits"]}
    assert "honey oat cereal" in matched          # fuzzy/normalized hit
    assert "2 slices pizza" in out["misses"]
    assert "northstar" in out["misses"]        # not stored → miss
    assert len(out["hits"]) + len(out["misses"]) == 3


def test_decimal_and_fraction_amounts_are_read_before_punctuation_is_stripped(tmp_path):
    """2026-11-16: "0.5 chicken bowl" was normalised to "0 5 chicken bowl" (qty 0, a stray 5
    in the name), so HALF a restaurant bowl was remembered as a whole one."""
    P = _load(tmp_path)
    assert P.parse_full("0.5 chicken rice bowl")[:3] == (0.5, "", "chicken rice")
    assert P.parse_full("1.5 cups white rice")[:3] == (1.5, "cup", "white rice")
    assert P.parse_full("1/2 chicken sandwich")[:3] == (0.5, "", "chicken sandwich")
    assert P.parse_full("half of a chicken sandwich")[:3] == (0.5, "", "chicken sandwich")
    assert P.parse_full("2.5g magnesium")[:3] == (2.5, "g", "magnesium")
    assert P.parse_full("apple")[:3] == (1.0, "", "apple") and P.parse_full("an apple")[:3] == (1.0, "", "apple")
    P.remember("0.5 chicken rice bowl", 308, 26, 27, 10)          # half eaten -> the WHOLE is stored
    assert P.lookup("chicken rice bowl")["kcal"] == 616
    assert P.lookup("0.5 chicken rice bowl")["kcal"] == 308
    assert "0 5" not in next(iter(P._load()["items"]))
