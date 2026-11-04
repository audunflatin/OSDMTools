"""
OSDM Editor – parse, analyser og modifiser OSDM fareDelivery-filer.

Designprinsipper:
- Priser er absolutte EUR-beløp (ikke ratio-felt i selve filen).
- Ratio utledes fra eksisterende priser (se _infer_ratio).
- Voksen = passasjertype med høyest gjennomsnittlig ikke-null pris.
- Priser = 0 (FIP, gratis) røres aldri ved recalkulering.
- Nye priser rundes opp til nærmeste 0,20 EUR (DRTF-krav).
- Nye RCs kopierer (carrier × serviceClass × pc)-kombinasjoner fra første RC.
"""

import json
import math
import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

# UIC-prefiks (2 siffer) → ISO-landskode
_UIC_COUNTRY: dict[str, str] = {
    "10": "FI", "20": "RU", "21": "BY", "22": "UA", "24": "LT",
    "25": "LV", "26": "EE", "41": "BA", "44": "SI", "49": "HR",
    "51": "PL", "53": "BG", "54": "CZ", "55": "HU", "56": "SK",
    "57": "RO", "58": "RS", "59": "ME", "61": "MD", "65": "MK",
    "70": "GB", "71": "ES", "72": "RS", "73": "GR", "74": "SE",
    "75": "NO", "76": "NO", "79": "SI", "80": "DE", "81": "AT",
    "82": "LU", "83": "IT", "84": "NL", "85": "CH", "86": "DK",
    "87": "FR", "88": "BE", "89": "PT", "90": "ES", "94": "GR",
}

MAX_RC_IN_SUMMARY = 500


# ---------------------------------------------------------------------------
# Hjelpefunksjoner
# ---------------------------------------------------------------------------

def _country_from_uic(uic: str) -> str:
    return _UIC_COUNTRY.get(str(uic)[:2], "XX")


def eur_entry(price_obj: dict) -> dict | None:
    """Returner EUR-oppføringen i en OSDM-pris (`price_obj["price"]` kan ha flere valutaer)."""
    entries = price_obj.get("price") or []
    return next((p for p in entries if p.get("currency") == "EUR"), None)


def _get_price_eur(price_entry: dict) -> float:
    p = eur_entry(price_entry)
    if p is None:
        return 0.0
    return p["amount"] / (10 ** p.get("scale", 2))


def set_calendar_validity(calendars: list, valid_from: str, valid_to: str) -> int:
    """Sett gyldighetsperiode på alle kalendere. Datoer er lokale (YYYY-MM-DD, Oslo).

    Formatet følger resten av appen: lokal dato med «+0000» og utcOffset i
    minutter for Oslo-tid på fra-datoen. Returnerer utcOffset.
    """
    from_dt = datetime.fromisoformat(valid_from[:10]).replace(tzinfo=ZoneInfo("Europe/Oslo"))
    utc_offset = int(from_dt.utcoffset().total_seconds() / 60)
    for cal in calendars:
        cal["fromDate"] = f"{valid_from[:10]}T00:00:00+0000"
        cal["untilDate"] = f"{valid_to[:10]}T23:59:59+0000"
        cal["utcOffset"] = utc_offset
    return utc_offset


def round_up_020(eur: float) -> float:
    """Rund opp til nærmeste 0,20 EUR (DRTF-krav).

    Regner i hele cent: `math.ceil(eur / 0.20)` alene runder f.eks.
    12.600000000000001 opp til 12,80 pga. flyttallsunøyaktighet.
    """
    if eur <= 0:
        return 0.0
    cents = round(eur * 100, 6)
    return math.ceil(cents / 20) * 20 / 100


_round_to_20_cents = round_up_020


def _eur_to_amount_int(eur: float, scale: int = 2) -> int:
    return round(eur * (10 ** scale))


def _detect_id_prefix(fs: dict) -> str:
    for section in ("regionalConstraints", "prices", "connectionPoints", "passengerConstraints"):
        for item in fs.get(section, []):
            item_id = item.get("id", "")
            m = re.match(r"^(.*?)[A-Z]__\d+$", item_id)
            if m:
                return m.group(1)
    return ""


def _max_id_num(items: list, prefix: str, letter: str) -> int:
    pattern = re.compile(rf"^{re.escape(prefix)}{letter}__(\d+)$")
    max_n = 0
    for item in items:
        m = pattern.match(item.get("id", ""))
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n


