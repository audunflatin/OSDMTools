# PopToOSDM – Kontekst og løsningsbeskrivelse

Dette dokumentet beskriver status, arkitektur og valg i PopToOSDM-løsningen.
Det er ment som kontekst for videre arbeid, feilsøking eller videreutvikling.

---

## Overordnet formål

PopToOSDM er et GUI-basert verktøy for europeiske jernbaneoperatører for å generere
og håndtere OSDM fareDelivery-filer basert på:

- TEN-avstander (regionalConstraints.distance)
- TEN-pristabell (CSV)
- Brukerinput via GUI

Genererte OSDM-filer er validert mot **UIC DRTF** og laster grønt i DRTF.

Produksjonsdomene: **https://osdmtools.com**

---

## Teknologi

- **Backend:** FastAPI (Python 3.12)
- **Frontend:** HTML / CSS / Vanilla JavaScript (servert via FastAPI)
- **Database:** SQLite (persistent disk på Railway: `/data/users.db`)
- **Autentisering:** Session-basert, pbkdf2_sha256 via `hashlib` (passlib-kompatibelt format)
- **Drift:** Railway (automatisk deploy fra `main`)
- **DNS / CDN:** Cloudflare (CNAME-oppslag, ikke A-record)
- **E-post:** Resend API

---

## Filstruktur

```
backend/
  main.py          — app-oppsett (middleware, lifespan, include_router)
  routers/         — én fil per tjeneste (se CLAUDE.md «Backend-struktur»)
  core/            — security, state, pages, osdm_bytes, settings
  osdm_editor.py   — OSDM-parsing, ratio-inferens, priskalkulering, relasjon-tillegg
  auth_db.py       — SQLAlchemy-modeller: User, LoginLog, EventLog + migrering
  auth_utils.py    — passord-hashing og generering
  email_utils.py   — e-postutsending via Resend API
  rics_codes.py    — ERA RICS-koder: 2388 europeiske transportørnavn (kode → navn)
  core/
    settings.py    — SESSION_SECRET, RESEND_API_KEY, SENDER_EMAIL, CONTACT_EMAIL, APP_URL
frontend/
  landing.html     — landingsside for uinnloggede brukere
  login.html       — innloggingsside
  index.html       — hoved-GUI (Priser fra avstandsfil)
  price-adjust.html — Prisregulering (skaler priser med fast %)
  fare-discount.html — legg til rabatterte priser i eksisterende OSDM
  osdm-editor.html — OSDM-editor (passasjerprofiler, metadata, relasjoner)
  admin.html       — admin-panel (brukerhåndtering, kun for admins)
  admin-log.html   — aktivitetslogg (kun for admins)
  fix-osdm.html    — rydd opp i OSDM (to-stegs flyt)
  osdmtoexcel.html — OSDM til Excel-konvertering
  contact.html     — kontaktskjema
  min-konto.html   — Min konto (endre navn, endre passord, slett konto)
  endre-passord.html — 301-redirect til /min-konto (beholdt for bakoverkompatibilitet)
  change_password.html — tvungen passordbytte ved første innlogging
  forgot_password.html — glemt passord (be om tilbakestillingslenke)
  reset_password.html  — tilbakestill passord via e-postlenke
  app.js           — JavaScript for index.html
  priceAdjust.js   — JavaScript for price-adjust.html
  osdmEditor.js    — JavaScript for osdm-editor.html
  admin.js         — JavaScript for admin.html (paginering, søk, brukertabell)
  admin-log.js     — JavaScript for admin-log.html
  fareDiscount.js  — JavaScript for fare-discount.html
  osdmtoExcel.js   — JavaScript for osdmtoexcel.html
  fixOsdm.js       — JavaScript for fix-osdm.html
  i18n.js          — flerspråklig støtte (no, en, de, sv, fr)
  styles.css       — felles styling (mørk marineblå, Entur-inspirert)
  admin_mock.js    — lokal testdata for admin-siden (i .gitignore)
data/
  input/
    connectionpoint_to_stopplace.csv — kobling CP-id → stopplace
    uic_to_stopplace.csv             — kobling UIC → stopplace
schemas/           — JSON-skjemaer for OSDM-validering
railway.json       — Railway-konfigurasjon
Procfile           — alternativ startkommando for Railway
.python-version    — Python 3.12
```

