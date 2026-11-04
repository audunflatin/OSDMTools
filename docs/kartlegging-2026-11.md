# Kartlegging av PopToOSDM – november 2026

Gjennomgang av backend, prislogikk, sikkerhet og frontend, gjort 4. november 2026.

**Status:** K1, K2, K3 og S1 (tester) er rettet 4. november 2026. Resten er åpent.

Hvert funn har en ID (K = kritisk, H = høy, M = middels, S = struktur, U = UX/GUI) slik at vi kan vise til dem når vi tar dem.

**Hvordan funnene er sjekket:** K1 og K2 er bekreftet ved å kjøre koden mot 1076-malen. Resten er funnet ved å lese koden og er ikke kjørt.

---

## Kritisk – bør rettes nå

### K1. ✅ Priser kryper oppover ved hver generering

**Hvor:** `osdm_editor._compute_ratios`, `_round_to_20_cents`, `set_prices_from_adult`

**Hva:** Jeg kjørte `set_prices_from_adult` på 1076-malen med **nøyaktig de samme voksenprisene som filen har i dag**. Da endret **4 820 av 11 660 farer** pris, alle oppover, med 0,20 eller 0,40 EUR. Voksenprisene sto stille, mens barn, honnør, gruppe og FIP gikk opp.

Det er to årsaker:

1. **Ratioen er et snitt av allerede oppoverrundede priser.** Barneratioen blir 0,2516 i stedet for 0,25, honnør 0,501 i stedet for 0,5, og gruppevoksen 0,9019 i stedet for 0,9. Når den skjeve ratioen rundes opp igjen, havner prisen ett hakk for høyt. Det gir 4 648 av endringene.
2. **Flyttall i avrundingen.** `math.ceil(x / 0.20) * 0.20` runder for eksempel 12,600000000000001 opp til 12,80. Det rammer omtrent 11 % av tilfellene der resultatet skulle vært et rent 0,20-tall, og gir de resterende 172 endringene (FIP-voksen).

Med begge feilene rettet endres **0** farer.

**Konsekvens:** Bygges neste leveranse på forrige leveranse, ratioene utledes på nytt fra de allerede for høye prisene, og driften hoper seg opp for hver generering. Kunder betaler noen tiere for mye på mange relasjoner. Samme avrundingsfeil finnes i prisregulering, fare-discount og OSDM-editor.

**Forslag:**
- Rund av i hele cent med heltall (eller `Decimal`), aldri flyttall delt på 0,20.
- La ratioene være kjente verdier (0,25 / 0,5 / 0,9 …) som brukeren bekrefter eller korrigerer, i stedet for et snitt. Alternativt kan snittet rundes av til nærmeste hele prosent.
- ~~Avklares~~ Bekreftet: 1076 bruker 0,25 / 0,5 / 0,9.

### K2. ✅ XSS på admin-siden via det åpne tilgangsskjemaet

**Hvor:** `frontend/admin.js:45-47` og `POST /request-access` i `main.py`

**Hva:** E-postadressen fra tilgangsskjemaet settes inn i `onclick="approveRequest('…')"`. `escapeHtml` escaper ikke apostrof, og backend sjekker ikke at e-postfeltet faktisk er en e-postadresse. En uinnlogget person kan sende inn en «e-post» som bryter ut av JS-strengen. Koden kjøres da i admins nettleser når admin åpner brukersiden.

**Konsekvens:** Koden kjører med admins sesjon, og kan for eksempel opprette en ny admin-bruker via `/admin/add-user`. Det betyr full overtakelse uten innlogging.

**Forslag:** Valider e-postformatet på serveren. Bytt inline-`onclick` med `data-email` og `addEventListener`, og escape `'` i `escapeHtml`.

### K3. ✅ Avstandsfilen (TEN) er felles for alle brukere

**Hvor:** `TEN_TABLE` og `GENERATION_PROGRESS` i `main.py`

**Hva:** Laster bruker A opp en avstandsfil og bruker B laster opp sin like etterpå, genererer A med Bs priser. Fremdriftsindikatoren (`/ui/progress`, som heller ikke krever innlogging) er også felles.

**Konsekvens:** Feil priser i en fil som ser riktig ut. Risikoen er lav med få samtidige brukere, men konsekvensen er alvorlig.

**Forslag:** Lagre avstandsfilen per bruker, som `INPUT_OSDM_STORE`, og bruk det vanlige job-mønsteret for generering.

---

## Høy

### H1. Tunge operasjoner fryser hele serveren