def _build_text_map(texts: list) -> dict[str, str]:
    result: dict[str, str] = {}
    for t in texts:
        tid = t.get("id", "")
        val = t.get("textUtf8") or t.get("text") or ""
        if tid:
            result[tid] = val
    return result


def _build_price_map(prices: list) -> dict[str, float]:
    return {p["id"]: _get_price_eur(p) for p in prices}


def _build_amount_to_price_id(prices: list) -> dict[int, str]:
    """Deduplication: amount_in_cents → first price_id with that amount."""
    result: dict[int, str] = {}
    for p in prices:
        eur = _get_price_eur(p)
        cents = round(eur * 100)
        if cents not in result:
            result[cents] = p["id"]
    return result


def _build_cp_maps(connection_points: list) -> tuple[dict, dict]:
    """Returns (uic→cp_id, cp_id→uic)."""
    uic_to_cp: dict[str, str] = {}
    cp_to_uic: dict[str, str] = {}
    for cp in connection_points:
        cp_id = cp["id"]
        for station_set in cp.get("stationSets", []):
            for station in station_set:
                if station.get("codeList") == "UIC":
                    uic = str(station["code"])
                    if uic not in uic_to_cp:
                        uic_to_cp[uic] = cp_id
                    if cp_id not in cp_to_uic:
                        cp_to_uic[cp_id] = uic
    return uic_to_cp, cp_to_uic


def _build_station_name_map(station_names: list) -> dict[str, str]:
    result: dict[str, str] = {}
    for s in station_names:
        name = s.get("nameUtf8") or s.get("name") or ""
        if not name:
            continue
        # Flat code field (most operators)
        if "code" in s:
            code = str(s["code"])
            if code:
                result[code] = name
        # Trenitalia / Italian format: country + localCode
        elif "country" in s and "localCode" in s:
            try:
                uic = str(int(s["country"]) * 100000 + int(s["localCode"]))
                result[uic] = name
            except (TypeError, ValueError):
                pass
    return result


def _find_adult_id(pcs: list, fares: list, price_map: dict) -> str:
    """Passasjertype med høyest gjennomsnittlig ikke-null pris (ingen reductionConstraintRef).
    Bruker maks-pris per (rc, carrier, sc, pc)-gruppe for å hindre at FIP/rabattfarer
    fortyner snittet og fører til at feil PC velges som voksen."""
    # Maks pris per unik (rc, carrier, sc, pc)-kombinasjon
    max_prices: dict[tuple, float] = {}
    for fare in fares:
        if fare.get("reductionConstraintRef"):
            continue
        pc_id = fare.get("passengerConstraintRef")
        price_id = fare.get("priceRef")
        if not pc_id or not price_id:
            continue
        eur = price_map.get(price_id, 0.0)
        if eur <= 0:
            continue
        key = (
            fare.get("regionalConstraintRef"),
            fare.get("carrierConstraintRef"),
            fare.get("serviceClassRef"),
            pc_id,
        )
        if key not in max_prices or eur > max_prices[key]:
            max_prices[key] = eur

    # Gjennomsnitt av maks-priser per PC
    total: dict[str, float] = {}
    count: dict[str, int] = {}
    for (_, _, _, pc_id), eur in max_prices.items():
        total[pc_id] = total.get(pc_id, 0.0) + eur
        count[pc_id] = count.get(pc_id, 0) + 1

    best_id, best_avg = None, -1.0
    for pc_id, tot in total.items():
        avg = tot / count[pc_id]
        if avg > best_avg:
            best_avg = avg
            best_id = pc_id

    if not best_id:
        for pc in pcs:
            if pc.get("passengerType") == "ADULT":
                return pc["id"]
        if pcs:
            return pcs[0]["id"]
    return best_id or ""


def _infer_ratio(pairs: list[tuple[float, float]]) -> float:
    """Utled ratio fra (voksenpris, kategoripris)-par.

    Prisene er rundet opp til nærmeste 0,20 EUR, så den sanne ratioen r må
    oppfylle  pris - 0,20 < voksen × r <= pris  for hver relasjon. Snittet av
    pris/voksen er derfor skjevt oppover (f.eks. 0,2516 i stedet for 0,25),
    og gir prisdrift ved neste generering. I stedet tar vi fellesmengden av alle
    intervallene og velger den «runde» verdien (færrest desimaler) innenfor.
    Hvis prisene ikke passer med opprundingsregelen, brukes snittet.
    """
    eps = 1e-9
    upper = min(c / a for a, c in pairs)
    lower = max((c - 0.20) / a for a, c in pairs)
    if lower > upper + eps:
        return sum(c / a for a, c in pairs) / len(pairs)
    for decimals in range(0, 5):
        r = round(upper, decimals)
        if lower - eps <= r <= upper + eps and r >= 0:
            return r
    return upper


