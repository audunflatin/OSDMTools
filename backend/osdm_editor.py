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
    border_by_code = {
        _border_code(cp): cp["id"] for cp in cps_list if _border_code(cp) != "0"
    }
    names_by_uic = _station_names_by_uic(fs.get("stationNames", []))

    def ensure_cp(uic: str) -> str:
        if uic in uic_to_cp:
            return uic_to_cp[uic]
        # Grensestasjon (f.eks. Storlien(Gr) med kode 541): bruk grensepunktet
        # med samme kode, slik eksisterende relasjoner gjør.
        sn = names_by_uic.get(uic)
        code = str(sn.get("legacyBorderPointCode", 0)) if sn else "0"
        if code != "0" and code in border_by_code:
            return border_by_code[code]
        next_e = _next_cp_num(fs, id_prefix)
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


# ---------------------------------------------------------------------------
# Grensepunkter
# ---------------------------------------------------------------------------
#
# Et grensepunkt er et connectionPoint med legacyBorderPointCode ≠ "0". Det har
# som regel to stationSets: stasjonen på hver side av grensen (1076: Kornsjø
# 7600551 / 7400731). Ferjehavner (0083: Ancona, Brindisi) har bare én side.
# Koden står også i stationNames (som tall), og relasjonsrutene starter/slutter
# på en av stasjonene i punktet.

_UIC_RE = re.compile(r"^\d{7}$")
_BORDER_CODE_RE = re.compile(r"^\d{1,5}$")


def _border_code(cp: dict) -> str:
    return str(cp.get("legacyBorderPointCode", "0") or "0").strip() or "0"


def _is_border_cp(cp: dict) -> bool:
    return _border_code(cp) != "0"


def _station_name_uic(s: dict) -> str:
    """UIC for en stationNames-oppføring (flatt `code` eller Trenitalias country+localCode)."""
    if "code" in s:
        return str(s["code"])
    try:
        return str(int(s["country"]) * 100000 + int(s["localCode"]))
    except (KeyError, TypeError, ValueError):
        return ""


def _station_names_by_uic(station_names: list) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for s in station_names:
        uic = _station_name_uic(s)
        if uic and uic not in result:
            result[uic] = s
    return result


def _next_cp_num(fs: dict, prefix: str) -> int:
    """Neste ledige E-nummer. fulfillmentConstraints deler E-prefikset med
    connectionPoints (1076: E__150, 0083: E__797), så begge må telles med."""
    return max(
        _max_id_num(fs.get("connectionPoints", []), prefix, "E"),
        _max_id_num(fs.get("fulfillmentConstraints", []), prefix, "E"),
    ) + 1


def _cp_uics(cp: dict) -> list[str]:
    return [str(s["code"]) for ss in cp.get("stationSets", []) for s in ss if s.get("codeList") == "UIC"]


def _refresh_station_maps(store_entry: dict) -> None:
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    store_entry["uic_to_cp"], store_entry["cp_to_uic"] = _build_cp_maps(fs.get("connectionPoints", []))
    store_entry["station_name_map"] = _build_station_name_map(fs.get("stationNames", []))


def list_border_points(store_entry: dict) -> list[dict]:
    """Grensepunkter med stasjoner per side, antall relasjoner og advarselskoder."""
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    cps = [cp for cp in fs.get("connectionPoints", []) if _is_border_cp(cp)]
    if not cps:
        return []
    names = _station_names_by_uic(fs.get("stationNames", []))
    ids = {cp["id"] for cp in cps}

    rc_count = {cid: 0 for cid in ids}
    route_ends: dict[str, set] = {cid: set() for cid in ids}
    for rc in fs.get("regionalConstraints", []):
        route = []
        rvs = rc.get("regionalValidity") or []
        if rvs:
            route = (rvs[0].get("viaStations") or {}).get("route") or []
        for key, pos in (("entryConnectionPointId", 0), ("exitConnectionPointId", -1)):
            cid = rc.get(key)
            if cid in ids:
                rc_count[cid] += 1
                if route:
                    code = (route[pos].get("station") or {}).get("code")
                    if code:
                        route_ends[cid].add(str(code))

    result = []
    for cp in cps:
        code = _border_code(cp)
        uics = set(_cp_uics(cp))
        sides = []
        for ss in cp.get("stationSets", []):
            side = []
            for s in ss:
                uic = str(s.get("code", ""))
                sn = names.get(uic)
                side.append({
                    "uic": uic,
                    "country": s.get("country", ""),
                    "name": (sn.get("nameUtf8") or sn.get("name") or "") if sn else "",
                })
            sides.append(side)

        warnings = []
        # Stasjonsnavn som har denne koden, men ikke ligger i punktet
        elsewhere = [
            f'{(s.get("nameUtf8") or s.get("name") or uic)} ({uic})'
            for uic, s in names.items()
            if str(s.get("legacyBorderPointCode", 0)) == code and uic not in uics
        ]
        if elsewhere:
            warnings.append({"code": "bp_code_elsewhere", "params": {"stations": ", ".join(elsewhere)}})
        # Rutene starter/slutter på en stasjon som ikke er i punktet
        outside = sorted(route_ends[cp["id"]] - uics)
        if outside:
            labels = [f'{(names.get(u) or {}).get("nameUtf8") or (names.get(u) or {}).get("name") or u} ({u})' for u in outside]
            warnings.append({"code": "bp_route_mismatch", "params": {"stations": ", ".join(labels)}})

        result.append({
            "id": cp["id"],
            "code": code,
            "sides": sides,
            "rc_count": rc_count[cp["id"]],
            "warnings": warnings,
        })
    result.sort(key=lambda b: int(b["code"]) if b["code"].isdigit() else 0)
    return result