**Hvor:** `price_adjust`, `fix_osdm`, `fare_discount_apply`, `editor_load`, `editor_relation` og flere er `async def`, men gjør tung, synkron JSON-behandling.

**Hva:** I FastAPI kjører `async def` på selve hendelsesløkken. Mens én bruker prisregulerer en stor fil, svarer serveren ikke på noen andre forespørsler, heller ikke innlogging eller fremdriftsmålinger. Vanlige `def`-endepunkter (som `generate_osdm`) kjøres derimot i en trådpool og har ikke dette problemet.

**Forslag:** Gjør dem om til `def` (enkleste løsning), eller flytt arbeidet til bakgrunnsjobb etter CLAUDE.md-mønsteret.

### H2. Nye rabattfarer mangler legacy-feltene SilverRail trenger

**Hvor:** `fare_discount_apply`, der `new_fare` bygges

**Hva:** Nye farer får ikke `legacyAccountingIdentifier`, `legacyConversion` eller `individualContracts`. `involvedTCOs` settes til de valgte transportørene, og blir **tom** hvis ingen er valgt (originalen har `["1076"]`).

**Konsekvens:** Samme feil som SilverRail-saken i oktober. Rabattfarene blir sannsynligvis ikke med i 108.1-konverteringen.

**Forslag:** Kopier legacy-feltene og `involvedTCOs` fra originalfaren. Avklar hvilket `serialId` en rabattfare skal ha.

### H3. Genereringen hopper stille over relasjoner avstandsfilen ikke dekker

**Hvor:** `generate_osdm`, `except ValueError: pass`

**Hva:** Relasjoner med en avstand utenfor avstandsfilen beholder **malens gamle priser**, uten at det står noe i resultatet. Avstandssjekken under validering advarer om dette, men selve genereringen sier ingenting.

**Forslag:** Tell og vis antall relasjoner som ikke fikk ny pris i genereringsresultatet. Vurder å stoppe genereringen helt.

### H4. HTML-injeksjon i e-poster, og kontaktskjemaet er helt åpent

**Hvor:** `email_utils.send_contact_email` og `send_access_request_email`, og `POST /contact`

**Hva:** Navn, organisasjon og melding settes rett inn i HTML-e-posten uten escaping. Tilgangsskjemaet er åpent for alle, så hvem som helst kan få en e-post med egne lenker og eget innhold til å se ut som den kommer fra OSDMTools. `POST /contact` krever i tillegg verken innlogging eller rate limiting, selv om kontaktsiden gjør det.

**Forslag:** Bruk `html.escape()` på alle brukerverdier. Krev innlogging og rate limit på `/contact`.

### H5. Deaktiverte og slettede brukere forblir innlogget

**Hvor:** `require_login`

**Hva:** Innlogging sjekkes bare ut fra sesjons-cookien. Sletter eller deaktiverer du en bruker, fortsetter vedkommende å ha tilgang til cookien utløper (Starlette-standard: 14 dager). Det samme gjelder tvunget passordbytte: brukeren kan gå rett til `/` uten å bytte.

**Forslag:** Slå opp brukeren i databasen i `require_login`, sjekk `is_active` og `must_change_password`, og sett kortere `max_age` og `https_only=True` på sesjonen.

---

## Middels

| ID | Hvor | Funn | Forslag |
|---|---|---|---|
| M1 | `price_adjust` | Grupperer på `fareConstraintBundleRef`, men feltet heter `bundleRef`. Grupperingen blir derfor bare (relasjon, transportør). Priser som deles mellom grupper skrives over av den siste gruppen. | Skaler hver pris direkte: `ny = rund_opp(gammel × faktor)` |
| M2 | `price_adjust`, Excel-eksport, fare-discount | Bruker `price[0]` uten å sjekke at valutaen er EUR | Velg EUR-oppføringen eksplisitt, som `osdm_editor._get_price_eur` gjør |
| M3 | `fare_discount_apply` | Bruker den *første* faren per (relasjon, kategori, klasse) som grunnlag. I 1076 er det tilfeldigvis primærfaren, men med en annen rekkefølge blir rabatten beregnet fra FIP-prisen. | Velg faren med høyest pris, slik at duplikatregelen i CLAUDE.md følges |
| M4 | `/forgot-password` | Ingen rate limit, så hvem som helst kan sende mange e-poster til en bruker | Samme rate limit som tilgangsforespørsel |
| M5 | Generering og OSDM-editor | `json.loads` på hele filen. En fil på 1,2 GB krever trolig 8–12 GB RAM. Ingen størrelsesgrense på serveren. | Avklar om generering og editor skal håndtere DB-filer. Hvis ikke, bør det stå tydelig. |
| M6 | `set_prices_from_adult` | Går gjennom alle kombinasjoner for hver relasjon (kvadratisk). Greit med 1 166 relasjoner, men uholdbart med 100 000. | Indekser kombinasjonene per relasjon |
| M7 | `update_delivery` (editor) | Gyldighetsdatoer skrives som de kommer fra frontend, uten format og uten `utcOffset`, i motsetning til generering og prisregulering | Felles funksjon for kalenderoppdatering |
| M8 | Hele appen | Tilstanden ligger i minnet. Kjører Railway noen gang mer enn én instans eller worker, forsvinner jobber og filer tilfeldig. | Dokumenter kravet om én instans |