def _compute_ratios(adult_id: str, pcs: list, fares: list, price_map: dict) -> dict[str, float]:
    """Utled ratio per passasjertype relativt til voksenpris."""
    # Grupper farer etter (rc, carrier, sc) → {pc_id: pris}
    groups: dict[tuple, dict[str, float]] = {}
    for fare in fares:
        if fare.get("reductionConstraintRef"):
            continue
        pc_id = fare.get("passengerConstraintRef")
        key = (
            fare.get("regionalConstraintRef"),
            fare.get("carrierConstraintRef"),
            fare.get("serviceClassRef"),
        )
        if not pc_id or not key[0]:  # kun regionalConstraintRef er påkrevd
            continue
        price_id = fare.get("priceRef")
        eur = price_map.get(price_id, 0.0)
        if key not in groups:
            groups[key] = {}
        # Behold høyeste pris per PC (primærfare) — unngår at FIP/rabattfarer forringer ratioen
        if pc_id not in groups[key] or eur > groups[key][pc_id]:
            groups[key][pc_id] = eur

    pairs: dict[str, list[tuple[float, float]]] = {}
    for pc_prices in groups.values():
        adult_price = pc_prices.get(adult_id)
        if not adult_price or adult_price <= 0:
            continue
        for pc_id, price in pc_prices.items():
            pairs.setdefault(pc_id, []).append((adult_price, price))

    ratios: dict[str, float] = {pc_id: _infer_ratio(p) for pc_id, p in pairs.items()}
    ratios[adult_id] = 1.0
    for pc in pcs:
        if pc["id"] not in ratios:
            ratios[pc["id"]] = 0.0
    return ratios


def _get_fare_templates(fares: list, rcs: list) -> list[dict]:
    """
    Hent uike (pc, carrier, sc)-kombinasjoner fra første RC (uten reductionConstraintRef).
    Returnerer full fare-kopi som mal for nye RC-er.
    """
    if not rcs:
        return []
    first_rc_id = rcs[0]["id"]
    templates: list[dict] = []
    seen: set[tuple] = set()
    for fare in fares:
        if fare.get("regionalConstraintRef") != first_rc_id:
            continue
        if fare.get("reductionConstraintRef"):
            continue
        key = (
            fare.get("passengerConstraintRef"),
            fare.get("carrierConstraintRef"),
            fare.get("serviceClassRef"),
        )
        if key in seen or not all(key):
            continue
        seen.add(key)
        templates.append(dict(fare))
    return templates


def _detect_price_format(prices: list) -> tuple[str, int]:
    """Returner (currency, scale) fra første prisoppføring."""
    for p in prices:
        for entry in p.get("price", []):
            return entry.get("currency", "EUR"), entry.get("scale", 2)
    return "EUR", 2


# ---------------------------------------------------------------------------
# Offentlig API
# ---------------------------------------------------------------------------

def load_osdm(content: bytes) -> dict:
    """
    Parser OSDM-fil og returnerer en store-entry dict med:
    - data: hele den parsede JSON (muteres av later operations)
    - adult_id, ratios, price_map, uic_to_cp, cp_to_uic,
      station_name_map, text_map, id_prefix, fare_templates
    """
    data = json.loads(content)
    fs = data["fareDelivery"]["fareStructure"]

    pcs = fs.get("passengerConstraints", [])
    fares = fs.get("fares", [])
    prices = fs.get("prices", [])
    rcs = fs.get("regionalConstraints", [])
    cps = fs.get("connectionPoints", [])

    price_map = _build_price_map(prices)
    text_map = _build_text_map(fs.get("texts", []))
    uic_to_cp, cp_to_uic = _build_cp_maps(cps)
    station_name_map = _build_station_name_map(fs.get("stationNames", []))
    adult_id = _find_adult_id(pcs, fares, price_map)
    ratios = _compute_ratios(adult_id, pcs, fares, price_map)
    id_prefix = _detect_id_prefix(fs)
    fare_templates = _get_fare_templates(fares, rcs)

    return {
        "data": data,
        "adult_id": adult_id,
        "ratios": ratios,
        "price_map": price_map,
        "uic_to_cp": uic_to_cp,
        "cp_to_uic": cp_to_uic,
        "station_name_map": station_name_map,
        "text_map": text_map,
        "id_prefix": id_prefix,
        "fare_templates": fare_templates,
    }