def _validate_border_input(fs: dict, code: str, sides: list, own_cp_id: str | None) -> tuple[str, list[list[str]]]:
    """Sjekk kode og stasjoner. Returnerer (kode, sider) eller kaster BorderPointError."""
    code = str(code or "").strip()
    if not _BORDER_CODE_RE.match(code) or int(code) == 0:
        raise BorderPointError("bp_code_invalid")
    for cp in fs.get("connectionPoints", []):
        if cp["id"] != own_cp_id and _border_code(cp) == code:
            raise BorderPointError("bp_code_in_use", value=code)

    clean: list[list[str]] = []
    seen: set[str] = set()
    for side in sides or []:
        uics = [str(u).strip() for u in (side or []) if str(u).strip()]
        if not uics:
            continue
        for u in uics:
            if not _UIC_RE.match(u):
                raise BorderPointError("bp_uic_invalid", uic=u)
            if u in seen:
                raise BorderPointError("bp_uic_duplicate", uic=u)
            seen.add(u)
        clean.append(uics)
    if not clean or len(clean) > 2:
        raise BorderPointError("bp_sides_invalid")
    return code, clean


class EditorError(Exception):
    """Feil med oversettbar kode (se core/errors.py); routeren gjør den om til AppError."""

    def __init__(self, error_code: str, **params):
        super().__init__(error_code)
        self.code = error_code
        self.params = params


BorderPointError = EditorError


def _station_entry(uic: str, existing: dict[str, dict]) -> dict:
    if uic in existing:
        return dict(existing[uic])
    return {"codeList": "UIC", "code": uic, "country": _country_from_uic(uic)}


def _set_station_names(fs: dict, names: dict[str, str]) -> None:
    """Sett/legg til stasjonsnavn (fra grensepunktskjemaet). Tomme navn hoppes over."""
    for uic, name in (names or {}).items():
        uic = str(uic).strip()
        name = str(name or "").strip()
        if name and _UIC_RE.match(uic):
            _upsert_station_name(fs, uic, name)


def _check_uics_free(fs: dict, uics: list[str], allowed_cp_ids: set[str]) -> None:
    for cp in fs.get("connectionPoints", []):
        if cp["id"] in allowed_cp_ids:
            continue
        for u in _cp_uics(cp):
            if u in uics:
                raise BorderPointError("bp_uic_in_use", uic=u, cp=cp["id"])


def update_border_point(store_entry: dict, cp_id: str, code: str, sides: list, names: dict | None = None) -> dict:
    """Endre kode og stasjoner for et grensepunkt.

    Stasjonsnavn med den gamle koden får den nye. Relasjonsrutene endres ikke.
    """
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    cp = next((c for c in fs.get("connectionPoints", []) if c["id"] == cp_id), None)
    if cp is None or not _is_border_cp(cp):
        raise BorderPointError("bp_not_found")
    old_code = _border_code(cp)
    code, clean = _validate_border_input(fs, code, sides, cp_id)
    _check_uics_free(fs, [u for side in clean for u in side], {cp_id})

    existing = {str(s.get("code")): s for ss in cp.get("stationSets", []) for s in ss}
    cp["legacyBorderPointCode"] = code
    cp["stationSets"] = [[_station_entry(u, existing) for u in side] for side in clean]

    if code != old_code:
        for sn in fs.get("stationNames", []):
            if str(sn.get("legacyBorderPointCode", 0)) == old_code:
                sn["legacyBorderPointCode"] = int(code)
    _set_station_names(fs, names or {})
    _refresh_station_maps(store_entry)
    return {"id": cp_id, "code": code}