---

## Routing – viktig å kjenne til

`/` (root) serverer **ulike sider** avhengig av innloggingsstatus:
- Uinnlogget → `landing.html` (landingsside)
- Innlogget → `index.html` (hoved-GUI)

`/login` er en separat rute (GET + POST).

Statiske HTML-filer er blokkert via `_NoHTMLStaticFiles` (subklasse av `StaticFiles`
som returnerer 404 for `.html`-filer). Gir auth-bypass hvis man åpner `/static/admin.html` direkte.

---

## Landingsside og tilgangsforespørsel

`landing.html` vises for alle som ikke er innlogget. Inneholder:
- Hero med logo, tagline, «Logg inn»-knapp og «Be om tilgang»-knapp (ghost-stil)
- Fire funksjonskort (Prisregulering, Priser fra avstandsfil, OSDM→Excel, Legg til rabatt)
- Tilgangsforespørselsskjema — skjult bak en trigger-knapp med smooth CSS-animasjon

### Tilgangsforespørsel (`POST /request-access`)
- **Åpent endepunkt** — ingen innlogging nødvendig
- **Honeypot:** skjult `website`-felt — bots fyller det ut og avvises stille
- **Rate limiting:** maks 3 forespørsler per IP per 24 timer (`_access_requests` i `backend/core/security.py`)
- Navn er splittet i `first_name` + `last_name` (to separate felt i skjema og DB)
- Sender e-post til **alle aktive admins** (hentes fra DB) via `send_access_request_email()`
- Logger hendelsen i `EventLog`

---

## Flerspråklig støtte (i18n)

Støtter **5 språk**: norsk (no), engelsk (en), tysk (de), svensk (sv), fransk (fr).

- Språk detekteres automatisk fra nettleseren (`navigator.languages`)
- Valgt språk lagres i `localStorage` (nøkkel: `poptoosdm_lang`)
- Alle synlige tekster i HTML bruker `data-i18n="nøkkel"`-attributter
- Plassholdertekst: `data-i18n-placeholder="nøkkel"`
- Title-attributter: `data-i18n-title="nøkkel"`
- Toggle-knapper med skjult radio-input: wrap teksten i `<span data-i18n="nøkkel">`
- All dynamisk tekst i JS bruker `t("nøkkel")`-funksjonen
- `i18n.js` må lastes **før** side-spesifikk JS
- Språkbytte kaller `loadUserList()` på admin-siden for å oppdatere knapptekster
- Global `invalid`-event-listener oversetter HTML5-valideringsmeldinger til valgt språk

### Legge til ny tekst
1. Bruk `data-i18n="min_nøkkel"` i HTML eller `t("min_nøkkel")` i JS
2. Legg til nøkkelen i **alle fem språk** i `i18n.js`
3. Bump `i18n.js`-versjon i alle HTML-filer som laster den

---

## Cache-busting – gjeldende versjoner

| Fil | Versjon |
|---|---|
| `styles.css` | v=25 |
| `i18n.js` | v=54 (alle hovudsider) / v=19 (login-sider) |
| `app.js` | v=21 |
| `admin.js` | v=19 |
| `admin-log.js` | v=4 |
| `osdmtoExcel.js` | v=7 |
| `fareDiscount.js` | v=18 |
| `priceAdjust.js` | v=10 |
| `fixOsdm.js` | v=3 |
| `osdmEditor.js` | v=2 |
| `presentation.js` | v=8 |

HTML-filer som laster `i18n.js` med v=54:
`landing.html`, `index.html`, `admin.html`, `admin-log.html`, `fare-discount.html`,
`contact.html`, `endre-passord.html`, `min-konto.html`, `osdmtoexcel.html`, `price-adjust.html`, `fix-osdm.html`, `osdm-editor.html`

HTML-filer med v=19 (login-sider, endres sjelden):
`login.html`, `change_password.html`, `forgot_password.html`, `reset_password.html`