def get_summary(store_entry: dict, filename: str = "") -> dict:
    """Returner strukturoversikt for frontend."""
    data = store_entry["data"]
    fs = data["fareDelivery"]["fareStructure"]
    delivery = data["fareDelivery"]["delivery"]
    adult_id = store_entry["adult_id"]
    ratios = store_entry["ratios"]
    text_map = store_entry["text_map"]
    cp_to_uic = store_entry["cp_to_uic"]
    station_name_map = store_entry["station_name_map"]

    pcs = fs.get("passengerConstraints", [])
    ccs = fs.get("carrierConstraints", [])
    rcs = fs.get("regionalConstraints", [])
    fares = fs.get("fares", [])
    prices = fs.get("prices", [])
    calendars = fs.get("calendars", [])

    passenger_types = []
    for pc in pcs:
        name_ref = pc.get("nameRef")
        name = text_map.get(name_ref, "") if name_ref else ""
        if not name:
            name = pc.get("id", "")
        passenger_types.append({
            "id": pc["id"],
            "name": name,
            "type": pc.get("passengerType", ""),
            "ratio": round(ratios.get(pc["id"], 0.0), 4),
            "is_adult": pc["id"] == adult_id,
        })

    carrier_list = []
    for cc in ccs:
        included = cc.get("includedCarrier", [])
        carrier_list.append({"id": cc["id"], "carriers": included})

    rc_list = []
    for rc in rcs[:MAX_RC_IN_SUMMARY]:
        entry_uic = cp_to_uic.get(rc.get("entryConnectionPointId", ""), "")
        exit_uic = cp_to_uic.get(rc.get("exitConnectionPointId", ""), "")
        rc_list.append({
            "id": rc["id"],
            "from_uic": entry_uic,
            "to_uic": exit_uic,
            "from_name": station_name_map.get(entry_uic, "") or entry_uic,
            "to_name": station_name_map.get(exit_uic, "") or exit_uic,
            "distance": rc.get("distance", 0),
        })

    validity = None
    if calendars:
        c = calendars[0]
        validity = {
            "from_date": c.get("fromDate", "")[:10],
            "until_date": c.get("untilDate", "")[:10],
        }

    return {
        "filename": filename,
        "delivery": {
            "fareProvider": delivery.get("fareProvider", ""),
            "deliveryId": delivery.get("deliveryId", ""),
            "previousDeliveryId": delivery.get("previousDeliveryId", ""),
            "usage": delivery.get("usage", "PRODUCTION"),
            "optionalDelivery": bool(delivery.get("optionalDelivery", False)),
            "version": delivery.get("version", ""),
        },
        "validity": validity,
        "stats": {
            "rc_count": len(rcs),
            "fare_count": len(fares),
            "price_count": len(prices),
            "rc_list_truncated": len(rcs) > MAX_RC_IN_SUMMARY,
        },
        "passenger_types": passenger_types,
        "carrier_constraints": carrier_list,
        "adult_id": adult_id,
        "rc_list": rc_list,
    }


def update_delivery(store_entry: dict, fields: dict) -> None:
    """Oppdater delivery-metadata og kalender-gyldighet."""
    delivery = store_entry["data"]["fareDelivery"]["delivery"]
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    allowed = {"deliveryId", "previousDeliveryId", "usage", "optionalDelivery"}
    for key, value in fields.items():
        if key in allowed:
            delivery[key] = value
    calendars = fs.get("calendars", [])
    if calendars and (fields.get("valid_from") or fields.get("valid_until")):
        valid_from = fields.get("valid_from") or calendars[0].get("fromDate", "")
        valid_until = fields.get("valid_until") or calendars[0].get("untilDate", "")
        if valid_from and valid_until:
            set_calendar_validity(calendars, valid_from, valid_until)