def add_border_point(store_entry: dict, code: str, sides: list, names: dict | None = None) -> dict:
    """Legg til et grensepunkt.

    Ligger stasjonen på første side allerede i et vanlig punkt (kode 0), gjøres
    det punktet om til grensepunkt, slik at relasjonene som bruker det beholdes.
    """
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    code, clean = _validate_border_input(fs, code, sides, None)
    cps: list = fs.setdefault("connectionPoints", [])

    first = clean[0][0]
    target = next((c for c in cps if first in _cp_uics(c)), None)
    if target is not None and _is_border_cp(target):
        raise BorderPointError("bp_uic_in_use", uic=first, cp=target["id"])
    allowed = {target["id"]} if target is not None else set()
    _check_uics_free(fs, [u for side in clean for u in side], allowed)

    if target is None:
        prefix = store_entry["id_prefix"]
        target = {"id": f"{prefix}E__{_next_cp_num(fs, prefix)}"}
        cps.append(target)
        converted = False
    else:
        converted = True
    existing = {str(s.get("code")): s for ss in target.get("stationSets", []) for s in ss}
    target["legacyBorderPointCode"] = code
    target["stationSets"] = [[_station_entry(u, existing) for u in side] for side in clean]

    _set_station_names(fs, names or {})
    # Grensestasjonen på første side bærer koden i stationNames (som Kornsjø 543)
    by_uic = _station_names_by_uic(fs.get("stationNames", []))
    for u in clean[0]:
        sn = by_uic.get(u)
        if sn is not None and str(sn.get("legacyBorderPointCode", 0)) == "0":
            sn["legacyBorderPointCode"] = int(code)
    _refresh_station_maps(store_entry)
    return {"id": target["id"], "code": code, "converted": converted}


# ---------------------------------------------------------------------------
# Tekster og stasjonsnavn
# ---------------------------------------------------------------------------
#
# OSDM 3.1-filer har både `text` (ASCII, for 108.1-konvertering) og `textUtf8`;
# 7.0 har bare `text` (UTF-8). Tilsvarende `name`/`nameUtf8` i stationNames.
# 1076 skriver «Meraaker» og «Barn 6-17 year» i ASCII-feltene.

_ASCII_MAP = {
    "å": "aa", "Å": "Aa", "æ": "ae", "Æ": "Ae", "ø": "oe", "Ø": "Oe",
    "ä": "ae", "Ä": "Ae", "ö": "oe", "Ö": "Oe", "ü": "ue", "Ü": "Ue", "ß": "ss",
    "–": "-", "—": "-", "’": "'", "‘": "'", "«": '"', "»": '"', "“": '"', "”": '"', "„": '"',
}
_LANG_RE = re.compile(r"^[a-z]{2}$")


def ascii_fold(text: str) -> str:
    """ASCII-versjon av en tekst: æøå/äöü → ae/oe/aa/ue, øvrige aksenter fjernes."""
    import unicodedata
    s = "".join(_ASCII_MAP.get(ch, ch) for ch in text)
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if ord(ch) < 128 and not unicodedata.combining(ch))


def _walk_refs(obj, ids: set, section: str, field: str | None, counts: dict) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            _walk_refs(v, ids, section, k, counts)
    elif isinstance(obj, list):
        for v in obj:
            _walk_refs(v, ids, section, field, counts)
    elif isinstance(obj, str) and obj in ids:
        key = (obj, f"{section}.{field}")
        counts[key] = counts.get(key, 0) + 1


# Seksjoner som aldri peker på tekster – hoppes over for fart (store i DB-filer)
_NO_TEXT_REFS = {"texts", "prices", "regionalConstraints", "connectionPoints", "stationNames",
                 "fareReferenceStationSetDefinitions"}


def list_texts(store_entry: dict) -> list[dict]:
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    texts = fs.get("texts", [])
    ids = {t["id"] for t in texts if t.get("id")}
    counts: dict[tuple, int] = {}
    for section, items in fs.items():
        if section not in _NO_TEXT_REFS:
            _walk_refs(items, ids, section, None, counts)
    used: dict[str, list] = {}
    for (tid, where), n in sorted(counts.items(), key=lambda kv: kv[0][1]):
        used.setdefault(tid, []).append({"where": where, "count": n})

    result = []
    for t in texts:
        legacy = "textUtf8" in t or "shortTextUtf8" in t
        result.append({
            "id": t.get("id", ""),
            "legacy_ascii": legacy,
            "text": (t.get("textUtf8") if legacy else t.get("text")) or "",
            "short_text": (t.get("shortTextUtf8") if legacy else t.get("shortText")) or "",
            "text_ascii": t.get("text", "") if legacy else "",
            "short_text_ascii": t.get("shortText", "") if legacy else "",
            "translations": [
                {"language": tr.get("language", ""), "text": tr.get("text", ""), "shortText": tr.get("shortText", "")}
                for tr in t.get("translations") or []
            ],
            "used_by": used.get(t.get("id", ""), []),
        })
    return result