---

## Global server-state

Definert i `backend/core/state.py`:

```python
TEN_TABLE: list | None         # Lastes ved POST /ui/validate-ten
OSDM_STORE: dict               # user_email → {"filename": str, "content": str, "created_at": float}
INPUT_OSDM_STORE: dict         # user_email → {"filename": str, "bytes": bytes, "created_at": float}
FIX_OSDM_STORE: dict           # user_email → {"filename": str, "content": bytes, "created_at": float}
EDIT_STORE: dict               # user_email → osdm_editor store_entry + {"filename": str, "created_at": float}
XLSX_JOBS: dict                # job_id → {status, result, percent, user_email, ...}
VALIDATION_JOBS: dict          # job_id → {status, percent, phase, result, user_email, ...}
PARSE_JOBS: dict               # job_id → {status, percent, phase, result, user_email, ...}
GENERATION_PROGRESS: dict      # {"status": ..., "percent": ...}
_login_attempts: dict          # IP → [timestamps] — rate limiting innlogging
_access_requests: dict         # IP → [timestamps] — rate limiting tilgangsforespørsler
```

En bakgrunnstråd rydder alle syv stores eldre enn 2 timer hvert 10. minutt.
`OSDM_STORE` og `FIX_OSDM_STORE` kan inneholde store filer (opp til ~1,2 GB for Deutsche Bahn).

**Konsekvens:** TEN-filen og OSDM-malfilen må valideres i riktig rekkefølge per server-sesjon.
Ingenting skrives til disk under generering.

---

## Miljøvariabler

| Variabel         | Beskrivelse                          | Standard                    |
|------------------|--------------------------------------|-----------------------------|
| `SESSION_SECRET` | Hemmelig nøkkel for sessions         | `CHANGE_ME_BEFORE_PROD`     |
| `DATABASE_URL`   | SQLite-sti                           | SQLite lokalt               |
| `RESEND_API_KEY` | API-nøkkel for Resend                | _(tom – e-post deaktivert)_ |
| `SENDER_EMAIL`   | Avsenderadresse for e-poster         | `noreply@osdmtools.com`     |
| `CONTACT_EMAIL`  | Fallback-mottaker for kontaktskjema  | `kontakt@osdmtools.com`     |
| `APP_URL`        | Basis-URL i e-postlenker             | `https://osdmtools.com`     |

---

## E-post (Resend)

Alle utgående e-poster håndteres av `email_utils.py`.

| Funksjon | Trigger | Mottaker |
|---|---|---|
| `send_welcome_email` | Admin legger til ny bruker | Ny bruker |
| `send_reset_link_email` | Bruker ber om glemt-passord | Brukeren |
| `send_contact_email` | Kontaktskjema sendes | `CONTACT_EMAIL` |
| `send_access_request_email` | Tilgangsforespørsel sendes | Alle aktive admins (fra DB), fallback: `CONTACT_EMAIL` |

E-postdesign: mørk marineblå (`#0d1b2a`/`#152535`), korall-rød aksentfarge.
Støtter dark mode via `@media (prefers-color-scheme: dark)` + `data-ogsc` for Outlook.

---

## Brukerhåndtering

- Brukere lagres i SQLite (`/data/users.db` på Railway)
- Passord: hashes med pbkdf2_sha256 (`backend/auth_utils.py`, passlib-kompatibelt format)
- Sesjon: server-side via Starlette SessionMiddleware

### Databasemodell – User
| Kolonne | Type | Beskrivelse |
|---|---|---|
| `email` | String | Unik, brukes som innlogging |
| `password_hash` | String | pbkdf2_sha256-hash |
| `first_name` | String | Fornavn (valgfritt) |
| `last_name` | String | Etternavn (valgfritt) |
| `is_admin` | Boolean | Admin-tilgang |
| `is_active` | Boolean | Aktiv/deaktivert |
| `must_change_password` | Boolean | True inntil bruker bytter passord |
| `first_login_at` | DateTime | Tidspunkt for første vellykkede innlogging |

### Databasemodell – AccessRequest
Lagrer tilgangsforespørsler fra landingssiden.
Feltene inkluderer `first_name`, `last_name`, `email`, `org`, `requested_at`, `status`.

