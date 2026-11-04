"""Feil og advarsler med stabile koder, slik at frontend kan oversette dem.

Serveren sender `{"code": ..., "params": {...}, "message": <norsk tekst>}`.
Frontend viser `t("srv_" + code)` med parametere satt inn (se `translateError` i
i18n.js) og faller tilbake på `message`. Ny kode: legg til norsk tekst her og
`srv_<kode>` på alle fem språk i i18n.js.
"""
from fastapi import HTTPException

# Norske tekster (reserve, logging og tester). Plassholdere: {navn}.
MESSAGES: dict[str, str] = {
    # Tilgang og innlogging
    "not_logged_in": "Ikke innlogget",
    "not_admin": "Ikke administrator",
    "forbidden": "Ikke tilgang",
    "rate_limited": "For mange forespørsler. Prøv igjen senere.",
    "login_rate_limited": "For mange innloggingsforsøk. Prøv igjen om litt.",
    "invalid_login": "Ugyldig innlogging",
    "passwords_mismatch": "Passordene er ikke like",
    "password_too_short": "Passordet må være minst 8 tegn",
    "user_not_found": "Bruker ikke funnet",
    "reset_link_invalid": "Lenken er ugyldig eller utløpt",
    "admin_cannot_delete_account": "Admins kan ikke slette sin egen konto",
    "message_empty": "Meldingen er tom",
    "invalid_email": "Ugyldig e-postadresse",
    "user_exists": "Bruker finnes allerede",
    "cannot_change_own_admin": "Kan ikke endre din egen admin-status",
    "cannot_delete_self": "Kan ikke slette deg selv",
    "request_not_found": "Forespørsel ikke funnet",
    # Filer og jobber
    "file_unreadable": "Filen kunne ikke leses",
    "invalid_json": "Filen er ikke gyldig JSON",
    "missing_fare_structure": "Filen mangler fareDelivery.fareStructure",
    "invalid_osdm": "Filen er ikke en gyldig OSDM fareDelivery",
    "missing_prices": "Filen mangler prisliste (prices)",
    "job_not_found": "Jobben finnes ikke (utløpt?) – prøv igjen",
    "file_not_ready": "Filen er ikke klar ennå",
    "file_not_found": "Filen finnes ikke lenger – kjør operasjonen på nytt",
    "no_result": "Ingen resultatfil tilgjengelig – kjør operasjonen på nytt",
    "internal_error": "Intern feil: {detail}",
    # Priser fra avstandsfil
    "ten_not_validated": "Avstandsfilen er ikke validert",
    "osdm_not_validated": "OSDM-filen er ikke lastet opp og validert",
    "ten_unreadable": "Kunne ikke lese avstandsfilen",
    "ten_empty": "Avstandsfilen er tom",
    "ten_columns": "Linje {line}: feil antall kolonner (forventet 3, fikk {got})",
    "ten_from_not_int": "Linje {line}: «fra»-verdien «{value}» er ikke et heltall",
    "ten_to_not_int": "Linje {line}: «til»-verdien «{value}» er ikke et heltall",
    "ten_price_not_int": "Linje {line}: prisen «{value}» er ikke et heltall",
    "ten_negative_km": "Linje {line}: negative km-verdier er ikke tillatt",
    "ten_from_ge_to": "Linje {line}: «fra» ({from_km}) må være mindre enn «til» ({to_km})",
    "ten_price_not_positive": "Linje {line}: prisen må være større enn 0 (fikk {price})",
    "ten_gap": "Linje {line}: hull eller overlapp i km-intervallene (forrige til={prev_to}, denne fra={from_km})",
    "exchange_rate_failed": "Kunne ikke hente valutakurs fra frankfurter.app",
    # Prisregulering
    "invalid_percent": "Ugyldig prosent",
    # Fare-discount
    "invalid_station_pairs": "Ugyldige stasjonspar",
    "no_regional_constraints": "OSDM-filen inneholder ingen relasjoner (regionalConstraints)",
    "no_rc_for_pairs": "Fant ingen relasjoner for de valgte stasjonsparene",
    "no_matching_fares": "Fant ingen eksisterende farer for valgt kombinasjon av stasjoner, passasjerer og serviceklasse",
    # OSDM-editor
    "no_file_loaded": "Ingen fil lastet – last inn filen på nytt",
    "ratio_missing": "Ratio mangler",
    "ratio_not_number": "Ratio må være et tall",
    "ratio_range": "Ratio må være mellom 0 og 2",
    "ratio_adult": "Kan ikke endre ratio for voksenkategorien",
    "invalid_input": "Ugyldig inndata",
    "price_distance_invalid": "Pris må være større enn 0 og avstand minst 0",
    "bp_not_found": "Grensepunktet finnes ikke",
    "bp_code_invalid": "Grensepunktkoden må være et tall fra 1 til 99999",
    "bp_code_in_use": "Grensepunktkoden {value} er allerede i bruk",
    "bp_uic_invalid": "«{uic}» er ikke en gyldig UIC-kode (7 siffer)",
    "bp_uic_duplicate": "UIC-kode {uic} er oppgitt mer enn én gang",
    "bp_uic_in_use": "Stasjon {uic} ligger allerede i tilkoblingspunkt {cp}",
    "bp_sides_invalid": "Et grensepunkt må ha én eller to sider med stasjoner",
    "rc_not_found": "Relasjonen finnes ikke (lenger) – last inn lista på nytt",
    "rc_distance_invalid": "Avstanden må være et heltall fra 0 til 100000 km",
    "rc_price_invalid": "Voksenprisen må være større enn 0",
    "rc_delete_all": "Du kan ikke slette alle relasjonene – fila må ha minst én",
    "rc_in_use": "Relasjonene brukes også i {where} og kan ikke slettes her",
    "pc_not_found": "Passasjerkategorien finnes ikke",
    "pc_type_invalid": "Ugyldig passasjertype (store bokstaver og _, f.eks. ADULT)",
    "pc_age_invalid": "«{field}» må være et heltall fra 0 til 999",
    "pc_age_order": "Nedre aldersgrense kan ikke være høyere enn øvre",
    "pc_weight_invalid": "Vekten må være et tall fra 0 til 100",
    "pc_name_ref_unknown": "Navnet peker på en tekst som ikke finnes",
    "pc_comb_ref_unknown": "Kombinasjonsregelen peker på ukjent kategori {ref}",
    "pc_comb_duplicate": "Kategori {ref} står flere ganger i kombinasjonsreglene",
    "pc_comb_invalid": "Kombinasjonsregel: min og maks må være heltall fra 0 til 999, og min kan ikke være større enn maks",
    "pc_not_sibling": "{pc} har ikke samme navn og type og kan ikke endres samtidig",
    "w_pc_age_order": "{pc}: nedre aldersgrense ({lower}) er høyere enn øvre ({upper})",
    "w_pc_comb_ref_unknown": "{pc}: kombinasjonsregel peker på ukjent kategori {ref}",
    "w_pc_age_overlap": "{bundle}: aldersintervallene overlapper – {a} og {b}",
    "w_pc_age_gap": "{bundle}: ingen personkategori dekker alder {from}–{to}",
    "text_not_found": "Teksten finnes ikke",
    "text_empty": "Teksten kan ikke være tom",
    "text_language_invalid": "«{language}» er ikke en gyldig språkkode (to bokstaver, f.eks. en)",
    "text_translation_empty": "Oversettelsen for {language} mangler tekst",
    "text_language_duplicate": "Samme språk er oppgitt flere ganger",
    "station_name_empty": "Stasjonsnavnet kan ikke være tomt",
    "bp_code_elsewhere": "Stasjonsnavn med samme grensepunktkode som ikke ligger i punktet: {stations}",
    "bp_route_mismatch": "Relasjonsruter starter eller slutter på stasjoner som ikke ligger i punktet: {stations}",
    # Valideringsadvarsler (vises som liste)
    "w_unknown_ref": "{count} fare(r) peker på ukjent {field} i {section}: {examples}",
    "w_bad_cp_refs": "Relasjoner med ugyldig referanse til tilkoblingspunkt: {examples}",
    "w_unused_prices": "{count} pris(er) er definert men ikke brukt av noen fare",
    "w_unused_pcs": "{count} passasjerkategori(er) er definert men ikke brukt av noen fare",
    "w_unused_rcs": "{count} relasjon(er) er definert men ikke brukt av noen fare",
    "w_distance_uncovered": "{count} avstand(er) i OSDM-filen dekkes ikke av avstandsfilen: {examples}",
    "w_distance_max": "OSDM-filen har ruter opp til {osdm_max} km, men avstandsfilen slutter på {ten_max} km",
}


def error_payload(code: str, **params) -> dict:
    """Feil/advarsel som JSON: kode, parametere og norsk reservetekst.

    `extra` (antall utelatte eksempler) legges til som « (+ N til)» i teksten.
    """
    message = MESSAGES[code].format(**params)
    if params.get("extra"):
        message += f" (+ {params['extra']} til)"
    return {"code": code, "params": params, "message": message}


class AppError(HTTPException):
    """HTTP-feil med oversettbar kode: `raise AppError(400, "invalid_json")`."""

    def __init__(self, status_code: int, code: str, **params):
        super().__init__(status_code=status_code, detail=error_payload(code, **params))