def update_passenger_ratio(store_entry: dict, pc_id: str, new_ratio: float) -> dict:
    """
    Endre ratio for en passasjertype og recalkuler alle tilhørende farer.
    Priser = 0 røres ikke. Returnerer dict med antall oppdaterte farer.
    """
    data = store_entry["data"]
    fs = data["fareDelivery"]["fareStructure"]
    adult_id = store_entry["adult_id"]

    if pc_id == adult_id:
        return {"error": "ratio_adult"}  # feilkode, se core/errors.py
    if not (0.0 <= new_ratio <= 2.0):
        return {"error": "ratio_range"}

    prices_list: list = fs["prices"]
    fares_list: list = fs["fares"]
    currency, price_scale = _detect_price_format(prices_list)

    price_map = _build_price_map(prices_list)
    amount_to_price_id = _build_amount_to_price_id(prices_list)

    # Bygg (rc, carrier, sc) → {pc_id: maks_eur} for voksenprisen
    # og (rc, carrier, sc, pc) → [(fare_idx, curr_eur)] for alle farer
    combo_adult_eur: dict[tuple, float] = {}   # gruppenøkkel → høyeste voksenpris
    fare_groups: dict[tuple, list] = {}         # (rc,carrier,sc,pc) → [(idx, curr_eur)]
    for i, fare in enumerate(fares_list):
        if fare.get("reductionConstraintRef"):
            continue
        pc = fare.get("passengerConstraintRef")
        key3 = (
            fare.get("regionalConstraintRef"),
            fare.get("carrierConstraintRef"),
            fare.get("serviceClassRef"),
        )
        if not pc or not key3[0]:
            continue
        curr_eur = price_map.get(fare.get("priceRef", ""), 0.0)
        # Voksenprisen: behold høyeste (primærfare, ikke FIP)
        if pc == adult_id:
            if key3 not in combo_adult_eur or curr_eur > combo_adult_eur[key3]:
                combo_adult_eur[key3] = curr_eur
        # Alle farer for target-PC samles med indeks og nåværende pris
        if pc == pc_id:
            key4 = (key3[0], key3[1], key3[2], pc)
            if key4 not in fare_groups:
                fare_groups[key4] = []
            fare_groups[key4].append((i, curr_eur))

    next_price_num = _max_id_num(prices_list, store_entry["id_prefix"], "I") + 1
    updated_count = 0

    for key3, adult_price_eur in combo_adult_eur.items():
        if adult_price_eur <= 0:
            continue
        key4 = (key3[0], key3[1], key3[2], pc_id)
        group = fare_groups.get(key4, [])
        if not group:
            continue

        base_price_eur = _round_to_20_cents(adult_price_eur * new_ratio)
        max_curr = max(eur for _, eur in group)

        for fare_idx, curr_eur in group:
            # Primærfare (høyest pris) → ny ratio direkte
            # Sekundærfare (FIP etc.) → proporsjonalt skalert
            if max_curr <= 0 or curr_eur >= max_curr:
                new_price_eur = base_price_eur
            else:
                new_price_eur = _round_to_20_cents(base_price_eur * curr_eur / max_curr)

            new_cents = _eur_to_amount_int(new_price_eur, price_scale)
            if new_cents in amount_to_price_id:
                new_price_id = amount_to_price_id[new_cents]
            else:
                new_price_id = f"{store_entry['id_prefix']}I__{next_price_num}"
                next_price_num += 1
                prices_list.append({
                    "id": new_price_id,
                    "price": [{"currency": currency, "amount": new_cents, "scale": price_scale, "vatDetails": []}],
                })
                amount_to_price_id[new_cents] = new_price_id
                price_map[new_price_id] = new_price_eur

            fares_list[fare_idx]["priceRef"] = new_price_id
            updated_count += 1

    store_entry["ratios"][pc_id] = new_ratio
    store_entry["price_map"] = price_map

    return {"updated_fares": updated_count, "new_ratio": new_ratio, "pc_id": pc_id}


