# CLAUDE.md – PopToOSDM

Rask referanse for Claude. Detaljert arkitektur og kontekst: `CONTEXT_PopToOSDM.md`.

---

## Slik starter du lokalt

```bash
.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

## Tester

```bash
.venv/bin/pip install -r requirements-dev.txt   # første gang
.venv/bin/python -m pytest -q tests
```

`tests/test_pricing.py` bruker 1076-malen som fasit (avrunding, ratioer, ingen prisdrift, FIP-proporsjoner). `tests/test_api.py` kjører mot en midlertidig SQLite-database, aldri `users.db`. **Kjør testene etter hver endring i prislogikken.**

`tests/test_auth_utils.py` sikrer at gamle passord-hasher (passlib) fortsatt verifiseres. Kjør også `.venv/bin/python -m pyflakes backend` — den fanger udefinerte navn etter flytting av kode.

---

## Backend-struktur

```
backend/
  main.py            — kun app-oppsett: lifespan, middleware (sesjon/DB-sjekk, CORS), /static, include_router
  routers/           — én fil per tjeneste, hver med `router = APIRouter()`
    public.py        — /, /personvern, robots/sitemap, /kontakt, /contact, /request-access
    auth.py          — login/logout, passordbytte/glemt/reset, /min-konto, /account
    admin.py         — /admin/* (brukere, forespørsler, logger, presentasjon)
    generate.py      — priser fra avstandsfil (/ui/*: TEN, validering, generering, nedlasting)
    excel.py         — OSDM → Excel (`osdm_to_xlsx_bytes`, `start_xlsx_job`)
    price_adjust.py, fare_discount.py, fix_osdm.py, editor.py, station.py
  core/
    security.py      — require_login/require_admin, _rate_limit, _valid_email, _safe_filename, log_event
    state.py         — alle stores i minnet + opprydding + jobbhjelpere (create_job, owned_job, start_file_job, file_job_progress)
    pages.py         — serve_page(request, "fil.html", admin=False, extra={...})
    osdm_bytes.py    — byte-nivå JSON (_find_section_span …), require_fare_delivery, read_delivery, _ijson_default
    settings.py      — miljøvariabler
  osdm_editor.py     — all prislogikk (ratioer, avrunding, set_prices_from_adult, scale_prices, delivery-felt)
  auth_db.py, auth_utils.py, email_utils.py, rics_codes.py
```

## Viktig: Når du legger til en ny tjeneste

**Hver gang en ny side/tjeneste legges til, MÅ følgende oppdateres:**

0. **Sidebar** — legg lenken til i `frontend/partials/sidebar.html` (én fil for alle sider). Sider med `<!-- SIDEBAR -->` og `<!-- APP_HEADER -->` får sidebar og toppfelt satt inn av `serve_page`; aktiv lenke markeres ut fra URL-stien, og `<!-- ADMIN -->`-blokken fjernes for ikke-admins (ingen JS-toggling).

0b. **Backend** — lag `backend/routers/<tjeneste>.py` med `router = APIRouter()`, server siden med `serve_page(request, "<side>.html")`, og legg modulen til i `include_router`-løkka nederst i `backend/main.py`. Tunge operasjoner: `def`-endepunkt + `create_job`/`start_file_job` fra `core/state.py`. Legg til tester i `tests/`.

1. **`landing.html`** — legg til et nytt feature-kort (`.feat-card`) med passende SVG-ikon, `data-i18n`-tittel og -beskrivelse. Legg til de tilhørende i18n-nøklene i alle 5 språk i `i18n.js`.

2. **`presentation.js`** — legg til et nytt slide i `getSlides()`-arrayen med beskrivelse av tjenesten på alle 5 språk (no, en, de, sv, fr), og bump `presentation.js`-versjonen i `presentation.html`.

## Designkrav som alltid gjelder

**Felles frontend-hjelpere i `common.js`** (lastes etter `i18n.js`, før side-JS) — brukes av alle verktøysidene (avstandsfil, prisregulering, OSDM → Excel, fare-discount, rydd opp, editor):
- Opplasting: `showFileInfo(file)` (filnavn/størrelse + 5000 MB-sjekk i `#fileInfo`), `uploadWithProgress` + `pollJob` (jobb-endepunkter), `uploadAndWait(url, fd, progressId, stage)` (synkrone endepunkter: opplasting 0–50 %, så «stage» mot 95 %; returnerer `{ok, data}` med `data.error` fra FastAPI-`detail`), `validateOsdmUpload`.
- Fremdrift: `<div id="…" class="upload-progress" style="display:none;"></div>` — innholdet fylles av common.js; styr med `showProgress(id, pct, stage)` / `hideProgress(id)`. Stegtekst = `validate_stage_<stage>` i i18n (uploading, reading, validating, converting, processing).
- Meldinger: `renderOsdmSummary` (`.info-box`), `renderWarnings` (`.warnings-box`), `renderError` (`.status-error`), `setValidationError` (`#validationError`), `showResult(text, ok)`/`hideResult` (`#resultBox` + `#finalStatus`; første linje er overskrift), `deliverySummaryLines`, `exportExcel(startUrl)`.

**Mobil:** alle sider (unntatt presentasjonen) har `<meta name="viewport">`. Under 800 px blir sidebaren en uttrekksmeny bak ☰ (`.menu-toggle` i `partials/header.html`, `body.nav-open`), skjema går til én kolonne og `.data-table` ruller sidelengs. Mobil-regler ligger samlet nederst i `styles.css`. Nye sider trenger viewport-taggen.

**Ingen inline-stiler.** Bruk klasser i `styles.css` (tabeller: `.data-table` med `.muted`/`.center`/`.right`, søk: `.search-input`/`.filter-row`/`.filter-control`, meldinger: `.success-msg`/`.validation-error`, m.fl.). Kun `style="display:none;"` som starttilstand er greit. Sider uten tema-støtte (landing, innlogging, personvern, presentasjon) er bevisst alltid mørke.

**Escape alt fra serveren i innerHTML** (`escapeHtml`). Aktivitetsloggen viste e-post fra mislykket innlogging og data fra tilgangsskjemaet uescapet — lagret XSS mot admin (rettet nov. 2026).

Verktøy som lager en OSDM-fil (generering, prisregulering, fare-discount) viser resultatboks + «Last ned oppdatert OSDM JSON» + «Last ned Excel». Hovedknapp: `.btn-primary`. Beskrivelse under tittelen: `.panel-desc`. Ingen hardkodede farger i side-CSS — bruk tema-variablene (`var(--text)`, `var(--surface)` …), ellers blir siden feil i lyst tema.

**Progress bar (ikke spinner) på alle filopplastingssider.**
Nye sider med filopplasting og tung backend-prosessering skal bruke en styled progress bar med prosent og steg-tekst (`.progress-fill`, `.progress-pct`, `.progress-stage`), ikke en spinner. Se `fare-discount.html` / `osdmtoexcel.html` for HTML-mønster og `fareDiscount.js` for JS-mønster.

**Filstørrelsesgrense = 5000 MB overalt.**
Ikke bruk betingede grenser basert på `localhost` vs. produksjon. Deutsche Bahn har OSDM-filer på ~1,2 GB. Grensen skal alltid være `const maxMb = 5000;`.

**Tung prosessering skal bruke async job-mønster.**
Nye endepunkter som gjør tung prosessering (JSON-parse av store filer, validering, konvertering) skal starte en bakgrunnstråd og returnere et `jobId` som frontend poller via et eget progress-endepunkt. Bruk hjelperne i `backend/core/state.py`: `start_file_job` + `file_job_progress` (validering/parsing) eller `create_job` + `owned_job` (egen tråd, se `start_xlsx_job` i `routers/excel.py`).

**Tunge endepunkter skal være `def`, aldri `async def`.** FastAPI kjører `async def` på hendelsesløkken — tung synkron kode der fryser hele serveren for alle brukere. Bruk `fil.file.read()` i stedet for `await fil.read()`. Trenger du `await request.json()`, behold `async def` og kjør tungt arbeid med `await run_in_threadpool(...)`. `test_heavy_endpoints_are_sync` vokter dette.

---

## Sider og ansvarlige filer

| URL | HTML | JS | Funksjon |
|---|---|---|---|
| `/` (uinnlogget) | `landing.html` | — | Landingsside med info og tilgangsforespørsel |
| `/login` | `login.html` | — | Innlogging |
| `/` (innlogget) | `index.html` | `app.js` | Priser fra avstandsfil (hovedflyt) |
| `/price-adjust` | `price-adjust.html` | `priceAdjust.js` | Prisregulering (skaler priser med fast %) |
| `/osdmtoexcel` | `osdmtoexcel.html` | `osdmtoExcel.js` | OSDM JSON → Excel |
| `/fare-discount` | `fare-discount.html` | `fareDiscount.js` | Legg til rabatterte priser i eksisterende OSDM |
| `/fix-osdm` | `fix-osdm.html` | `fixOsdm.js` | Rydd opp i OSDM (fjern ubrukte elementer) |
| `/osdm-editor` | `osdm-editor.html` | `osdmEditor.js` | Rediger passasjerprofiler, metadata, relasjoner i OSDM |
| `/stasjonssok` | `station-lookup.html` | `stationLookup.js` | Søk opp jernbanestasjoner via Wikidata (navn/UIC-kode) |
| `/admin/users` | `admin.html` | `admin.js` | Brukerhåndtering (kun admin) |
| `/admin/log` | `admin-log.html` | `admin-log.js` | Aktivitetslogg (kun admin) |
| `/kontakt` | `contact.html` | — | Kontaktskjema |
| `/min-konto` | `min-konto.html` | — | Min konto (endre navn, passord, slett konto) |
| `/endre-passord` | — | — | 301-redirect til /min-konto |

Felles styling: `styles.css`. Flerspråklig: `i18n.js` (lastes før side-spesifikk JS).

---

## Global server-state (viktig å kjenne til)

Definert i `backend/core/state.py`:

```python
TEN_STORE: dict                # user_email → {"table": [(fra_km, til_km, pris)], "created_at": float}
OSDM_STORE: dict               # user_email → {"filename": str, "content": str, "created_at": float}
ADJUST_STORE: dict             # user_email → {"filename": str, "content": str, "created_at": float} (prisregulering)
DISCOUNT_STORE: dict           # user_email → {"filename": str, "content": bytes, "created_at": float} (fare-discount)
INPUT_OSDM_STORE: dict         # user_email → {"filename": str, "bytes": bytes, "created_at": float}
FIX_OSDM_STORE: dict           # user_email → {"filename": str, "content": bytes, "created_at": float}
EDIT_STORE: dict               # user_email → osdm_editor store entry + {"filename": str, "created_at": float}
XLSX_JOBS: dict                # job_id → {status, result, percent, user_email, ...}
VALIDATION_JOBS: dict          # job_id → {status, result, percent, user_email, ...}
PARSE_JOBS: dict               # job_id → {status, result, percent, user_email, ...}
GENERATION_PROGRESS: dict      # user_email → {"status", "percent", "created_at"} for progressbar
```

**Viktig:** All tilstand er per bruker (keyed på `user_email`) — også `TEN_STORE` og `GENERATION_PROGRESS` (var felles før november 2026). `OSDM_STORE`, `INPUT_OSDM_STORE` og `FIX_OSDM_STORE` er per bruker (keyed på `user_email`) — to brukere kan generere OSDM samtidig uten konflikt. Alle job-dicts lagrer `user_email`; progress- og download-endepunkter sjekker eierskap (403 hvis annen bruker).

**Konsekvens:** TEN-filen og OSDM-malfilen må valideres i riktig rekkefølge per server-sesjon.
Ingenting skrives til disk under generering.

En bakgrunnstråd rydder alle stores eldre enn 2 timer hvert 10. minutt (`XLSX_JOBS`, `VALIDATION_JOBS`, `PARSE_JOBS`, `OSDM_STORE`, `INPUT_OSDM_STORE`, `FIX_OSDM_STORE`, `EDIT_STORE`, `TEN_STORE`, `GENERATION_PROGRESS`).

---

## Prisregulering – endepunkter

| Kall | Handling |
|---|---|
| `GET /price-adjust` | Serverer `price-adjust.html` |
| `POST /price-adjust` | Mottar OSDM-fil + %, beregner nye priser, oppdaterer delivery-felt, lagrer i `ADJUST_STORE[user_email]`, returnerer `{ok, outputFile, summary}` (samme format som generering) |
| `GET /price-adjust/download/{filename}` | Laster ned regulert fil |
| `POST /price-adjust/excel` | Starter Excel-jobb av regulert fil (poll via `/frontend/osdm-to-csv-status/{jobId}`) |

Parametere: `osdm_file`, `pct`, `delivery_id`, `previous_delivery_id`, `environment`, `optional_delivery`, `valid_from`, `valid_to`.
Algoritme (november 2026): samme kode som generering — `osdm_editor.scale_prices()`. Ny voksenpris = gammel voksenpris × `(1 + pct/100)` per (RC, carrier, klasse), rundet opp; øvrige kategorier = avrundet voksenpris × eksakt ratio; FIP o.l. proporsjonalt med primærfaren. Grupper uten voksenfare skaleres direkte. Delivery-felt og ID-prefix settes med `_apply_delivery_fields` / `_serialize_with_new_prefix`, felles med generering. Leser hele filen inn i minnet (se fallgruver).

Tidligere grupperte prisregulering på `fareConstraintBundleRef` (feltet heter `bundleRef`) og endret priser på stedet — priser delt mellom relasjoner med ulik voksenpris fikk verdien fra siste relasjon.

---

## Priser fra avstandsfil – flyt og endepunkter

| # | Kall | Handling |
|---|---|---|
| 1 | `POST /ui/validate-ten` | Parser TEN-CSV, lagrer i `TEN_STORE[user_email]` |
| 2 | `POST /ui/validate-osdm` | Validerer struktur, lagrer råbytes i `INPUT_OSDM_STORE[user_email]`, returnerer warnings + deliveryId |
| 3 | `GET /ui/exchange-rate?from_=EUR&to=NOK` | Henter kurs fra frankfurter.app (ECB) |
| 4 | `POST /ui/generate-osdm` | Bruker `TEN_STORE[user_email]` + `INPUT_OSDM_STORE[user_email]` (ingen hardkodet template), lagrer i `OSDM_STORE[user_email]` |
| 5 | `GET /ui/download-osdm/{filename}` | Serverer `OSDM_STORE[user_email]` (krever innlogging) |
| 6 | `POST /ui/excel-from-generated` | Konverterer `OSDM_STORE[user_email]` til Excel (async) |

Generering og prisregulering returnerer også `rcsTotal`, `rcsChanged` og `priceExamples` (voksenpris «før → etter» for inntil 5 relasjoner) fra `osdm_editor.price_change_summary()`; frontend viser dem med `priceChangeLines()` i common.js.

Genereringsresultatet rapporterer `rcsSkipped` / `rcsSkippedExamples`: RC-er uten avstand eller utenfor avstandsfilen beholder malens gamle priser, og frontend viser en advarsel.

### Delivery-felt som settes ved generering
- `deliveryId` — ny ID (brukes i alle ID-er via string-replace)
- `previousDeliveryId` — forrige leveranse-ID (valgfri)
- `usage` — `TEST_ONLY` / `PRODUCTION`
- `optionalDelivery` — boolean
- `calendars[].fromDate/untilDate` + `utcOffset` — gyldighetsperiode

### Valutakurs
- Brukervalgt valuta for avstandsfilen (standard: EUR)
- `exchangeRate` = EUR per filvaluta-enhet (eksempel NOK: ~0,087)
- EUR gir kurs = 1 (ingen konvertering)
- Støttede valutaer: EUR, NOK, SEK, DKK, GBP, CHF, BGN, CZK, HUF, ISK, PLN, RON, TRY

---

## Fare-discount – flyt og endepunkter

| Kall | Handling |
|---|---|
| `POST /fare-discount/parse` | Parser OSDM-fil, returnerer stasjoner/transportører/passasjerkategorier |
| `POST /fare-discount/apply` | Legger til rabatterte farer, lagrer i `DISCOUNT_STORE[user_email]`, returnerer `{ok, outputFile, summary}` |
| `GET /fare-discount/download/{filename}` / `POST /fare-discount/excel` | Last ned / Excel-eksport av resultatet |
| `GET /fare-discount/rics` | Liste over alle RICS-transportørkoder (for dropdown) |

### `/fare-discount/apply` – nøkkelparametere
- `stationPairsJson` — JSON-streng: `[{fromCpId, toCpId, fromUic, toUic}, …]` (støtter mange par)
- `discountName`, `discountPct` — navn og prosent (1–99)
- `carrierCodes[]` — valgfri liste med RICS-koder (tom = ingen begrensning)
- `passengerRefs[]`, `serviceClassIds[]` — hvilke kategorier og klasser rabatten gjelder

Backend samler RC-er fra **alle** par og lager priser for samtlige kombinasjoner av RC × passasjerkategori × serviceklasse.

Nye rabattfarer:
- Bygger på **primærfaren** (høyest pris) per (RC, PC, klasse), ikke første fare — ellers kan FIP-prisen bli grunnlaget.
- Kopierer `legacyAccountingIdentifier`, `legacyConversion`, `individualContracts` og `involvedTCOs` fra grunnlagsfaren (SilverRail trenger legacy-feltene).
- Uten valgte transportører beholdes grunnlagsfarens `carrierConstraintRef`; med valgte lages en ny carrierConstraint.

---

## OSDM-validering – typer advarsler

`POST /ui/validate-osdm` returnerer warnings for:
- Farer som peker på ukjent `priceRef`, `passengerConstraintRef`, `regionalConstraintRef`, `carrierConstraintRef`, `bundleRef`, `nameRef`
- RC-er med ugyldig `entryConnectionPointId` / `exitConnectionPointId`
- Ubrukte `prices`, `passengerConstraints`, `regionalConstraints`

`POST /ui/fix-osdm` fikser **alle** disse automatisk — se eget avsnitt nedenfor.

---

## Rydd opp i OSDM – flyt og endepunkter

To-stegs flyt: analyser → bekreft → last ned.

| Kall | Handling |
|---|---|
| `POST /ui/fix-osdm` | Analyserer filen, lagrer resultat i `FIX_OSDM_STORE[user_email]`, returnerer JSON `{stats, filename}` |
| `GET /ui/fix-osdm/download` | Serverer lagret resultat fra `FIX_OSDM_STORE[user_email]` |

Frontend (`fixOsdm.js`): viser oppsummering av hva som vil bli fjernet, med "Last ned fikset fil"-knapp som trigges av brukeren. Resultatet ryddes fra `FIX_OSDM_STORE` etter 2 timer.

**`ijson` parser tall som `decimal.Decimal`** — alle `json.dumps`-kall i fix_osdm bruker `default=_ijson_default` for å konvertere Decimal til int/float.

---

## OSDM-editor – flyt og endepunkter

`backend/osdm_editor.py` inneholder all logikk; `EDIT_STORE[user_email]` holder parse-resultat per bruker.

| Kall | Handling |
|---|---|
| `POST /osdm-editor/load` | Parser OSDM-fil, lagrer store_entry i `EDIT_STORE[user_email]`, returnerer summary |
| `GET /osdm-editor/summary` | Returnerer summary fra eksisterende `EDIT_STORE[user_email]` |
| `POST /osdm-editor/metadata` | Oppdaterer delivery-felt + kalenderperiode i store_entry |
| `POST /osdm-editor/passenger/{pc_id}` | Setter ny ratio for en passasjerkategori, rekalkuderer alle priser for den kategorien |
| `POST /osdm-editor/relation` | Legger til ny relasjon (RC + fares for alle passasjerkategorier × serviceklasser) |
| `GET /osdm-editor/download` | Serialiserer og returnerer endret OSDM-fil |

### `osdm_editor.load_osdm(content: bytes)` — returnerer store_entry dict med:
- `data` — hele den parsede JSON (muteres av alle operations)
- `adult_id` — PC-id for voksen (høyest gjennomsnittspris basert på primærfarer)
- `ratios` — `{pc_id: float}` — utledet fra eksisterende priser
- `price_map` — `{price_id: float}` — for rask pris-lookup (EUR)
- `uic_to_cp`, `cp_to_uic` — UIC ↔ CP-ID maps
- `station_name_map` — UIC → stasjonsnavn
- `text_map` — tekst-ID → tekst
- `id_prefix` — f.eks. `"1076_7.0_"` eller `"0083_3.1_"`
- `fare_templates` — liste over fare-strukturer for `add_relation`

### `osdm_editor.set_prices_from_adult(store_entry, rc_adult_prices)` og `scale_prices(store_entry, factor)`
Begge går via `_apply_adult_prices` (generering bruker den første, prisregulering den andre):
- **Kategoripriser = ratio × den avrundede voksenprisen** — samme grunnlag som `_infer_ratio` utleder fra. Bruker man urundet voksenpris, driver ratioene ved neste generering (f.eks. 0,9003).
- Priser som blir ubrukt etter endringen fjernes (priser som var ubrukt fra før røres ikke).

Detaljer for `set_prices_from_adult`:
- Tar `{rc_id: adult_eur}` og setter priser for alle PC × SC-kombinasjoner
- Håndterer filer med **duplikat-farer per (RC, carrier, sc, PC)** — se avsnittet nedenfor
- Runder opp til nærmeste 0,20 EUR (DRTF-krav)
- Returnerer `{updated_fares, new_prices}`

---

## OSDM-struktur – nøkkelkonsepter

ID-mønster: `{fareProvider}_{deliveryId}_{typekode}__{nr}` — typekoder: E=connectionPoint, K=regionalConstraint, I=price, G=passengerConstraint, C=carrierConstraint, S=bundle

**Ratio-innsikt (viktig):** Ratio er IKKE lagret i OSDM-filer — den utledes fra passasjerkategoriens pris og voksenprisen. **Ikke bruk snittet av pris/voksen:** prisene er rundet opp, så snittet blir skjevt oppover (0,2516 i stedet for 0,25) og gir prisdrift ved hver generering (rettet november 2026). `_infer_ratio` bruker at `pris − 0,20 < voksen × r ≤ pris` for alle relasjoner, og velger den «runde» verdien i fellesmengden av intervallene. 1076: barn 0,25, honnør 0,5, gruppevoksen 0,9 (bekreftet av brukeren). Voksen = passasjerkategori med høyest gjennomsnittlig pris (beregnet fra primærfarer, ikke FIP-farer). `osdm_editor.load_osdm()` gjør dette automatisk og lagrer `adult_id` og `ratios` i store_entry.

**stationNames-format varierer per operatør:**
- De fleste: `{"code": "8311705", "codeList": "UIC", ...}`
- Trenitalia: `{"country": 83, "localCode": 11705, ...}` → UIC = `str(country * 100000 + localCode)`

### fareStructure-seksjoner (alle nøkler)
`calendars`, `serviceClassDefinitions`, `texts`, `prices`,
`regionalConstraints`, `carrierConstraints`, `passengerConstraints`,
`fareConstraintBundles`, `passengerCombinationConstraints`, `fares`,
`salesAvailabilityConstraint`, `travelValidityConstraints`,
`combinationConstraints`, `fulfillmentConstraints`,
`connectionPoints`, `stationNames`

---

## OSDM – duplikat-farer per (RC, carrier, sc, PC): FIP og lignende

**Dette er det viktigste nye vi lærte om OSDM-formatet (juni 2026).**

NSB 1076-filen (og trolig andre operatørers filer) har **to eller flere farer for samme (RC, carrierConstraintRef, serviceClassRef, passengerConstraintRef)**-kombinasjon, typisk:

- `nameRef = P__7` / PC = `G__1` → vanlig voksenpris, f.eks. 102,40 EUR (**primærfare**)
- `nameRef = P__5` / PC = `G__1` → FIP-rabattert voksenpris, f.eks. 60,40 EUR (**sekundærfare**)

Begge har **ingen `reductionConstraintRef`** — de ser ut som vanlige farer.

Tilsvarende for barn (PC = `G__3`): én normal barnepris og én FIP-barnepris.

### Konsekvenser for prislogikken i `osdm_editor.py`

Alle fire funksjoner som leser farer må ta høyde for dette mønsteret:

| Funksjon | Gammel feil | Løsning |
|---|---|---|
| `_find_adult_id` | FIP-priser fortynnet snittet → G__2 (gruppevoksen, 0,9×) ble valgt som "voksen" pga høyere gjennomsnitt | Bruk maks-pris per (rc, carrier, sc, pc)-gruppe; gjennomsnitt av maks-priser per PC |
| `_compute_ratios` | FIP-pris overskrev primærpris → feil ratioer for alle PC-er | Behold kun høyeste pris per PC per gruppe (`if eur > groups[key][pc_id]`) |
| `set_prices_from_adult` | `fare_index[key] = i` (dict) → siste fare (FIP) overskrev første (primær) → kun FIP-faren ble oppdatert; primærfaren (vanlig voksen) ble aldri endret | Bruk `fare_groups[key] = list of (idx, curr_eur)` → oppdater alle farer proporsjonalt: primærfare (høyest nåværende pris) → ny TEN-pris; sekundærfare (FIP) → ny TEN-pris × (FIP_current / primary_current) |
| `update_passenger_ratio` | Samme dict-overskriving + feil voksenprisbasis | Samme proporsjonale tilnærming som set_prices_from_adult |

### Proporsjonalitetsregelen (viktig):
Når en (rc, carrier, sc, pc)-gruppe har flere farer, skal sekundærfarer skaleres proporsjonalt:
```
ny_sekundærpris = ny_primærpris × (gammel_sekundærpris / gammel_primærpris)
```
Eksempel: FIP-voksen var 60,40 av 102,40 EUR (ratio ≈ 0,59). Ny voksenpris = 108,60 EUR → ny FIP-voksen = 108,60 × 0,59 ≈ 64,20 EUR (rundet opp til 0,20 EUR).

### `_get_fare_templates` (for `add_relation`):
Denne bruker `(pc, carrier, sc)` som nøkkel og beholder første forekomst (`if key in seen: continue`). Nye relasjoner vil derfor kun få én fare per PC (primærfaren) — FIP-varianter kopieres ikke. Dette er akseptabelt for add_relation-funksjonen.

---

## Tilgangsforespørsel – endepunkt

`POST /request-access` (åpent, ingen innlogging nødvendig):
- Rate limiting: maks 3 forespørsler per IP per 24 timer (`_access_requests` i `core/security.py`)
- Honeypot-felt (`website`) — bots avvises stille med 200 OK
- Sender e-post til admin via `send_access_request_email()` i `email_utils.py`
- Krever `CONTACT_EMAIL`-miljøvariabel

---

## Cache-busting – gjeldende versjoner

| Fil | Versjon |
|---|---|
| `styles.css` | v=32 |
| `i18n.js` | v=62 (alle sider, også innloggingssidene) |
| `app.js` | v=28 |
| `admin.js` | v=23 |
| `admin-log.js` | v=5 |
| `osdmtoExcel.js` | v=9 |
| `fareDiscount.js` | v=21 |
| `priceAdjust.js` | v=13 |
| `fixOsdm.js` | v=4 |
| `osdmEditor.js` | v=5 |
| `stationLookup.js` | v=2 |
| `presentation.js` | v=8 |
| `common.js` | v=5 (alle verktøysider) |

Ved endringer i statiske filer: bump versjonsnummeret i **alle**
HTML-filer som laster den aktuelle filen.

Alle HTML-filer laster `i18n.js` med samme versjon (v=62) — bump alle samtidig.

---

## Feilmeldinger fra serveren – koder, ikke tekst

Serveren sender aldri ferdig tekst til brukeren. Bruk `AppError(status, "kode", **params)` (HTTP-feil) eller `error_payload("kode", **params)` (feltet `error` i JSON, jobbfeil, advarsler) fra `backend/core/errors.py`. Svaret blir `{code, params, message}` der `message` er norsk reserve.
Frontend viser `translateError(err)` (i `i18n.js`) → `t("srv_" + code)` med `{param}` satt inn; `params.extra` gir « (+ N til)». `errorDetail`, `pollJob` og `renderWarnings` i common.js gjør dette automatisk.
**Ny kode:** legg til norsk tekst i `MESSAGES` og `srv_<kode>` på alle fem språk — `test_all_error_codes_are_translated_in_frontend` feiler ellers.
Innlogging er et vanlig skjema: feil gir redirect til `/login?error=<kode>` (ikke JSON).

## i18n – legge til ny tekst

**All synlig tekst skal alltid språkstyres.** Ingen hardkodede strenger i HTML eller JS.

1. Bruk `data-i18n="nøkkel"` i HTML eller `t("nøkkel")` i JS
2. For placeholder-tekst: `data-i18n-placeholder="nøkkel"` på input-elementet
3. For title-attributter: `data-i18n-title="nøkkel"` på elementet
4. For toggle-knapper med skjult radio-input: wrap teksten i `<span data-i18n="nøkkel">`
5. Legg til nøkkelen i **alle fem språk** i `i18n.js` (no, en, de, sv, fr)
6. Bump `i18n.js`-versjon overalt

Dynamiske etiketter (f.eks. valutakurs-label) bruker `data-i18n`-attributten
og oppdateres via JS — se `updateExchangeRateLabel()` i `app.js`.

---

## Sikkerhet – viktige mekanismer

- **Passord**: PBKDF2-SHA256 i passlib-formatet, implementert med `hashlib` i `auth_utils.py` (passlib fjernet nov. 2026). Nye hasher: 600 000 runder; eldre (29 000) oppgraderes automatisk ved innlogging (`needs_rehash`).
- **SESSION_SECRET**: logger advarsel ved oppstart hvis standardverdi brukes (ikke satt i env).
- **Sesjonen sjekkes mot DB ved hver forespørsel** (`validate_session_user`-middleware i `backend/main.py`): slettet/deaktivert bruker logges ut umiddelbart, `is_admin` i sesjonen oppdateres, og `must_change_password` sperrer alt unntatt `_PASSWORD_CHANGE_ALLOWED` (GET → redirect til `/change-password`, ellers 403). Middlewaren må registreres **før** `SessionMiddleware` i koden (Starlette legger sist registrerte ytterst).
- **Session-cookie**: `max_age` 7 dager (`SESSION_MAX_AGE`), `Secure`-flagg på Railway (`SESSION_HTTPS_ONLY`, auto via `RAILWAY_ENVIRONMENT_NAME`), av lokalt.
- **E-post**: all brukerinput escapes med `_esc()` i `email_utils.py`, og emnefelt renses med `_subject_safe()`. Nye e-postmaler må gjøre det samme.
- **Ingen inline `onclick` med data fra brukere** — admin.js bruker `data-action`/`data-email` + én delegert click-listener. `escapeHtml` i en `onclick`-streng beskytter ikke (HTML-entiteter dekodes før JS kjøres). E-post valideres på serveren med `_valid_email()` i `/request-access` og `/admin/add-user`.
- **CORS**: `ALLOWED_ORIGINS` begrenser til `APP_URL` + localhost (settes i `backend/core/settings.py`).
- **Job-eierskap**: alle job-dicts (`XLSX_JOBS`, `VALIDATION_JOBS`, `PARSE_JOBS`) lagrer `user_email`. Progress- og download-endepunkter returnerer 403 hvis feil bruker spør.
- **Job-cleanup**: bakgrunnstråd sletter jobber eldre enn 2 timer hvert 10. minutt.
- **`_safe_filename()`**: fjerner tegn som kan ødelegge `Content-Disposition`-headere. Brukes i `fix_osdm`, `price_adjust` og `osdm-to-csv-download`.
- **`OSDM_STORE`**: per-bruker dict — to brukere kan generere OSDM samtidig uten å overskrive hverandres resultat.
- **Rate limiting**: 10 innloggingsforsøk per IP per 60 sek; 3 tilgangsforespørsler per IP per 24 t; 5 «glemt passord» per IP per time; 5 kontaktmeldinger per bruker per time (`/contact` krever innlogging). Felles hjelper: `_rate_limit(store, nøkkel, vindu, maks)`.
- **SameSite=strict** på session-cookie; `CF-Connecting-IP` for korrekt IP bak Cloudflare.

---

## Kjente fallgruver

- **`TEN_STORE` er tom etter serverrestart** — brukeren må validere TEN-filen på nytt. Tilsvarende for `INPUT_OSDM_STORE`. Dette er by design (stateless storage mellom requests, men state lever i server-prosessen).
- **`import requests` var lenge glemt** i `main.py` (lagt til mai 2026). Valutahenting feilet stille.
- **Priser rundes opp til nærmeste 0,20 EUR** — DRTF-krav. **Bruk alltid `osdm_editor.round_up_020()`**, aldri `math.ceil(eur / 0.20) * 0.20`: flyttall gjør at f.eks. 12.600000000000001 rundes til 12,80.
- **String-replace på serialisert JSON** brukes til å bytte id-prefix overalt i generate_osdm — treffer alle ID-typer uten manuell iterasjon, men betyr at old_prefix ikke kan være delstreng av noe annet i filen.
- **FIP-farer med `has_reduction = false`** — NSB 1076 har FIP-farer (nameRef P__5) som IKKE har `reductionConstraintRef`. De er ikke filtrerbare som "rabatt-farer" — de ser ut som vanlige farer og skaper duplikat-nøkler. Se eget avsnitt over.
- **`_find_adult_id` kan velge feil PC** — hvis en PC har to farer med svært ulike priser (f.eks. vanlig og FIP), reduseres gjennomsnittet slik at en annen PC (f.eks. gruppevoksen med ~0,9× pris) kan få høyere snitt og bli valgt som "voksen". Løst med maks-pris per (rc, carrier, sc, pc)-gruppe.
- **Ikke fjern legacy-felt fra farer** (`legacyAccountingIdentifier`, `legacyConversion`, `individualContracts`). OSDM-tool (brukt av bl.a. SilverRail) trenger `serialId` (serienummer) og `legacyConversion: "YES"` for å konvertere til 108.1 — uten dem importeres ingen farer. Ble fjernet ved malopp-rydding mai 2026 og gjenopprettet oktober 2026.
- **price-adjust: ID-mismatch ved ny delivery_id** — se avsnittet om prisregulering ovenfor.
- **`ijson` returnerer Python `int` for heltall i JSON** (ikke `Decimal`). `Decimal` oppstår kun for desimaltall. I fix_osdm brukes ijson til full parse → `default=_ijson_default` i json.dumps håndterer eventuelle Decimal-verdier. I price-adjust og andre steder brukes ijson kun for streaming, og tallverdier er typisk heltall.
- **Én instans, hele filen i minnet for noen tjenester.** All tilstand (jobber, opplastede filer) ligger i prosessens minne — appen må kjøre som **én** instans med **én** worker. Generering, prisregulering og OSDM-editoren gjør `json.loads` på hele filen (~10× filstørrelse i RAM): greit for operatørfiler (1076 ≈ 10 MB), ikke for DB-filer på 1,2 GB. Validering, Excel-eksport, «Rydd opp» og fare-discount strømmer med `ijson` og tåler store filer.
- **Kalenderdatoer** settes alltid med `osdm_editor.set_calendar_validity()` (lokal dato + `+0000`, `utcOffset` for Oslo). Editoren får/sender `YYYY-MM-DD`.
- **Priser i flere valutaer**: bruk `osdm_editor.eur_entry()` / `_get_price_eur()`, aldri `price[0]`.
- **Sidebar span-arving** — koblingene som pakker tekst i `<span>` (OSDM-editor med BETA-tag, Brukere med pending-badge) arvet ikke `--text-muted` korrekt fra parent-`<a>` i mørk modus. Løst med eksplisitte `.sidebar-link span:not(.beta-tag)` farger i `styles.css`.