### Databasemodell – EventLog
Logger alle hendelser: innlogginger, brukerstyring, OSDM-generering, kontakt, tilgangsforespørsler.
Feltene er `user_email`, `event_type`, `status`, `detail` (JSON), `created_at`.

### Innloggingsflyt
1. Bruker logger inn → logges i EventLog
2. Hvis `must_change_password = True` → redirect til `/change-password`
3. Bruker velger nytt passord → `must_change_password = False`
4. Redirect til `/`

### Admin-panel (`/admin/users`)
- Søkefelt filtrerer på navn og e-post
- Paginering: 15 brukere per side
- Brukerstatus: ✅ innlogget, — avventer, ❌ inaktiv
- Brukertabell viser: Navn, E-post, Admin, Aktiv, Handlinger
- Handlinger: gi/fjern admin-tilgang, slett bruker
- Admin-status sjekkes mot DB på hvert kall (`_check_is_admin_from_db`) — sessions kan ikke bli "stale"
- `window.IS_ADMIN = true` injiseres server-side i alle admin-sider

### Aktivitetslogg (`/admin/log`)
Viser alle EventLog-hendelser med filtrering og paginering.

### Min konto (`/min-konto`)
- Endre fornavn/etternavn (`GET /account` + `POST /account/name`)
- Endre passord (`POST /change-password`)
- Slett konto (`DELETE /account`) — fjerner bruker og all tilknyttet data

---

## Sikkerhet

- **`_NoHTMLStaticFiles`**: blokkerer direkte tilgang til `.html`-filer via `/static/` (returnerer 404)
- **`_check_is_admin_from_db(request)`**: leser `is_admin` fra DB og oppdaterer session — brukes på alle admin-ruter
- **`window.IS_ADMIN`**: injiseres server-side i admin-sider via `</head>`-string-replace
- **Job-eierskap**: alle job-dicts lagrer `user_email` — progress/download returnerer 403 for feil bruker
- **Rate limiting**: 10 innloggingsforsøk per IP per 60 sek; 3 tilgangsforespørsler per IP per 24 t
- **SameSite=strict** på session-cookie; `CF-Connecting-IP` for korrekt IP bak Cloudflare
- **`_safe_filename()`**: saniterer filnavn i `Content-Disposition`-headere

---

## Prisregulering – endepunkter

| Kall | Handling |
|---|---|
| `GET /price-adjust` | Serverer `price-adjust.html` |
| `POST /price-adjust` | Mottar OSDM-fil + parametere, returnerer justert fil |

Parametere: `osdm_file`, `pct`, `delivery_id`, `previous_delivery_id`, `environment`, `optional_delivery`, `valid_from`, `valid_to`.
Algoritme: grupper fares etter (RC, carrier, bundle) → maks = voksen → skaler med `1 + pct/100` → rund opp til 0,20 EUR → beregn øvrige fra ratio → oppdater delivery-felt og kalenderperiode.

---

## Priser fra avstandsfil – flyt og endepunkter

| # | Kall | Handling |
|---|---|---|
| 1 | `POST /ui/validate-ten` | Parser TEN-CSV, lagrer i `TEN_TABLE` |
| 2 | `POST /ui/validate-osdm` | Validerer struktur, lagrer råbytes i `INPUT_OSDM_STORE[user_email]`, returnerer warnings + deliveryId |
| 3 | `GET /ui/exchange-rate?from_=EUR&to=NOK` | Henter kurs fra frankfurter.app (ECB) |
| 4 | `POST /ui/generate-osdm` | Bruker `TEN_TABLE` + `INPUT_OSDM_STORE[user_email]` (ingen hardkodet template), lagrer i `OSDM_STORE[user_email]` |
| 5 | `GET /ui/download-osdm/{filename}` | Serverer `OSDM_STORE[user_email]` |
| 6 | `POST /ui/excel-from-generated` | Konverterer `OSDM_STORE[user_email]` til Excel (async) |