def add_relation(
    store_entry: dict,
    from_uic: str,
    to_uic: str,
    adult_price_eur: float,
    distance: int,
) -> dict:
    """
    Legg til ny RC med fares for alle eksisterende kombinasjoner.
    Priser beregnes fra adult_price_eur × ratio, avrundet til 0,20 EUR.
    """
    data = store_entry["data"]
    fs = data["fareDelivery"]["fareStructure"]
    adult_id = store_entry["adult_id"]
    ratios = store_entry["ratios"]
    id_prefix = store_entry["id_prefix"]
    uic_to_cp = store_entry["uic_to_cp"]
    cp_to_uic = store_entry["cp_to_uic"]

    cps_list: list = fs["connectionPoints"]
    rcs_list: list = fs["regionalConstraints"]
    fares_list: list = fs["fares"]
    prices_list: list = fs["prices"]

    currency, price_scale = _detect_price_format(prices_list)
    amount_to_price_id = _build_amount_to_price_id(prices_list)
    next_price_num = _max_id_num(prices_list, id_prefix, "I") + 1

    # Finn eller lag CPs for de to UIC-kodene
    def ensure_cp(uic: str) -> str:
        if uic in uic_to_cp:
            return uic_to_cp[uic]
        next_e = _max_id_num(cps_list, id_prefix, "E") + 1
        cp_id = f"{id_prefix}E__{next_e}"
        country = _country_from_uic(uic)
        cps_list.append({
            "id": cp_id,
            "legacyBorderPointCode": "0",
            "stationSets": [[{"codeList": "UIC", "code": uic, "country": country}]],
        })
        uic_to_cp[uic] = cp_id
        cp_to_uic[cp_id] = uic
        return cp_id

    from_cp_id = ensure_cp(from_uic)
    to_cp_id = ensure_cp(to_uic)

    # Prøv å ta med carrier-info fra første eksisterende RC (varierer per operatør)
    first_rv: dict = {}
    if rcs_list:
        rvs = rcs_list[0].get("regionalValidity", [])
        if rvs:
            first_rv = rvs[0].get("viaStations", {})

    def _make_station_entry(uic: str) -> dict:
        return {
            "isBorder": False,
            "alternativeRoute": [],
            "route": [],
            "station": {"codeList": "UIC", "code": uic, "country": _country_from_uic(uic)},
            "routeValidityType": "LINE",
            "stop": False,
            "technicalViaOnly": False,
        }

    via_stations: dict = {
        "isBorder": False,
        "alternativeRoute": [],
        "route": [_make_station_entry(from_uic), _make_station_entry(to_uic)],
        "routeValidityType": "LINE",
        "stop": False,
        "technicalViaOnly": False,
    }
    # Bevar carrier-referanse fra første RC hvis den finnes
    if "carrierConstraintRef" in first_rv:
        via_stations["carrierConstraintRef"] = first_rv["carrierConstraintRef"]
    elif "carrier" in first_rv:
        via_stations["carrier"] = first_rv["carrier"]

    next_k = _max_id_num(rcs_list, id_prefix, "K") + 1
    new_rc_id = f"{id_prefix}K__{next_k}"
    rcs_list.append({
        "id": new_rc_id,
        "entryConnectionPointId": from_cp_id,
        "exitConnectionPointId": to_cp_id,
        "distance": distance,
        "regionalValidity": [{"seqNb": 1, "viaStations": via_stations}],
    })

    # Generer farer fra malen
    created_fares = 0
    for template in store_entry["fare_templates"]:
        pc_id = template.get("passengerConstraintRef")
        if not pc_id:
            continue

        ratio = ratios.get(pc_id, 0.0)
        if pc_id == adult_id:
            price_eur = _round_to_20_cents(adult_price_eur)
        elif ratio <= 0:
            price_eur = 0.0
        else:
            price_eur = _round_to_20_cents(adult_price_eur * ratio)

        cents = _eur_to_amount_int(price_eur, price_scale)

        if cents in amount_to_price_id:
            price_id = amount_to_price_id[cents]
        else:
            price_id = f"{id_prefix}I__{next_price_num}"
            next_price_num += 1
            prices_list.append({
                "id": price_id,
                "price": [{"currency": currency, "amount": cents, "scale": price_scale, "vatDetails": []}],
            })
            amount_to_price_id[cents] = price_id

        new_fare = dict(template)
        new_fare["id"] = uuid.uuid4().hex
        new_fare["regionalConstraintRef"] = new_rc_id
        new_fare["priceRef"] = price_id
        fares_list.append(new_fare)
        created_fares += 1

    return {
        "rc_id": new_rc_id,
        "from_uic": from_uic,
        "to_uic": to_uic,
        "adult_price_eur": round(adult_price_eur, 2),
        "distance": distance,
        "fares_created": created_fares,
    }


def set_prices_from_adult(store_entry: dict, rc_adult_prices: dict[str, float]) -> dict:
    """
    Sett priser for alle ikke-rabatterte farer i de angitte RC-ene.
    rc_adult_prices: rc_id → ny voksenpris i EUR (samme pris for alle carrier × klasse i RC-en).
    Returnerer statistikk: updated_fares, new_prices.
    """
    fare_groups = _build_fare_groups(store_entry)
    combo_adult: dict[tuple, float] = {}
    for rc, carrier, sc, _ in fare_groups:
        if rc in rc_adult_prices:
            combo_adult[(rc, carrier, sc)] = rc_adult_prices[rc]
    return _apply_adult_prices(store_entry, fare_groups, combo_adult)


