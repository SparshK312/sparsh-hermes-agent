"""Tests for food_pantry.py — the food memory that makes repeat logs instant + consistent.

Pins the matching/scaling logic that, if wrong, would silently reuse the wrong macros:
  * parse_item: quantity + unit stripping + singularization → a stable identity key
  * lookup: exact key, and quantity SCALING of stored per-unit macros
  * fuzzy/containment match: brand/partial shorthand ("fairlife" → "Fairlife Core Power")
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
    assert P.parse_item("a banana")[:2] == (1.0, "banana")
    assert P.parse_item("Fairlife Core Power")[:2] == (1.0, "fairlife core power")


def test_remember_stores_per_unit_and_lookup_scales(tmp_path):
    P = _load(tmp_path)
    P.remember("3 eggs", 234, 18, 0, 15, source="mcp")   # → per-egg 78/6/0/5
    one = P.lookup("egg")
    assert one["kcal"] == 78 and one["protein_g"] == 6
    two = P.lookup("2 eggs")
    assert two["kcal"] == 156 and two["protein_g"] == 12 and two["match"] == "exact"


def test_fuzzy_containment_brand_shorthand(tmp_path):
    P = _load(tmp_path)
    P.remember("fairlife core power", 170, 27, 4, 4, source="label")
    hit = P.lookup("fairlife")
    assert hit is not None and hit["matched"] == "fairlife core power"
    assert hit["match"] == "fuzzy" and hit["kcal"] == 170


def test_remember_reuses_fuzzy_identity_no_dup(tmp_path):
    P = _load(tmp_path)
    P.remember("fairlife core power", 170, 27, 4, 4)
    P.remember("fairlife", 175, 27, 4, 5)            # shorthand → same entry, refresh + bump
    items = P._load()["items"]
    assert len(items) == 1
    only = next(iter(items.values()))
    assert only["uses"] == 2 and round(only["kcal"]) == 175


def test_lookup_miss_returns_none(tmp_path):
    P = _load(tmp_path)
    assert P.lookup("dragonfruit smoothie") is None


def test_resolve_splits_hits_and_misses(tmp_path):
    P = _load(tmp_path)
    P.remember("lucky charms bowl", 220, 4, 44, 4)
    out = P.resolve(["lucky charms", "2 slices pizza", "fairlife"])
    matched = {h["input"] for h in out["hits"]}
    assert "lucky charms" in matched          # fuzzy/normalized hit
    assert "2 slices pizza" in out["misses"]
    assert "fairlife" in out["misses"]        # not stored → miss
    assert len(out["hits"]) + len(out["misses"]) == 3