---

## Struktur og vedlikehold

| ID | Funn | Forslag |
|---|---|---|
| S1 ✅ | **Ingen tester.** Prislogikken har ingen automatisk kontroll. K1 hadde blitt fanget av én enkel test. | Testsuite med 1076-malen som fasit: avrunding, ratioer, FIP-proporsjoner, ID-bytte, «ingen endring gir ingen endring» |
| S2 | `main.py` er 2 966 linjer med 75 endepunkter | Del opp med `APIRouter`: auth, admin, generate, price_adjust, fare_discount, fix_osdm, excel, editor |
| S3 | 13 nesten like side-ruter (sjekk innlogging → les HTML → sett inn `IS_ADMIN`) | Én hjelpefunksjon `serve_page(request, "fil.html")` |
| S4 | Job-mønsteret er implementert tre ganger, med to ulike formater for fremdriftssvar | Felles `JobStore` med `start`, `progress` og `result` |
| S5 | Struktursjekken «er dette OSDM?» er kopiert fem ganger | Én hjelpefunksjon |
| S6 | Ubrukt kode: `_id_base`, `eur_amount`, `admin_mock.js`, `layout-preview.html`, `logo-preview.html`, og `1076-OSDM-templateBACKUP13.05.2026.json` (10 MB i git) | Slett |
| S7 | `print()` i stedet for `logger`, og `import` inni funksjoner | Rydd |
| S8 | `@app.on_event("startup")` er utdatert i FastAPI | `lifespan` |
| S9 | `passlib` vedlikeholdes ikke lenger | Bytt til `hashlib.pbkdf2_hmac` direkte, eller `argon2-cffi`, med kompatibel verifisering av eksisterende hasher |
| S10 | Feilmeldinger fra backend er hardkodet på norsk og vises som de er for engelske, tyske og andre brukere | Returner feilkoder og oversett dem i frontend |

---

## UX og GUI

| ID | Funn | Forslag |
|---|---|---|
| U1 | Bare landingssiden har `<meta name="viewport">`, og `styles.css` har ingen `@media`-regler. Appen er ikke laget for mobil eller nettbrett. | Grunnleggende responsivitet (sidebar som meny på smale skjermer). Lav prioritet for et B2B-verktøy. |
| U2 | Sidebaren er kopiert inn i 13 HTML-filer. En ny tjeneste krever endringer i alle. | Generer sidebaren fra én kilde (JS eller server) |
| U3 | Mye inline-`style` (opptil 35 per side) | Flytt til klasser i `styles.css` |
| U4 | Hardkodet tekst forbi i18n, blant annet tabelloverskriftene og «Viser de første 500 …» i OSDM-editoren, «fares»/«prices» i statistikk-chipsene og «Laster…» i admin-loggen | Inn i `i18n.js` |
| U5 | Fremdrift og opplasting er implementert separat på hver side | Felles komponent for filopplasting med fremdriftsindikator |
| U6 | Genereringsresultatet viser fem eksempler, men ikke hva som *ikke* ble oppdatert (se H3) | Oppsummering med antall oppdaterte og hoppet over |

---

## Anbefalt rekkefølge

1. **Sikkerhet og priser:** K2, K1 (etter at du har bekreftet ratioene), K3, H5, H4
2. **Sikkerhetsnett:** S1 – tester for prislogikken *før* noe annet refaktoreres
3. **Riktighet:** H1, H2, H3, M1–M4
4. **Struktur:** S2–S8, med testene som garanti for at ingenting endrer oppførsel
5. **UX/GUI:** U2, U4, U5, U6, deretter U1 og U3

Steg 1 og 2 er en god start før det kommer ny funksjonalitet. Steg 4 gjør det merkbart enklere å legge til nye tjenester.