def update_text(store_entry: dict, text_id: str, fields: dict) -> dict:
    """Oppdater en tekst. Tomme ASCII-felt fylles med ASCII-versjonen av UTF-8-teksten,
    tom korttekst blir lik teksten (slik filene gjør i dag)."""
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    t = next((x for x in fs.get("texts", []) if x.get("id") == text_id), None)
    if t is None:
        raise EditorError("text_not_found")
    text = str(fields.get("text") or "").strip()
    if not text:
        raise EditorError("text_empty")
    short = str(fields.get("shortText") or "").strip() or text

    translations = []
    for tr in fields.get("translations") or []:
        if not isinstance(tr, dict):
            raise EditorError("invalid_input")
        lang = str(tr.get("language") or "").strip().lower()
        tr_text = str(tr.get("text") or "").strip()
        if not lang and not tr_text:
            continue
        if not _LANG_RE.match(lang):
            raise EditorError("text_language_invalid", language=lang or "–")
        if not tr_text:
            raise EditorError("text_translation_empty", language=lang)
        entry = {"language": lang, "text": tr_text}
        tr_short = str(tr.get("shortText") or "").strip()
        if tr_short:
            entry["shortText"] = tr_short
        translations.append(entry)
    langs = [tr["language"] for tr in translations]
    if len(langs) != len(set(langs)):
        raise EditorError("text_language_duplicate")

    if "textUtf8" in t or "shortTextUtf8" in t:
        t["textUtf8"] = text
        t["shortTextUtf8"] = short
        t["text"] = ascii_fold(str(fields.get("textAscii") or "").strip() or text)
        t["shortText"] = ascii_fold(str(fields.get("shortTextAscii") or "").strip() or short)
    else:
        t["text"] = text
        t["shortText"] = short
    t["translations"] = translations
    store_entry["text_map"] = _build_text_map(fs.get("texts", []))
    return {"id": text_id}


def _referenced_station_uics(fs: dict) -> set[str]:
    """UIC-koder som brukes i tilkoblingspunkter eller relasjonsruter."""
    uics = {u for cp in fs.get("connectionPoints", []) for u in _cp_uics(cp)}

    def walk_route(route):
        for r in route or []:
            code = (r.get("station") or {}).get("code")
            if code:
                uics.add(str(code))
            walk_route(r.get("route"))
            for alt in r.get("alternativeRoute") or []:
                walk_route(alt.get("route") if isinstance(alt, dict) else alt)

    for rc in fs.get("regionalConstraints", []):
        for rv in rc.get("regionalValidity") or []:
            walk_route((rv.get("viaStations") or {}).get("route"))
    return uics


def _upsert_station_name(fs: dict, uic: str, name: str, short_name: str = "") -> bool:
    """Sett navn på en stasjon; lager ny oppføring i filens format. True = ny."""
    station_names: list = fs.setdefault("stationNames", [])
    sn = _station_names_by_uic(station_names).get(uic)
    created = sn is None
    if created:
        italian_format = bool(station_names) and "code" not in station_names[0]
        sn = {"country": int(uic[:2]), "localCode": int(uic[2:])} if italian_format else {"code": uic}
        sn.update({"name": "", "nameUtf8": "", "shortName": "", "shortNameUtf8": "", "legacyBorderPointCode": 0})
        station_names.append(sn)
        short_name = short_name or name
    if "nameUtf8" in sn or created:
        sn["nameUtf8"] = name
        sn["name"] = ascii_fold(name)
    else:
        sn["name"] = name
    if short_name:
        if "shortNameUtf8" in sn or created:
            sn["shortNameUtf8"] = short_name
            sn["shortName"] = ascii_fold(short_name)
        else:
            sn["shortName"] = short_name
    return created


