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