### Generering
- Bruker `osdm_editor.load_osdm()` for å lese ratioer fra den opplastede OSDM-filen
- Ratioer er IKKE lagret i OSDM — de utledes fra eksisterende priser (voksen = høyest gjennomsnittspris)
- Priser settes via `osdm_editor.set_prices_from_adult(store_entry, rc_adult_prices)`
- Id-prefix byttes via string-replace på serialisert JSON etter at prisene er satt
- Rundes opp til nærmeste 0,20 EUR (DRTF-krav): `math.ceil(eur / 0.20) * 0.20`
- Valutakurs hentes live fra frankfurter.app (ECB)

### Validering – typer advarsler
`POST /ui/validate-osdm` returnerer warnings for:
- Farer som peker på ukjent `priceRef`, `passengerConstraintRef`, `regionalConstraintRef` o.l.
- RC-er med ugyldig `entryConnectionPointId` / `exitConnectionPointId`
- Ubrukte `prices`, `passengerConstraints`, `regionalConstraints`

`POST /ui/fix-osdm` fikser alle disse — se eget avsnitt nedenfor.

---

## Rydd opp i OSDM – to-stegs flyt

| Kall | Handling |
|---|---|
| `POST /ui/fix-osdm` | Analyserer filen, lagrer resultat i `FIX_OSDM_STORE[user_email]`, returnerer JSON `{stats, filename}` |
| `GET /ui/fix-osdm/download` | Serverer lagret resultat fra `FIX_OSDM_STORE[user_email]` |

Brukerflyten: last opp fil → se oppsummering av hva som vil bli fjernet → klikk "Last ned fikset fil".
Resultatet slettes fra minnet etter 2 timer (cleanup-loopen).

**`ijson` parser tall som `decimal.Decimal`** — alle `json.dumps`-kall bruker `default=_ijson_default` for å konvertere til int/float.

---

## Fare-discount – legg til rabatterte priser

| Kall | Handling |
|---|---|
| `POST /fare-discount/parse` | Parser OSDM-fil, returnerer stasjoner/transportører/passasjerkategorier |
| `POST /fare-discount/apply` | Legger til rabatterte farer, returnerer oppdatert OSDM-fil |
| `GET /fare-discount/rics` | Liste over alle RICS-transportørkoder |

Nøkkelparametere for `/fare-discount/apply`:
- `stationPairsJson` — `[{fromCpId, toCpId, fromUic, toUic}, …]`
- `discountName`, `discountPct` — navn og prosent (1–99)
- `carrierCodes[]` — valgfri RICS-liste (tom = ingen begrensning)
- `passengerRefs[]`, `serviceClassIds[]` — kategorier og klasser

---

## OSDM til Excel-konvertering

Konvertering kjøres async i bakgrunnstråd. Klienten poller status hvert 300ms.

Endepunkter:
- `POST /frontend/osdm-to-csv` — starter jobb, returnerer `jobId`
- `GET /frontend/osdm-to-csv-status/{job_id}` — status og prosent
- `GET /frontend/osdm-to-csv-download/{job_id}` — last ned ferdig fil

Kolonner bygges dynamisk fra farestruktur. Metadata-boks øverst (rader 1–7)
med leverandør, delivery-ID, gyldighetsperiode, transportør(er) med RICS-navn.

---

## OSDM-editor – flyt og endepunkter

`backend/osdm_editor.py` inneholder all logikk; `EDIT_STORE[user_email]` holder parse-resultat per bruker.

| Kall | Handling |
|---|---|
| `POST /osdm-editor/load` | Parser OSDM-fil via `osdm_editor.load_osdm()`, lagrer i `EDIT_STORE`, returnerer summary |
| `GET /osdm-editor/summary` | Returnerer summary fra eksisterende `EDIT_STORE[user_email]` |
| `POST /osdm-editor/metadata` | Oppdaterer delivery-felt (deliveryId, previousDeliveryId, usage, optionalDelivery) + kalenderperiode |
| `POST /osdm-editor/passenger/{pc_id}` | Setter ny ratio for en passasjerkategori, rekalkuderer alle priser |
| `POST /osdm-editor/relation` | Legger til ny relasjon (RC + fares for alle PC × SC-kombinasjoner) |
| `GET /osdm-editor/download` | Serialiserer og returnerer endret OSDM-fil |