def list_station_names(store_entry: dict, query: str = "", missing_only: bool = False, limit: int = 200) -> dict:
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    names = _station_names_by_uic(fs.get("stationNames", []))
    referenced = _referenced_station_uics(fs)
    q = (query or "").strip().casefold()

    rows = []
    for uic in set(names) | referenced:
        sn = names.get(uic)
        row = {
            "uic": uic,
            "name": (sn.get("nameUtf8") or sn.get("name") or "") if sn else "",
            "short_name": (sn.get("shortNameUtf8") or sn.get("shortName") or "") if sn else "",
            "border_code": str(sn.get("legacyBorderPointCode", 0)) if sn else "0",
            "in_file": sn is not None,
            "used": uic in referenced,
        }
        if missing_only and row["in_file"]:
            continue
        if q and q not in uic and q not in row["name"].casefold() and q not in row["short_name"].casefold():
            continue
        rows.append(row)
    rows.sort(key=lambda r: (r["in_file"], r["name"].casefold(), r["uic"]))
    short_lengths = [len(s.get("shortNameUtf8") or s.get("shortName") or "") for s in names.values()]
    return {
        "stations": rows[:limit],
        "total": len(rows),
        "truncated": len(rows) > limit,
        "missing_count": sum(1 for u in referenced if u not in names),
        "short_max": max(short_lengths) if short_lengths else 0,
    }


def update_station_name(store_entry: dict, uic: str, name: str, short_name: str = "") -> dict:
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    uic = str(uic or "").strip()
    if not _UIC_RE.match(uic):
        raise EditorError("bp_uic_invalid", uic=uic)
    name = str(name or "").strip()
    if not name:
        raise EditorError("station_name_empty")
    created = _upsert_station_name(fs, uic, name, str(short_name or "").strip())
    _refresh_station_maps(store_entry)
    return {"uic": uic, "created": created}


# ---------------------------------------------------------------------------
# Passasjerkategorier (passengerConstraints)
# ---------------------------------------------------------------------------
#
# 1076 har parvise kopier av samme kategori: G__1/G__2 (voksen) og G__3/G__4
# (barn) – én for vanlige billetter (bundle S__1) og én for grupper (S__2).
# Like aldersgrenser for samme type er derfor normalt; kontrollene ser på
# kategoriene som brukes i samme bundle.

PASSENGER_TYPES = [
    "ADULT", "SENIOR", "YOUTH", "CHILD", "YOUNG_CHILD", "FAMILY_CHILD", "PERSON",
    "PRM", "WHEELCHAIR", "DOG", "COMPANION_DOG", "PET", "BICYCLE", "PRAM",
    "LUGGAGE", "CAR", "MOTORCYCLE", "TRAILER",
]
# Typer som ikke er personer – tas ikke med i alderskontrollene
_NON_PERSON_TYPES = {"DOG", "COMPANION_DOG", "PET", "BICYCLE", "PRAM", "LUGGAGE",
                     "CAR", "MOTORCYCLE", "TRAILER", "WHEELCHAIR"}
_TYPE_RE = re.compile(r"^[A-Z][A-Z_]{1,39}$")
_AGE_FIELDS = ("lowerAgeLimit", "upperAgeLimit", "ageLimitToTravelAlone", "ageLimitForReservation")


def _pc_usage(fs: dict) -> tuple[dict[str, int], dict[str, set]]:
    fare_count: dict[str, int] = {}
    bundles: dict[str, set] = {}
    for f in fs.get("fares", []):
        pc = f.get("passengerConstraintRef")
        if pc:
            fare_count[pc] = fare_count.get(pc, 0) + 1
            if f.get("bundleRef"):
                bundles.setdefault(pc, set()).add(f["bundleRef"])
    return fare_count, bundles


def _short_id(item_id: str) -> str:
    """«1076_7.0_G__1» → «G__1» (for meldinger)."""
    m = re.search(r"[A-Z]__\d+$", item_id or "")
    return m.group(0) if m else item_id


def _pc_label(pc: dict, text_map: dict) -> str:
    name = text_map.get(pc.get("nameRef", ""), "") or pc.get("passengerType", "")
    return f"{name} ({_short_id(pc['id'])})"