def scale_prices(store_entry: dict, factor: float) -> dict:
    """
    Prisregulering: ny voksenpris = gammel × factor (rundet opp) per (RC, carrier, klasse),
    øvrige kategorier fra ratio og FIP o.l. proporsjonalt — samme regler som generering.
    Grupper uten voksenfare skaleres direkte.
    """
    fare_groups = _build_fare_groups(store_entry)
    adult_id = store_entry["adult_id"]
    combo_adult: dict[tuple, float] = {}
    for (rc, carrier, sc, pc), group in fare_groups.items():
        if pc == adult_id:
            combo_adult[(rc, carrier, sc)] = max(eur for _, eur in group) * factor
    return _apply_adult_prices(store_entry, fare_groups, combo_adult, fallback_factor=factor)


def _build_fare_groups(store_entry: dict) -> dict[tuple, list]:
    """(rc, carrier, sc, pc) → liste av (fare_index, nåværende_eur).

    Holder ALLE farer per kombinasjon (f.eks. vanlig voksen + FIP voksen).
    """
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    price_map = {p["id"]: _get_price_eur(p) for p in fs["prices"]}
    fare_groups: dict[tuple, list] = {}
    for i, fare in enumerate(fs["fares"]):
        if fare.get("reductionConstraintRef"):
            continue
        key = (
            fare.get("regionalConstraintRef"),
            fare.get("carrierConstraintRef"),
            fare.get("serviceClassRef"),
            fare.get("passengerConstraintRef"),
        )
        if key[0] is not None and key[3] is not None:
            fare_groups.setdefault(key, []).append((i, price_map.get(fare.get("priceRef", ""), 0.0)))
    return fare_groups


def _apply_adult_prices(
    store_entry: dict,
    fare_groups: dict[tuple, list],
    combo_adult: dict[tuple, float],
    fallback_factor: float | None = None,
) -> dict:
    """Sett nye priser ut fra voksenpris per (rc, carrier, sc).

    Primærfare (høyest nåværende pris) → voksenpris × ratio.
    Sekundærfare (FIP o.l.) → skalert proporsjonalt med primærfaren.
    Med fallback_factor skaleres grupper uten voksenpris direkte.
    Priser som ingen fare lenger bruker etter endringen, fjernes.
    """
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    adult_id = store_entry["adult_id"]
    ratios = store_entry["ratios"]
    id_prefix = store_entry["id_prefix"]
    prices_list: list = fs["prices"]
    fares_list: list = fs["fares"]

    currency, price_scale = _detect_price_format(prices_list)
    amount_to_price_id = _build_amount_to_price_id(prices_list)
    next_price_num = _max_id_num(prices_list, id_prefix, "I") + 1
    updated_fares = 0
    new_prices_count = 0
    replaced_refs: set[str] = set()

    def set_price(fare_idx: int, price_eur: float) -> None:
        nonlocal next_price_num, updated_fares, new_prices_count
        cents = _eur_to_amount_int(price_eur, price_scale)
        price_id = amount_to_price_id.get(cents)
        if price_id is None:
            price_id = f"{id_prefix}I__{next_price_num}"
            next_price_num += 1
            prices_list.append({
                "id": price_id,
                "price": [{"currency": currency, "amount": cents, "scale": price_scale, "vatDetails": []}],
            })
            amount_to_price_id[cents] = price_id
            new_prices_count += 1
        old_ref = fares_list[fare_idx].get("priceRef")
        if old_ref and old_ref != price_id:
            replaced_refs.add(old_ref)
        fares_list[fare_idx]["priceRef"] = price_id
        updated_fares += 1

    for (rc, carrier, sc, pc_id), group in fare_groups.items():
        adult_eur = combo_adult.get((rc, carrier, sc))
        if adult_eur is None:
            if fallback_factor is not None:
                for fare_idx, curr_eur in group:
                    set_price(fare_idx, round_up_020(curr_eur * fallback_factor))
            continue
        if adult_eur <= 0:
            continue

        # Ratioer gjelder den publiserte (avrundede) voksenprisen — det er også den
        # _infer_ratio utleder dem fra. Bruker vi den urundede, driver prisene ved neste generering.
        adult_rounded = round_up_020(adult_eur)
        ratio = ratios.get(pc_id, 0.0)
        if pc_id == adult_id:
            base_price_eur = adult_rounded
        elif ratio <= 0:
            base_price_eur = 0.0
        else:
            base_price_eur = round_up_020(adult_rounded * ratio)

        max_curr_eur = max(eur for _, eur in group)
        for fare_idx, curr_eur in group:
            if base_price_eur <= 0 or max_curr_eur <= 0 or curr_eur >= max_curr_eur:
                price_eur = base_price_eur
            else:
                price_eur = round_up_020(base_price_eur * curr_eur / max_curr_eur)
            set_price(fare_idx, price_eur)

    # Fjern priser vi selv har gjort ubrukt (rører ikke priser som var ubrukt fra før)
    if replaced_refs:
        still_used = {f.get("priceRef") for f in fares_list}
        orphaned = replaced_refs - still_used
        if orphaned:
            prices_list[:] = [p for p in prices_list if p["id"] not in orphaned]

    store_entry["price_map"] = _build_price_map(prices_list)
    return {"updated_fares": updated_fares, "new_prices": new_prices_count}


