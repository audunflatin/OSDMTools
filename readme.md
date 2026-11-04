# PopToOSDM

PopToOSDM er et internverktøy for å generere og redigere OSDM `fareDelivery`‑filer basert på TEN‑avstandsfiler og brukeropplastede OSDM-maler.


Produksjonsdomene: **https://osdmtools.com**

---

## Funksjonalitet

- **Landingsside** — info om tjenesten og tilgangsforespørselsskjema (med rate limiting og honeypot)
- **Priser fra avstandsfil** (`/`) — validering av TEN‑CSV + brukeropplastet OSDM-mal → ny OSDM-fil:
  - DeliveryId, miljø (test/prod), valgfri leveranse, gyldighetsperiode
  - Valutakurs (EUR, NOK, SEK, DKK, GBP, CHF, BGN, CZK, HUF, ISK, PLN, RON, TRY) hentet live fra ECB
  - Prisratioer utledes dynamisk fra den opplastede OSDM-filen (ingen hardkodet mal)
  - Priser rundes opp til nærmeste 0,20 EUR (DRTF-krav)
- **Prisregulering** (`/price-adjust`) — skaler alle priser i en OSDM-fil med fast prosentsats
- **OSDM → Excel** (`/osdmtoexcel`) — konverter OSDM JSON til stilisert Excel:
  - Metadata-boks med leveranseinformasjon
  - ERA RICS-navn på transportørkoder (f.eks. `1076` → `Vygruppen AS`)
  - Støtter stasjonspar med flere operatører
- **Legg til rabatt i OSDM** (`/fare-discount`) — legg rabatterte farer til en eksisterende OSDM-fil
- **Rydd opp i OSDM** (`/fix-osdm`) — fjern ubrukte priser, passasjerkategorier og regionsbegrensninger automatisk
- **OSDM-editor** (`/osdm-editor`) *(beta)* — rediger passasjerprofiler, leveransemetadata og strekningsrelasjoner direkte i en OSDM-fil
- **Stasjonssøk** (`/stasjonssok`) — søk opp jernbanestasjoner via Wikidata (navn eller UIC-kode), viser navn, land og UIC-kode
- **Kontaktskjema** (`/kontakt`) — send melding til admin
- **Min konto** (`/min-konto`) — endre navn, passord og slett konto
- **Admin: brukerhåndtering** (`/admin/users`):
  - Invitasjon av nye brukere med automatisk velkomst-e-post (Resend)
  - Godkjenning/avvisning av tilgangsforespørsler
  - Tildel/fjern admin-tilgang, deaktiver brukere
  - Tvungen passordbytte ved første innlogging
  - Paginering og søk i brukerlisten
- **Admin: aktivitetslogg** (`/admin/log`)
- **Admin: presentasjon** (`/admin/presentation`) — slideshow-presentasjon av tjenestene
- Flerspråklig: norsk, engelsk, tysk, svensk, fransk

---

## Arkitektur

- **Backend:** FastAPI (Python 3.12)
- **Frontend:** Statisk HTML/JS (servert via FastAPI)
- **Database:** SQLite (persistent disk på Railway)
- **Autentisering:** Session‑basert med tvungen passordbytte ved første innlogging
- **E-post:** Resend API
- **Drift:** Railway
- **DNS:** Cloudflare
- **Domene:** osdmtools.com

