"""Prislogikk: avrunding, ratioer og prisoppdatering mot 1076-malen."""
import pytest

from backend import osdm_editor as oe
from conftest import TEMPLATE_1076


@pytest.fixture(scope="module")
def template_bytes():
    return TEMPLATE_1076.read_bytes()


def _fare_prices(entry):
    fs = entry["data"]["fareDelivery"]["fareStructure"]
    pm = oe._build_price_map(fs["prices"])
    return [pm[f["priceRef"]] for f in fs["fares"]]


def _adult_price_per_rc(entry):
    fs = entry["data"]["fareDelivery"]["fareStructure"]
    pm = entry["price_map"]
    result = {}
    for f in fs["fares"]:
        if f["passengerConstraintRef"] == entry["adult_id"]:
            rc = f["regionalConstraintRef"]
            result[rc] = max(result.get(rc, 0.0), pm[f["priceRef"]])
    return result


# --- Avrunding -------------------------------------------------------------

def test_round_up_keeps_exact_multiples():
    for cents in range(20, 200_000, 20):
        assert oe.round_up_020(cents / 100) == cents / 100


def test_round_up_float_noise_does_not_add_step():
    # 20.00 × (12.60 / 20.00) = 12.600000000000001 i flyttall
    assert oe.round_up_020(20.0 * (12.6 / 20.0)) == 12.6


def test_round_up_rounds_up():
    assert oe.round_up_020(12.61) == 12.8
    assert oe.round_up_020(0.01) == 0.2
    assert oe.round_up_020(0) == 0.0
    assert oe.round_up_020(-5) == 0.0


# --- Ratio-utledning -------------------------------------------------------

def test_infer_ratio_recovers_exact_ratio_from_rounded_prices():
    adults = [x / 5 for x in range(50, 3000, 7)]
    pairs = [(a, oe.round_up_020(a * 0.25)) for a in adults]
    assert oe._infer_ratio(pairs) == 0.25


def test_infer_ratio_falls_back_to_mean_when_prices_do_not_fit_rounding():
    pairs = [(100.0, 50.0), (100.0, 60.0)]
    assert oe._infer_ratio(pairs) == pytest.approx(0.55)


def test_1076_ratios(template_bytes):
    entry = oe.load_osdm(template_bytes)
    by_pc = {pc: entry["ratios"][f"1076_7.0_{pc}"] for pc in ("G__1", "G__2", "G__3", "G__4", "G__5", "G__8")}
    assert entry["adult_id"] == "1076_7.0_G__1"
    assert by_pc == {"G__1": 1.0, "G__2": 0.9, "G__3": 0.25, "G__4": 0.25, "G__5": 0.5, "G__8": 0.5}


# --- Prisoppdatering -------------------------------------------------------

def test_unchanged_adult_prices_change_nothing(template_bytes):
    """Regresjon for prisdrift (K1): samme voksenpriser → ingen fare endrer pris."""
    entry = oe.load_osdm(template_bytes)
    before = _fare_prices(entry)
    oe.set_prices_from_adult(entry, _adult_price_per_rc(entry))
    assert _fare_prices(entry) == before


def test_regenerating_twice_is_stable(template_bytes):
    """Generering på forrige generering skal ikke krype oppover."""
    entry = oe.load_osdm(template_bytes)
    oe.set_prices_from_adult(entry, _adult_price_per_rc(entry))
    entry2 = oe.load_osdm(oe.serialize_osdm(entry))
    before = _fare_prices(entry2)
    oe.set_prices_from_adult(entry2, _adult_price_per_rc(entry2))
    assert _fare_prices(entry2) == before


def test_new_adult_price_sets_categories_and_scales_fip(template_bytes):
    entry = oe.load_osdm(template_bytes)
    fs = entry["data"]["fareDelivery"]["fareStructure"]
    pm = dict(entry["price_map"])
    rc = fs["regionalConstraints"][0]["id"]

    old = {}
    for f in fs["fares"]:
        if f["regionalConstraintRef"] == rc:
            key = (f["serviceClassRef"], f["passengerConstraintRef"])
            old.setdefault(key, []).append(pm[f["priceRef"]])

    new_adult = 108.60
    oe.set_prices_from_adult(entry, {rc: new_adult})
    pm_new = oe._build_price_map(fs["prices"])

    new = {}
    for f in fs["fares"]:
        if f["regionalConstraintRef"] == rc:
            key = (f["serviceClassRef"], f["passengerConstraintRef"])
            new.setdefault(key, []).append(pm_new[f["priceRef"]])

    for (sc, pc), old_prices in old.items():
        ratio = entry["ratios"][pc]
        primary = oe.round_up_020(new_adult * ratio) if ratio > 0 else 0.0
        primary_old = max(old_prices)
        expected = []
        for p in old_prices:
            if p >= primary_old or primary_old <= 0 or primary <= 0:
                expected.append(primary)
            else:  # FIP o.l.: proporsjonalt med primærfaren
                expected.append(oe.round_up_020(primary * p / primary_old))
        assert new[(sc, pc)] == expected, (sc, pc)

    adult_fares = new[(next(iter(old))[0], entry["adult_id"])]
    assert max(adult_fares) == new_adult


def test_category_prices_use_published_adult_price(template_bytes):
    """Kategoripris = ratio × den publiserte (avrundede) voksenprisen. Med urundet
    voksenpris drev ratioene ved ny utledning (f.eks. gruppevoksen 0,9003 ved ×1,05)."""
    entry = oe.load_osdm(template_bytes)
    adult_id = entry["adult_id"]
    unrounded = {rc: price * 1.05 for rc, price in _adult_price_per_rc(entry).items()}
    oe.set_prices_from_adult(entry, unrounded)

    fs = entry["data"]["fareDelivery"]["fareStructure"]
    pm = entry["price_map"]
    primary: dict[tuple, float] = {}
    for f in fs["fares"]:
        key = (f["regionalConstraintRef"], f["passengerConstraintRef"])
        primary[key] = max(primary.get(key, 0.0), pm[f["priceRef"]])
    for (rc, pc), price in primary.items():
        adult = oe.round_up_020(unrounded[rc])
        ratio = entry["ratios"][pc]
        assert price == (adult if pc == adult_id else oe.round_up_020(adult * ratio)), (rc, pc)

    entry2 = oe.load_osdm(oe.serialize_osdm(entry))
    assert entry2["ratios"] == entry["ratios"]