def apply_delivery_fields(
    store_entry: dict, delivery_id: str, previous_delivery_id: str,
    optional_delivery: str, environment: str, valid_from: str, valid_to: str,
) -> int:
    """Sett delivery-felt og gyldighetsperiode. Felles for generering og prisregulering.
    Returnerer utcOffset for fra-datoen."""
    data = store_entry["data"]
    delivery = data["fareDelivery"]["delivery"]
    delivery["deliveryId"] = delivery_id
    if previous_delivery_id.strip():
        delivery["previousDeliveryId"] = previous_delivery_id.strip()
    else:
        delivery.pop("previousDeliveryId", None)
    delivery["optionalDelivery"] = (optional_delivery.lower() == "true")
    delivery["usage"] = "TEST_ONLY" if environment == "test" else "PRODUCTION"
    calendars = data["fareDelivery"]["fareStructure"].get("calendars", [])
    return set_calendar_validity(calendars, valid_from, valid_to)


def serialize_with_new_prefix(store_entry: dict, delivery_id: str) -> tuple[str, str]:
    """Serialiser og bytt ID-prefix ({fareProvider}_{deliveryId}_) overalt.
    Returnerer (json-tekst, fare_provider)."""
    old_prefix = store_entry["id_prefix"]
    fare_provider = old_prefix.split("_")[0]
    new_prefix = f"{fare_provider}_{delivery_id}_"
    content = json.dumps(store_entry["data"], indent=2, ensure_ascii=False)
    if old_prefix and old_prefix != new_prefix:
        content = content.replace(old_prefix, new_prefix)
    return content, fare_provider


def adult_prices_by_rc(store_entry: dict) -> dict[str, float]:
    """Høyeste voksenpris (EUR) per RC – primærfaren, ikke FIP o.l."""
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    price_map = _build_price_map(fs["prices"])
    result: dict[str, float] = {}
    for fare in fs["fares"]:
        if fare.get("reductionConstraintRef") or fare.get("passengerConstraintRef") != store_entry["adult_id"]:
            continue
        rc = fare.get("regionalConstraintRef")
        eur = price_map.get(fare.get("priceRef", ""), 0.0)
        if rc and eur > result.get(rc, 0.0):
            result[rc] = eur
    return result


def rc_label(store_entry: dict, rc: dict) -> str:
    """«Fra → Til (km)» for en RC, med stasjonsnavn der de finnes."""
    cp_to_uic, names = store_entry["cp_to_uic"], store_entry["station_name_map"]
    from_uic = cp_to_uic.get(rc.get("entryConnectionPointId", ""), "")
    to_uic = cp_to_uic.get(rc.get("exitConnectionPointId", ""), "")
    return (f"{names.get(from_uic, from_uic or '?')} → {names.get(to_uic, to_uic or '?')}"
            f" ({rc.get('distance', '?')} km)")


def price_change_summary(store_entry: dict, before: dict[str, float], limit: int = 5) -> dict:
    """Oppsummering etter prisendring: antall RC-er med endret voksenpris og eksempler «før → etter»."""
    after = adult_prices_by_rc(store_entry)
    rcs = store_entry["data"]["fareDelivery"]["fareStructure"]["regionalConstraints"]
    changed = [rc for rc in rcs if rc["id"] in before and rc["id"] in after
               and abs(after[rc["id"]] - before[rc["id"]]) > 1e-9]
    examples = [f"{rc_label(store_entry, rc)}: {before[rc['id']]:.2f} → {after[rc['id']]:.2f} EUR"
                for rc in changed[:limit]]
    return {"rcsTotal": len(rcs), "rcsChanged": len(changed), "examples": examples}


def serialize_osdm(store_entry: dict) -> bytes:
    """Serialiser endret OSDM til JSON-bytes."""
    return json.dumps(
        store_entry["data"],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