```
Browser
  ↓
FastAPI (backend/main.py + backend/routers/)
  ├─ /                    → Landingsside (landing.html) — uinnlogget
  │                       → Hoved-GUI (index.html) — innlogget
  ├─ /login               → Innlogging (login.html)
  ├─ /forgot-password     → Glemt passord (forgot_password.html)
  ├─ /reset-password/{t}  → Nullstill passord (reset_password.html)
  ├─ /price-adjust        → Prisregulering (price-adjust.html)
  ├─ /osdmtoexcel         → OSDM → Excel (osdmtoexcel.html)
  ├─ /fare-discount       → Legg til rabatt (fare-discount.html)
  ├─ /fix-osdm            → Rydd opp i OSDM (fix-osdm.html)
  ├─ /osdm-editor         → OSDM-editor (osdm-editor.html)
  ├─ /stasjonssok         → Stasjonssøk (station-lookup.html)
  ├─ /kontakt             → Kontaktskjema (contact.html)
  ├─ /min-konto           → Min konto (min-konto.html)
  ├─ /admin/users         → Brukerhåndtering (admin.html)
  ├─ /admin/log           → Aktivitetslogg (admin-log.html)
  ├─ /admin/presentation  → Presentasjon (presentation.html)
  ├─ /personvern          → Personvernerklæring (personvern.html)
  ├─ /ui/*                → API: TEN / OSDM-generering / Excel
  ├─ /admin/*             → API: brukerhåndtering
  ├─ /fare-discount/*     → API: rabatterte farer
  ├─ /fix-osdm/*          → API: opprydding
  ├─ /osdm-editor/*       → API: OSDM-editor
  ├─ /request-access      → Tilgangsforespørsel (åpent endepunkt)
  └─ /static/*            → CSS, JS, favicon

Database: SQLite (/data/users.db — Railway persistent disk)
```

---

## Oppsett (lokalt – macOS / Linux)

### Forutsetninger

- Python 3.12 eller nyere
- Git

### Virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Miljøvariabler

| Variabel         | Beskrivelse                                          | Standard                    |
|------------------|------------------------------------------------------|-----------------------------|
| `SESSION_SECRET` | Hemmelig nøkkel for sessions                         | `CHANGE_ME_BEFORE_PROD`     |
| `DATABASE_URL`   | SQLite-sti (valgfri)                                 | SQLite lokalt               |
| `RESEND_API_KEY` | API‑nøkkel for Resend (e-post)                       | _(tom – e-post deaktivert)_ |
| `SENDER_EMAIL`   | Avsenderadresse for e-poster                         | `noreply@osdmtools.com`     |
| `CONTACT_EMAIL`  | Mottaker for kontaktskjema og tilgangsforespørsler   | `kontakt@osdmtools.com`     |
| `APP_URL`        | Basis-URL i e-postlenker                             | `https://osdmtools.com`     |

`RESEND_API_KEY` må settes for at e-postfunksjonene skal virke.

### Start applikasjonen lokalt

```bash
.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Applikasjonen er tilgjengelig på `http://127.0.0.1:8000`.

---

## DRTF‑hensyn

Genererte filer følger kravene fra **UIC DRTF**:

- `delivery.usage`: `TEST` → `TEST_ONLY`, `PROD` → `PRODUCTION`
- `deliveryId` styres fra GUI
- `optionalDelivery` settes eksplisitt
- Gyldighet leses fra `fareStructure.calendars`
- Priser rundes opp til nærmeste 0,20 EUR
- Prisratioer utledes dynamisk fra opplastet OSDM-fil (ingen hardkodet mal)

OSDM‑filer generert med dette verktøyet validerer grønt i **UIC DRTF**.

---

## Status

✅ Produksjonsklar  
✅ Validert mot UIC DRTF  
✅ SQLite i produksjon (Railway persistent disk)  
✅ Flerspråklig (norsk, engelsk, tysk, svensk, fransk)  
✅ Priser fra avstandsfil med dynamiske ratioer fra opplastet OSDM-mal  
✅ Prisregulering – skaler OSDM-priser med fast prosentsats  
✅ OSDM til Excel med metadata-boks og RICS-navn  
✅ Legg til rabatterte priser i eksisterende OSDM-fil  
✅ Rydd opp i OSDM – fjern ubrukte elementer automatisk  
✅ OSDM-editor – rediger passasjerprofiler og relasjoner direkte  
✅ Stasjonssøk via Wikidata (navn og UIC-kode)  
✅ Admin‑panel med brukerhåndtering, tilgangsforespørsler og aktivitetslogg  
✅ E-postinvitasjon og passordtilbakestilling via Resend  
✅ Tvungen passordbytte ved første innlogging  
✅ Landingsside med tilgangsforespørselsskjema  
✅ Deployet på Railway (osdmtools.com)  