def passenger_warnings(store_entry: dict) -> list[dict]:
    """Overlappende aldersintervaller og hull mellom personkategorier i samme bundle,
    og kombinasjonsregler som peker på ukjente kategorier."""
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    pcs = fs.get("passengerConstraints", [])
    by_id = {pc["id"]: pc for pc in pcs}
    text_map = store_entry.get("text_map", {})
    _, bundles = _pc_usage(fs)
    warnings: list[dict] = []

    for pc in pcs:
        lo, hi = pc.get("lowerAgeLimit"), pc.get("upperAgeLimit")
        if isinstance(lo, int) and isinstance(hi, int) and lo > hi:
            warnings.append({"code": "w_pc_age_order", "params": {"pc": _pc_label(pc, text_map), "lower": lo, "upper": hi}})
        for cc in pc.get("combinationConstraint") or []:
            ref = cc.get("passengerConstraintRef")
            if ref and ref not in by_id:
                warnings.append({"code": "w_pc_comb_ref_unknown", "params": {"pc": _pc_label(pc, text_map), "ref": ref}})

    bundle_pcs: dict[str, list] = {}
    for pc_id, bs in bundles.items():
        for b in bs:
            if pc_id in by_id:
                bundle_pcs.setdefault(b, []).append(by_id[pc_id])
    for bundle in sorted(bundle_pcs):
        persons = [
            pc for pc in bundle_pcs[bundle]
            if pc.get("passengerType") not in _NON_PERSON_TYPES and not pc.get("isAncillaryItem")
            and isinstance(pc.get("lowerAgeLimit"), int) and isinstance(pc.get("upperAgeLimit"), int)
            and pc["lowerAgeLimit"] <= pc["upperAgeLimit"]
        ]
        persons.sort(key=lambda pc: (pc["lowerAgeLimit"], pc["upperAgeLimit"]))
        short_bundle = _short_id(bundle)
        for i, a in enumerate(persons):
            for b in persons[i + 1:]:
                if b["lowerAgeLimit"] > a["upperAgeLimit"]:
                    break
                if a.get("passengerType") != b.get("passengerType"):
                    warnings.append({"code": "w_pc_age_overlap", "params": {
                        "bundle": short_bundle,
                        "a": f'{_pc_label(a, text_map)} {a["lowerAgeLimit"]}–{a["upperAgeLimit"]}',
                        "b": f'{_pc_label(b, text_map)} {b["lowerAgeLimit"]}–{b["upperAgeLimit"]}',
                    }})
        covered = -1
        for pc in persons:
            if covered >= 0 and pc["lowerAgeLimit"] > covered + 1:
                warnings.append({"code": "w_pc_age_gap", "params": {
                    "bundle": short_bundle, "from": covered + 1, "to": pc["lowerAgeLimit"] - 1}})
            covered = max(covered, pc["upperAgeLimit"])
    return warnings


def passenger_details(store_entry: dict) -> list[dict]:
    """Alle felt editoren kan endre, pluss bruk (farer, bundles) og «søsken»
    (kategorier med samme navn og type, som 1076s G__1/G__2)."""
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    pcs = fs.get("passengerConstraints", [])
    fare_count, bundles = _pc_usage(fs)
    result = []
    for pc in pcs:
        siblings = [
            o["id"] for o in pcs
            if o["id"] != pc["id"] and o.get("nameRef") == pc.get("nameRef")
            and o.get("passengerType") == pc.get("passengerType")
        ]
        result.append({
            "id": pc["id"],
            "nameRef": pc.get("nameRef", ""),
            "passengerType": pc.get("passengerType", ""),
            **{f: pc.get(f) for f in _AGE_FIELDS},
            "passengerWeight": pc.get("passengerWeight"),
            "isAncillaryItem": bool(pc.get("isAncillaryItem", False)),
            "combinationConstraint": [
                {"passengerConstraintRef": c.get("passengerConstraintRef", ""),
                 "minNumber": c.get("minNumber", 0), "maxNumber": c.get("maxNumber", 0)}
                for c in pc.get("combinationConstraint") or []
            ],
            "fare_count": fare_count.get(pc["id"], 0),
            "bundles": sorted(bundles.get(pc["id"], [])),
            "siblings": siblings,
        })
    return result


def _int_field(fields: dict, key: str, lo: int, hi: int, error: str) -> int:
    v = fields.get(key)
    if isinstance(v, bool):
        raise EditorError(error, field=key)
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise EditorError(error, field=key)
    if str(v).strip() not in (str(n), f"{n}.0") or not lo <= n <= hi:
        raise EditorError(error, field=key)
    return n