Frontend (`osdm-editor.html` / `osdmEditor.js`): 4-fane layout — Oversikt/metadata, Passasjerprofiler (ratio-redigering), Relasjoner (legg til ny RC + pristabell), Last ned.

---

## OSDM-struktur – nøkkelkonsepter

ID-mønster: `{fareProvider}_{deliveryId}_{typekode}__{nr}` — typekoder: E=connectionPoint, K=regionalConstraint, I=price, G=passengerConstraint, C=carrierConstraint, S=bundle

**Ratio-innsikt (viktig):** Ratio er IKKE lagret i OSDM-filer — den utledes ved å dele passasjerkategoriens pris på voksenprisen. Voksen = passasjerkategori med høyest gjennomsnittlig ikke-null pris (ekskl. reduction-fares). `osdm_editor.load_osdm()` gjør dette automatisk og lagrer `adult_id` og `ratios` i store_entry.

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

## Viktige tekniske detaljer

### UIC-koder å merke seg
- Oslo S: `7600100`
- Kornsjø grense: `7600551`

### Kjente fallgruver
- **`TEN_TABLE` er None etter serverrestart** — brukeren må validere TEN-filen på nytt. Tilsvarende for `INPUT_OSDM_STORE`. Dette er by design.
- **`import requests` var lenge glemt** i `main.py` (lagt til mai 2026) — valutahenting feilet stille
- **`padding-left: 220px` på body** i `styles.css` gjelder hovudsider med sidemeny — login-sider må overstyre med `padding: 0`
- **String-replace på id-prefix** i generate_osdm — `old_prefix` kan ikke være delstreng av andre verdier i filen

### Store OSDM-filer
- Deutsche Bahn (1080): 1,2 GB JSON, 1 210 300 fares, 48 412 RC-er
- Filstørrelsesgrense: 5000 MB overalt (ingen betingede grenser)

### Railway-konfigurasjon
- Startkommando: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
- Persistent disk montert på `/data` — inneholder `users.db`
- DNS: Cloudflare CNAME (ikke A-record) mot Railway-domenet

---

## Status

- ✅ Funksjonelt ferdig
- ✅ Validert mot UIC DRTF
- ✅ Deployet på Railway (osdmtools.com)
- ✅ SQLite i produksjon (Railway persistent disk)
- ✅ Landingsside med tilgangsforespørselsskjema (honeypot + rate limiting)
- ✅ Admin-panel med paginering, søk (navn + e-post), navn-kolonne, aktivitetslogg
- ✅ E-postinvitasjon via Resend, tilgangsforespørsel til alle admins
- ✅ Tvungen passordbytte ved første innlogging
- ✅ Min konto: endre navn, endre passord, slett konto
- ✅ OSDM til Excel-konvertering (alle land/operatører, metadata-boks, RICS-navn)
- ✅ Legg til rabatterte priser i eksisterende OSDM-fil
- ✅ Rydd opp i OSDM (to-stegs: analyser → bekreft → last ned)
- ✅ Prisregulering – skaler OSDM-priser med fast prosentsats
- ✅ OSDM-editor: rediger passasjerprofiler (ratio), metadata, legg til relasjoner
- ✅ Priser fra avstandsfil: generisk for alle operatører (ikke lenger begrenset til Vy/1076-template)
- ✅ Dynamisk ratio-inferens fra opplastet OSDM-fil (voksen = høyest gjennomsnittspris)
- ✅ Stasjonsnavn fra alle operatørers OSDM-filer (inkl. Trenitalia-format)
- ✅ Flerspråklig støtte (norsk, engelsk, tysk, svensk, fransk)
- ✅ HTML5-valideringsmeldinger oversatt til valgt språk
- ✅ Sikkerhet: blokkert direkte HTML-tilgang, admin sjekket mot DB per kall
- ✅ Ingen øvre filstørrelsesgrense

---

## Videre arbeid (hvis aktuelt)

- OAuth2 / Azure AD SSO for Entur-intern drift
