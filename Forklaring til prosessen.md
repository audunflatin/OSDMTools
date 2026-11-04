# Sammendrag – OSDMTools‑løsningen

OSDMTools er en komplett løsning for å generere **OSDM fareDelivery‑filer**
basert på **TEN‑avstander** og **TEN‑priser**.

Løsningen er utviklet for å være korrekt i henhold til **OSDM‑spesifikasjonen**
og for å **validere grønt i UIC DRTF**, samtidig som den er enkel å bruke,
forstå og videreutvikle.

Løsningen kjøres lokalt og består av:
- en **backend** som håndterer konvertering og validering
- et **GUI i nettleser** som guider brukeren gjennom hele prosessen fra input
  til ferdig OSDM‑fil

---

## Hva løsningen gjør

- Genererer ferdige **OSDM fareDelivery‑filer** klare for opplasting i UIC DRTF
- Bruker **TEN‑avstander** som grunnlag for prisberegning
- Bruker **TEN‑pristabell (CSV)** til å beregne priser basert på avstand
- Støtter både **TEST** og **PRODUKSJON**
- Setter alle sentrale OSDM‑felt korrekt:
  - `deliveryId`
  - `usage` (`TEST_ONLY` / `PRODUCTION`)
  - `optionalDelivery`
  - gyldighet via `fareStructure.calendars`
- Beregner og viser **eksempelpriser** for utvalgte strekninger
- Sørger for at brukeren følger riktig rekkefølge og ikke kan gjøre feil valg

---

## Hvordan brukeren jobber i GUI

1. Brukeren laster opp **TEN‑pristabell (CSV)**
2. TEN‑tabellen valideres før videre steg er tilgjengelige
3. Brukeren fyller inn:
   - **DeliveryId** (må økes ved hver innsending til DRTF)
   - Miljø (test eller produksjon)
   - OptionalDelivery
   - Valutakurs (NOK → EUR)
   - Gyldig fra / til
4. OSDM‑filen genereres
5. Ferdig fil kan lastes ned
6. Eksempelpriser vises som en egen **tabell i GUI**

GUI‑en er laget slik at:
- knapper kun aktiveres når forutsetningene er oppfylt
- feil vises tydelig før backend‑kall
- status vises klart (**OK / Feil**)
- løsningen er trygg å bruke også for ikke‑utviklere

---

## Konfigurasjonsfiler og datagrunnlag

For å koble sammen **Entur‑data**, **TEN‑avstander** og **OSDM‑modellen**
ble det etablert noen enkle **CSV‑baserte konfigurasjonsfiler**.

Disse fungerer som eksplisitte kartlegginger og gjør løsningen
**sporbar og lett å forklare**.

### `uic_to_stopplace.csv`

- Knytter **UIC‑stasjonkoder** til **Entur stopPlace‑IDer**
- Sikrer sammenheng mellom OSDM‑modellens bruk av UIC‑koder
  og Entur‑grunnlaget

### `connectionpoint_to_stopplace.csv`

- Knytter **OSDM connectionPoints** til **Entur stopPlaces**
- Gjør det mulig å forklare hvilke connectionPoints som representerer
  hvilke faktiske stasjoner eller punkter i Entur‑modellen

### `stopplace_ten_distances.csv`

- Inneholder **TEN‑avstander mellom stopPlaces**
- Avstandene brukes til å:
  - sette `regionalConstraints.distance`
  - slå opp riktig TEN‑prisintervall
  - beregne priser i kroner og deretter euro

Alle disse filene er:
- flate CSV‑filer
- enkle å lese og justere
- uavhengige av kode
- ment som dokumenterte koblinger, ikke skjulte regler

---

## Eksempelpriser

Løsningen beregner eksempelpriser for utvalgte strekninger som faktisk
finnes i OSDM‑modellen, basert på:
- `regionalConstraints.distance`
- TEN‑pristabell
- valutakurs
- definert avrunding

Eksempelprisene:
- vises **kun i GUI**
- vises som **tabell**
- brukes som dokumentasjon og illustrasjon
- påvirker ikke selve fareDelivery‑fila

---

## Viktige kvaliteter ved løsningen

- Full **sporbarhet** fra input til output
- Ingen skjult logikk
- Tydelig separasjon mellom:
  - data
  - logikk
  - presentasjon
- Lokalt kjørbar uten eksterne avhengigheter
- Klar til videre bruk, justering og utvidelse