def update_passenger(store_entry: dict, pc_id: str, fields: dict, also_ids: list | None = None) -> dict:
    """Endre type, aldersgrenser, vekt, navn og kombinasjonsregler for en kategori.

    `also_ids`: kategorier med samme navn og type som skal få samme type og
    aldersgrenser (vekt og kombinasjonsregler gjelder bare `pc_id`).
    Ratio og priser røres ikke.
    """
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    pcs = fs.get("passengerConstraints", [])
    by_id = {pc["id"]: pc for pc in pcs}
    pc = by_id.get(pc_id)
    if pc is None:
        raise EditorError("pc_not_found")

    ptype = str(fields.get("passengerType") or "").strip().upper()
    if not _TYPE_RE.match(ptype):
        raise EditorError("pc_type_invalid")
    ages = {f: _int_field(fields, f, 0, 999, "pc_age_invalid") for f in _AGE_FIELDS}
    if ages["lowerAgeLimit"] > ages["upperAgeLimit"]:
        raise EditorError("pc_age_order")
    try:
        weight = float(fields.get("passengerWeight"))
    except (TypeError, ValueError):
        raise EditorError("pc_weight_invalid")
    if not 0 <= weight <= 100 or weight != weight:
        raise EditorError("pc_weight_invalid")

    name_ref = str(fields.get("nameRef") or pc.get("nameRef") or "")
    text_ids = {t.get("id") for t in fs.get("texts", [])}
    if name_ref and name_ref not in text_ids:
        raise EditorError("pc_name_ref_unknown")

    combos = []
    seen = set()
    for c in fields.get("combinationConstraint") or []:
        if not isinstance(c, dict):
            raise EditorError("invalid_input")
        ref = str(c.get("passengerConstraintRef") or "")
        if ref not in by_id:
            raise EditorError("pc_comb_ref_unknown", ref=ref or "–")
        if ref in seen:
            raise EditorError("pc_comb_duplicate", ref=ref)
        seen.add(ref)
        lo = _int_field(c, "minNumber", 0, 999, "pc_comb_invalid")
        hi = _int_field(c, "maxNumber", 0, 999, "pc_comb_invalid")
        if lo > hi:
            raise EditorError("pc_comb_invalid", field="minNumber")
        combos.append({"maxNumber": hi, "minNumber": lo, "passengerConstraintRef": ref})

    targets = [pc]
    for other_id in also_ids or []:
        other = by_id.get(other_id)
        if other is None or other_id == pc_id:
            continue
        if other.get("nameRef") != pc.get("nameRef") or other.get("passengerType") != pc.get("passengerType"):
            raise EditorError("pc_not_sibling", pc=other_id)
        targets.append(other)

    for t in targets:
        t["passengerType"] = ptype
        t.update(ages)
    if name_ref:
        pc["nameRef"] = name_ref
    pc["passengerWeight"] = weight
    pc["isAncillaryItem"] = bool(fields.get("isAncillaryItem", False))
    pc["combinationConstraint"] = combos
    return {"id": pc_id, "updated": [t["id"] for t in targets]}


# ---------------------------------------------------------------------------
# Relasjoner (regionalConstraints): søk, endre avstand/voksenpris, slette
# ---------------------------------------------------------------------------

_MAX_DISTANCE = 100000
_MAX_PRICE_EUR = 100000.0


def _rc_endpoint(store_entry: dict, cp_id: str, border_codes: dict) -> dict:
    uic = store_entry["cp_to_uic"].get(cp_id, "")
    return {
        "uic": uic,
        "name": store_entry["station_name_map"].get(uic, "") or uic,
        "border": border_codes.get(cp_id, ""),
    }


def list_relations(store_entry: dict, query: str = "", offset: int = 0, limit: int = 100) -> dict:
    """Relasjoner med stasjoner, avstand, voksenpris og antall farer.

    Søket deles i ord; alle ordene må finnes i fra/til (navn eller UIC) eller
    relasjonens ID. «oslo bergen» finner altså Oslo S → Bergen.
    """
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    border_codes = {cp["id"]: _border_code(cp) for cp in fs.get("connectionPoints", []) if _is_border_cp(cp)}
    fare_count: dict[str, int] = {}
    for f in fs.get("fares", []):
        rc = f.get("regionalConstraintRef")
        if rc:
            fare_count[rc] = fare_count.get(rc, 0) + 1
    adult = adult_prices_by_rc(store_entry)
    terms = [t for t in (query or "").casefold().split() if t]
    all_rcs = fs.get("regionalConstraints", [])
    # Eksakt ID gir bare den relasjonen («K__1» skal ikke treffe K__10, K__100 …)
    exact = [rc for rc in all_rcs if len(terms) == 1 and rc.get("id", "").casefold() == terms[0]]
    if exact:
        all_rcs, terms = exact, []

    rows = []
    for rc in all_rcs:
        frm = _rc_endpoint(store_entry, rc.get("entryConnectionPointId", ""), border_codes)
        to = _rc_endpoint(store_entry, rc.get("exitConnectionPointId", ""), border_codes)
        if terms:
            hay = " ".join([rc["id"], frm["uic"], frm["name"], to["uic"], to["name"]]).casefold()
            if not all(t in hay for t in terms):
                continue
        rows.append({
            "id": rc["id"],
            "from": frm,
            "to": to,
            "distance": rc.get("distance"),
            "fare_count": fare_count.get(rc["id"], 0),
            "adult_eur": adult.get(rc["id"]),
        })
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 500))
    return {
        "relations": rows[offset:offset + limit],
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "rc_count": len(fs.get("regionalConstraints", [])),
    }


def update_relation(store_entry: dict, rc_id: str, distance=None, adult_eur=None) -> dict:
    """Endre avstand og/eller voksenpris for én relasjon.

    Ny voksenpris går via set_prices_from_adult (samme regler som generering:
    øvrige kategorier = avrundet voksenpris × ratio, FIP proporsjonalt).
    """
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    rc = next((r for r in fs.get("regionalConstraints", []) if r.get("id") == rc_id), None)
    if rc is None:
        raise EditorError("rc_not_found")

    new_distance = None
    if distance not in (None, ""):
        try:
            new_distance = int(str(distance).strip())
        except ValueError:
            raise EditorError("rc_distance_invalid")
        if not 0 <= new_distance <= _MAX_DISTANCE:
            raise EditorError("rc_distance_invalid")
    new_price = None
    if adult_eur not in (None, ""):
        try:
            new_price = float(str(adult_eur).replace(",", ".").strip())
        except ValueError:
            raise EditorError("rc_price_invalid")
        if not 0 < new_price <= _MAX_PRICE_EUR:
            raise EditorError("rc_price_invalid")

    if new_distance is not None:
        rc["distance"] = new_distance
    result = {"id": rc_id, "distance": rc.get("distance"), "updated_fares": 0}
    if new_price is not None:
        before = adult_prices_by_rc(store_entry).get(rc_id)
        stats = set_prices_from_adult(store_entry, {rc_id: new_price})
        result["updated_fares"] = stats["updated_fares"]
        result["adult_before"] = before
        result["adult_after"] = adult_prices_by_rc(store_entry).get(rc_id)
    return result


def delete_relations(store_entry: dict, rc_ids: list) -> dict:
    """Slett relasjoner med farene sine.

    Rydder også det slettingen gjør ubrukt: priser og vanlige tilkoblingspunkter.
    Grensepunkter, stasjonsnavn og ting som var ubrukt fra før røres ikke.
    """
    fs = store_entry["data"]["fareDelivery"]["fareStructure"]
    rcs: list = fs.get("regionalConstraints", [])
    ids = {str(i) for i in rc_ids or []}
    if not ids:
        raise EditorError("invalid_input")
    known = {r["id"] for r in rcs}
    unknown = sorted(ids - known)
    if unknown:
        raise EditorError("rc_not_found")
    if ids == known:
        raise EditorError("rc_delete_all")
    # Andre seksjoner som peker på relasjonene (finnes ikke i 1076/0083, men
    # skjemaet utelukker det ikke) – da sletter vi ikke.
    counts: dict = {}
    for section, items in fs.items():
        if section not in ("regionalConstraints", "fares"):
            _walk_refs(items, ids, section, None, counts)
    if counts:
        where = sorted({w for (_, w) in counts})
        raise EditorError("rc_in_use", where=", ".join(where))

    fares: list = fs.get("fares", [])
    removed_fares = [f for f in fares if f.get("regionalConstraintRef") in ids]
    fares[:] = [f for f in fares if f.get("regionalConstraintRef") not in ids]

    removed_rcs = [r for r in rcs if r["id"] in ids]
    rcs[:] = [r for r in rcs if r["id"] not in ids]

    # Priser som bare de slettede farene brukte
    candidate_prices = {f.get("priceRef") for f in removed_fares if f.get("priceRef")}
    still_used = {f.get("priceRef") for f in fares}
    orphan_prices = candidate_prices - still_used
    prices: list = fs.get("prices", [])
    if orphan_prices:
        prices[:] = [p for p in prices if p["id"] not in orphan_prices]

    # Vanlige tilkoblingspunkter som bare de slettede relasjonene brukte
    candidate_cps = {r.get(k) for r in removed_rcs for k in ("entryConnectionPointId", "exitConnectionPointId")}
    used_cps = {r.get(k) for r in rcs for k in ("entryConnectionPointId", "exitConnectionPointId")}
    cps: list = fs.get("connectionPoints", [])
    orphan_cps = {
        cp["id"] for cp in cps
        if cp["id"] in candidate_cps and cp["id"] not in used_cps and not _is_border_cp(cp)
    }
    if orphan_cps:
        cps[:] = [cp for cp in cps if cp["id"] not in orphan_cps]

    store_entry["price_map"] = _build_price_map(prices)
    store_entry["fare_templates"] = _get_fare_templates(fares, rcs)
    _refresh_station_maps(store_entry)
    return {
        "deleted_rcs": len(removed_rcs),
        "deleted_fares": len(removed_fares),
        "deleted_prices": len(orphan_prices),
        "deleted_cps": len(orphan_cps),
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
