# HEMS Master

HEMS Master is één Home Assistant custom integration voor een thuisaccu, Deye hybride omvormer, dynamische stroomprijs, MID-gemeten EV-laadpaal, werkgeversvergoeding en ERE-opbrengst. Je voegt zelf je bestaande entiteiten toe; de integratie regelt daarna de berekeningen, opslag, load balancing, het laadbeleid en de Deye-aansturing. Er zijn geen losse `input_number`-, `template`- of `utility_meter`-helpers nodig.

> **Veiligheid eerst.** Modbus-schrijfacties staan standaard uit. Laat de integratie eerst alleen meelezen, controleer je Deye-registermap en de werkelijke reactie van de omvormer, en schakel daarna pas schrijven in. De integratie kan je energie-installatie aansturen; gebruik haar niet zonder de FAT/SAT-tests uit dit document en zonder de veiligheidsvoorschriften van de fabrikant.

---

## 1. Wat de integratie doet

- Leest elke 30 seconden je prijs, MID-meter, accu, net, PV, EV en warmtepomp.
- Integreert de MID-meterstand tot laadsessies en rekent per sessie:
  - dynamische importkosten,
  - werkgeversvergoeding,
  - ERE-opbrengst,
  - netto winst.
- Houdt dag- en maandcijfers bij, plus de laatste 100 laadsessies en 400 dagen historie.
- Berekent iedere 5 seconden de veilige laadstroom voor de EV op basis van de hoofdzekering.
- Maakt elk uur een prijsplan voor de accu en schrijft alleen bij een wijziging naar de Deye.
- Gebruikt altijd de verplichte Deye-transactie: **unlock register 1100 → instellingen schrijven → verifiëren → lock register 1100**.
- Schakelt na 60 seconden communicatieverlies naar fallback: geen verdere schrijfacties en een duidelijke status.
- Herstelt na terugkomst van Modbus eerst een veilige toestand voordat het automatische plan weer wordt toegepast.
- Biedt een kant-en-klaar Lovelace-dashboard met power-flow, KPI's, prijsgrafiek en override-bediening.
- Biedt een service om een auditpayload met MID-sessies te exporteren.

Wat versie 1.0 **niet** doet:

- De Weheat Sparrow P60 wordt gelezen en getoond, maar niet automatisch in temperatuur gestuurd. Daarvoor zijn schrijfrechten en een warmtepomp-specifieke API nodig die per installatie verschillen.
- De integratie herschrijft de klok en de zes TOU-tijdvensters van de Deye niet. Ze gebruikt de registeradressen uit de opdrachtspecificatie (130, 145, 166, 172). Controleer of jouw firmware deze adressen hetzelfde interpreteert.
- Modbus wordt via **TCP** uitgelezen en geschreven. RTU over een seriële poort is in deze versie niet ingebouwd; gebruik een Modbus TCP-gateway of de netwerkpoort van de Deye.
- Er wordt geen belastingadvies gegeven. De fiscale behandeling van ERE is een juridisch onderwerp; zie §11.

---

## 2. Installatie

### Handmatig

1. Kopieer de map `custom_components/hems_master` naar:
   ```text
   /config/custom_components/hems_master
   ```
2. Herstart Home Assistant.
3. Ga naar **Instellingen → Apparaten & diensten → Integratie toevoegen**.
4. Zoek **HEMS Master** en doorloop de wizard.

### Via HACS (custom repository)

1. HACS → Integraties → drie puntjes → **Custom repositories**.
2. Voeg de repository-URL toe als categorie **Integration**.
3. Installeer **HEMS Master** en herstart Home Assistant.

De integratie declareert `pymodbus>=3.6.0,<4.0.0`. Home Assistant installeert deze requirement bij het laden van de integratie.

---

### Publiceren naar GitHub

De repository is voorbereid voor:

```text
https://github.com/KDCapital/KD-Home-Energy-Brain
```

Vanuit de projectmap:

```bash
bash scripts/push_to_github.sh
```

Het script maakt de eerste commit, zet de branch op `main` en pusht naar `origin`. De GitHub Actions-workflow in `.github/workflows/tests.yml` draait daarna automatisch `compileall` en `pytest`.

---

## 3. Configuratie

De wizard heeft vijf stappen.

### 3.1 Prijs en MID-meter (verplicht)

| Veld | Toelichting |
|---|---|
| Naam | Naam van de configuratie-entry. |
| Dynamische prijssensor | Sensor met de **all-in** importprijs in EUR/kWh, bijvoorbeeld Tibber, EFlux of een eigen template-sensor. |
| MID-meter laadpaal | Cumulatieve kWh-meter van de **interne MID-meter in de laadpaal**. Gebruik geen losse meter in de meterkast. |

De prijssensor mag ook `ct/kWh` of `EUR/MWh` als eenheid hebben; de integratie rekent dit om. De MID-meter mag `kWh`, `Wh` of `MWh` zijn.

### 3.2 Entiteiten koppelen (optioneel)

| Veld | Domein | Nodig voor |
|---|---|---|
| Laadvermogen EV | sensor | Power-flow en solar-surplus-detectie. |
| Laadstroom EV | sensor | Nauwkeuriger load balancing. |
| EV aangesloten | binary_sensor | Voorkomen dat een lege laadpaal wordt gestart. |
| Laadpaal aan/uit | switch | Pauzeren/hervatten van de laadpaal. |
| Laadstroom-instelling | number | Dynamisch verlagen/verhogen van de laadstroom. |
| Accuvermogen | sensor | Arbitrage- en statusberekening. |
| Accu-SOC | sensor | Nodig voor het automatische accuplan. |
| Netvermogen | sensor | Power-flow en fallback voor netstroom. |
| Netstroom | sensor | **Belangrijk:** moet de stroom van de hele aansluiting meten, inclusief laadpaal en warmtepomp. |
| PV-vermogen | sensor | Power-flow, solar surplus en PV-energie. |
| Huisbelasting | sensor | Power-flow en arbitrage-ontlading. Wordt anders geschat. Gebruik bij voorkeur een sensor **exclusief** EV en warmtepomp; HEMS telt die er voor de accu-analyse zelf bij. |
| Warmtepompvermogen | sensor | Power-flow en load-balancing-fallback. |
| Warmtepomp-COP | sensor | Weergave. |

**Belangrijk voor load balancing:** de netstroomsensor moet de totale stroom op de hoofdzekering meten. HEMS trekt daar de EV-stroom vanaf om de overige belasting te bepalen. Een sensor die alleen de huisgroep meet, geeft een onveilige uitkomst. Zie §7.

### 3.3 Financieel

| Parameter | Standaard | Betekenis |
|---|---:|---|
| Werkgeversvergoeding | € 0,25/kWh | Vergoeding per geladen kWh. |
| ERE-opbrengst | € 0,124/kWh | Netto ERE-vergoeding per geladen kWh. |
| Minimale arbitragemarge | € 0,08/kWh | Minimale spread voordat netladen interessant is. |
| Accucapaciteit | 32 kWh | Twee keer 16 kWh. |
| Round-trip-rendement | 90% | Verliezen bij laden/ontladen. |

Deze waarden zijn later ook via `number.hems_*` aan te passen.

### 3.4 Net en EV

| Parameter | Standaard | Betekenis |
|---|---:|---|
| Fasen | 3 | Aantal fasen. |
| Fasespanning | 230 V | Nominale spanning per fase. |
| Maximale netstroom | 25 A | Per fase, dus 3×25 A. |
| Minimale EV-stroom | 6 A | Onder deze waarde pauzeert de laadpaal. |
| Maximale EV-stroom | 16 A | Bovengrens van de laadpaal. |
| Veiligheidsreserve | 1 A | Wordt altijd vrijgehouden. |
| HEMS mag laadpaal aansturen | aan | Uit = alleen meten, geen laadpaalcommando's. |
| Teken netvermogen omkeren | uit | Zet aan als je net-sensor positief is bij export. |
| Teken accuvermogen omkeren | uit | Zet aan als je accu-sensor positief is bij ontladen. |

### 3.5 Deye Modbus

| Parameter | Standaard | Betekenis |
|---|---:|---|
| Modbus-verbinding | uit | Lezen/pingen van de Deye. |
| IP/hostname | leeg | Adres van de omvormer. |
| TCP-poort | 502 | Modbus TCP-poort. |
| Unit/slave-ID | 1 | Meestal 1. |
| Fysieke schrijfacties | uit | **Pas inschakelen na verificatie.** |

Als je Modbus-verbinding inschakelt, test de wizard de verbinding. Bij een ongeldig adres krijg je een blokkierende foutmelding; de configuratie kan dan niet worden afgerond.

### Later wijzigen

Ga naar de integratie → **Configureren**. Je kunt entiteiten, financiële parameters, netlimieten en Modbus apart aanpassen. De integratie herlaadt na opslaan.

---

## 4. Entiteiten

De device-naam is **HEMS**. Daardoor krijgen de belangrijkste entiteiten voorspelbare ID's.

### Financieel

| Entiteit | Eenheid | Betekenis |
|---|---|---|
| `sensor.hems_net_profit_today` | € | Netto resultaat van vandaag. |
| `sensor.hems_ev_employer_reimbursement_today` | € | Werkgeversvergoeding vandaag. |
| `sensor.hems_ere_earnings_today` | € | ERE-opbrengst vandaag. |
| `sensor.hems_battery_arbitrage_profit_today` | € | Gerealiseerde accu-arbitragewinst vandaag. |
| `sensor.hems_ev_net_profit_today` | € | EV-opbrengst minus EV-importkosten. |
| `sensor.hems_net_profit_month` | € | Netto resultaat deze maand. |
| `sensor.hems_ev_employer_reimbursement_month` | € | Werkgeversvergoeding deze maand. |
| `sensor.hems_ere_earnings_month` | € | ERE-opbrengst deze maand. |
| `sensor.hems_battery_arbitrage_profit_month` | € | Arbitragewinst deze maand. |

### Energie en laadsessie

| Entiteit | Eenheid |
|---|---|
| `sensor.hems_ev_energy_today` | kWh |
| `sensor.hems_ev_energy_month` | kWh |
| `sensor.hems_ev_import_cost_today` | € |
| `sensor.hems_grid_import_today` | kWh |
| `sensor.hems_grid_export_today` | kWh |
| `sensor.hems_pv_energy_today` | kWh |
| `sensor.hems_arbitrage_charge_today` | kWh |
| `sensor.hems_arbitrage_discharge_today` | kWh |
| `sensor.hems_session_energy` | kWh |
| `sensor.hems_session_cost` | € |
| `sensor.hems_session_profit` | € |
| `sensor.hems_last_session_energy` | kWh |

### Live en status

| Entiteit | Eenheid |
|---|---|
| `sensor.hems_pv_power` | W |
| `sensor.hems_grid_power` | W |
| `sensor.hems_battery_power` | W |
| `sensor.hems_house_load` | W |
| `sensor.hems_ev_power` | W |
| `sensor.hems_heatpump_power` | W |
| `sensor.hems_battery_soc` | % |
| `sensor.hems_heatpump_cop` | – |
| `sensor.hems_current_price` | EUR/kWh |
| `sensor.hems_max_allowed_ev_current` | A |
| `sensor.hems_available_grid_current` | A |
| `sensor.hems_system_status` | – |
| `sensor.hems_plan_reason` | – |
| `sensor.hems_planned_target_soc` | % |
| `sensor.hems_ev_actuation` | – |
| `sensor.hems_modbus_last_error` | – |
| `sensor.hems_last_update` | timestamp |

### Binaire sensoren

| Entiteit | Betekenis |
|---|---|
| `binary_sensor.hems_modbus_connected` | Deye bereikbaar. |
| `binary_sensor.hems_fallback_active` | Communicatieverlies > 60 s. |
| `binary_sensor.hems_ev_connected` | Auto aangesloten. |
| `binary_sensor.hems_ev_charging` | EV laadt. |
| `binary_sensor.hems_ev_charging_paused` | Load balancer heeft de EV gepauzeerd. |

### Schakelaars

| Entiteit | Functie |
|---|---|
| `switch.hems_auto_arbitrage` | Automatisch accu-/prijsplan aan/uit. Bij uitzetten schrijft HEMS één veilige toestand (Grid Charge uit, Solar Sell aan, Target SOC 100) en stopt daarna met automatische accu-aansturing. |
| `switch.hems_auto_ev_charging` | Automatisch EV-laadbeleid aan/uit. |
| `switch.hems_force_ev_charging` | Forceer EV-laden; load balancing blijft actief. |
| `switch.hems_force_grid_charge` | Forceer netladen van de accu. |

### Nummers

| Entiteit | Bereik |
|---|---|
| `number.hems_employer_rate` | 0–5 EUR/kWh |
| `number.hems_ere_rate` | 0–2 EUR/kWh |
| `number.hems_min_arbitrage_margin` | 0–1 EUR/kWh |
| `number.hems_max_grid_current` | 1–125 A |

### Tekenconventies

- **Netvermogen:** positief = import, negatief = export.
- **Accuvermogen:** positief = laden, negatief = ontladen.
- **EV-vermogen:** positief = laden.
- **PV en huis:** positief = opwek/verbruik.

Heb je een sensor met het omgekeerde teken, zet dan de betreffende inversie-optie aan.

---

## 5. Rekenmodel

### 5.1 EV-sessie

Voor iedere laadsessie:

```text
Importkosten    = Σ (kWh_ev,t × Prijs_t)
Werkgever       = kWh_ev,totaal × Tarief_werkgever
ERE             = kWh_ev,totaal × Tarief_ERE
Netto winst     = Werkgever + ERE − Importkosten
```

Het acceptatievoorbeeld uit de opdracht:

```text
40 kWh × € 0,25 = € 10,00 werkgever
40 kWh × € 0,124 = € 4,96 ERE
40 kWh × € 0,10 = € 4,00 import
Netto = € 10,00 + € 4,96 − € 4,00 = € 10,96
```

Dit is automatisch getest in `tests/test_financial_engine.py`.

### 5.2 Dag- en maandcijfers

De integratie houdt per dag en per maand de volgende grootheden bij:

- EV-kWh, EV-importkosten, werkgeversvergoeding en ERE-opbrengst;
- accu-arbitrage: kWh geladen uit het net, kosten daarvan, kWh ontladen en vermeden importwaarde;
- netimport, netexport en PV-energie.

De netto dagwinst is:

```text
Netto = Werkgever + ERE − EV-importkosten
      + Arbitrage-ontladen × prijs − Arbitrage-laadkosten
```

De arbitragewinst wordt alleen gerealiseerd als er daadwerkelijk eerder uit het net is geladen. Zonne-energie die later wordt ontladen telt dus niet als net-arbitrage. De opslagpool wordt met het ingestelde round-trip-rendement gevuld: 1 kWh netladen levert ongeveer 0,9 kWh bruikbare ontlading op. De pool wordt ook afgeboekt wanneer de accu de EV of een export voedt; alleen lokaal vermeden import wordt als winst gewaardeerd.

Als prijs- of SOC-data tijdens een actief netlaadcommando wegvalt, schakelt HEMS na drie opeenvolgende lege plannen (ongeveer 90 seconden) automatisch terug naar Grid Charge uit en Solar Sell aan.

### 5.3 Persistente opslag

De integratie bewaart de ledger in Home Assistant `.storage`:

- dag- en maandtellers;
- 400 dagen daghistorie;
- laatste 100 laadsessies;
- de actieve sessie;
- ingestelde tarieven en limieten;
- de laatste MID-meterstand.

Na een herstart wordt een MID-verschil tot 7 dagen meegenomen tegen de actuele prijs en als **geschat** gemarkeerd. Dit voorkomt dat een korte herstart een laadsessie laat verdwijnen, maar het is geen vervanging voor een echte intervalmeter.

---

## 6. Prijs- en accuplan

### 6.1 Prijsvoorspelling

De integratie leest de prijsvoorspelling uit de attributen van je prijssensor. Ondersteund worden onder andere:

- `raw_today` en `raw_tomorrow` (Tibber-stijl);
- `today` en `tomorrow`;
- `prices` of `forecast`.

Een item mag een dictionary zijn met `start`/`time` en `price`/`value`, of een los getal (dan worden uurintervallen vanaf middernacht aangenomen). De verwachte eenheid is **EUR/kWh**. De genormaliseerde lijst staat in het attribuut `prices` van `sensor.hems_current_price` en wordt door het dashboard gebruikt.

### 6.2 Automatische accusturing

De planner kijkt 18 uur vooruit en hanteert deze regels:

1. **Negatieve of nulprijs:** laden toestaan tot 100% SOC, Solar Sell uit.
2. **Winstgevende spread:** als `piekprijs − huidige prijs ≥ marge / rendement`, laden tot 100%, Solar Sell uit.
3. **Accu vol (≥ 98%):** niet netladen.
4. **Hoge prijs:** Target SOC 20%, Solar Sell aan, zodat de accu de dure uren kan dekken.
5. **Overige uren:** Target SOC 75%, Solar Sell aan; ruimte voor PV en geen geforceerde netimport.

Alleen bij een wijziging wordt er geschreven. De integratie schrijft **niet** elke 30 seconden dezelfde waarden opnieuw.

> De Deye gebruikt per tijdslot een Target SOC. Deze versie schrijft de waarden voor **slot 1** (registers 166 en 172), conform de opdrachtspecificatie. Als jouw omvormer op dat moment in een ander TOU-slot zit, kan het interne TOU-schema het effect bepalen. Controleer dit op je firmware en stem de TOU-tijden af.

### 6.3 Automatische EV-sturing

Automatisch EV-laden wordt apart bestuurd door `switch.hems_auto_ev_charging`; `auto_arbitrage` hoeft daarvoor niet aan te staan. Het is aan als:

- de auto aangesloten is (indien een connected-sensor is gekoppeld), en
- de importprijs plus marge lager is dan `werkgeversvergoeding + ERE`, **of**
- er voldoende netexport is (ongeveer de minimale laadstroom × 3 × 230 V × 0,8).

Bij `auto_ev_charging` uit doet HEMS geen laadpaalcommando's; load balancing blijft wel de veilige stroom berekenen. Met `force_ev_charging` laadt de auto ongeacht prijs, maar nooit boven de veilige netcapaciteit.

De laadpaal wordt alleen **automatisch gestart** als er een `number`-entiteit voor de laadstroom is gekoppeld. Met alleen een aan/uit-schakelaar kan HEMS de laadpaal wel pauzeren, maar niet veilig op een lagere stroom starten.

Gebruik je **evcc**, koppel dan de door evcc aangemaakte `number`-entiteit voor de laadstroom en, indien beschikbaar, de bijbehorende `switch`. HEMS blijft dan de veilige stroomlimiet en het prijsbeleid bepalen; evcc verzorgt de voertuigcommunicatie en het laadprotocol.

---

## 7. Dynamic load balancing

Iedere 5 seconden, plus direct bij een wijziging van de net- of EV-sensor:

```text
I_overig     = I_net − I_ev
I_beschikbaar = I_hoofdzekering − I_overig − I_reserve
I_doel       = min(I_max_ev, floor(I_beschikbaar))
```

Als `I_doel < I_min_ev` (standaard 6 A), wordt de laadpaal gepauzeerd. De laadpaal wordt pas hervat als er weer voldoende ruimte is.

Voorbeeld uit de acceptatietest:

| Situatie | Waarde |
|---|---:|
| Hoofdzekering | 25 A |
| Netstroom totaal | 34 A |
| EV-stroom | 16 A |
| Overige belasting | 18 A |
| Reserve | 1 A |
| Beschikbaar | 6 A |
| Nieuwe EV-limiet | 6 A |

Dit is automatisch getest in `tests/test_financial_engine.py`.

**Randvoorwaarde:** de netstroomsensor moet de hele aansluiting meten. Als hij alleen de huisgroep meet, is de uitkomst onveilig. Zonder netmeting zet HEMS de laadstroom op 0 (gepauzeerd) en toont de integratie `measured: false`; automatisch starten of verhogen doet HEMS in die situatie niet. De berekening gaat uit van een gebalanceerde driefasenaansluiting. Bij een enkelfasige laadpaal op één fase blijft fasebewaking door de laadpaal of installateur nodig.

---

## 8. Deye Modbus

### 8.1 Registers

| Register | Functie | Bereik |
|---:|---|---:|
| 1100 | Write lock/unlock | 0/1 |
| 130 | Grid Charge Enable | 0/1 |
| 145 | Solar Sell Slot 1 | 0/1 |
| 166 | Target SOC Slot 1 | 0–100 |
| 172 | Charge Enable Slot 1 (bit 0) | 0/3 |

### 8.2 Schrijfsequentie

Bij iedere wijziging:

1. Schrijf `1` naar register **1100**.
2. Schrijf `130` (Grid Charge) met functiecode `0x10`.
3. Schrijf `172` (Slot 1 charge enable).
4. Schrijf `145` (Solar Sell).
5. Schrijf `166` (Target SOC).
6. Lees de vier registers terug en vergelijk.
7. Schrijf `0` naar register **1100** — ook als stap 2–6 faalt.

Als het opnieuw vergrendelen mislukt, sluit de client de socket en rapporteert een fout. De omvormer blijft nooit bewust ontgrendeld.

### 8.3 Fallback

De integratie pingt de omvormer iedere 30 seconden. Na meer dan 60 seconden zonder communicatie:

- gaat de status naar **Fallback - inverter internal TOU**;
- worden er geen nieuwe Modbus-schrijfacties gedaan;
- blijft de omvormer werken met de laatst weggeschreven instellingen en zijn interne TOU-schema.

De Deye valt niet automatisch terug op een apart schema dat de integratie niet kent. Zorg daarom dat het interne TOU-schema van de omvormer veilig is en dat een communicatiestoring niet leidt tot ongewenst netladen. Bij herstel schrijft HEMS eerst een veilige toestand (Grid Charge uit, Solar Sell aan, Target SOC 100) en pakt daarna het automatische plan weer op.

### 8.4 Firmwarewaarschuwing

Deye heeft meerdere hybride modellen en firmwareversies met afwijkende registermappen. De adressen in deze integratie komen uit de opdrachtspecificatie. Verifieer vóór productie:

- of 130/145/166/172 op jouw model dezelfde betekenis hebben;
- of write multiple/single holding register wordt ondersteund;
- of de gekozen slave-ID klopt;
- of de omvormer na een testwrite correct terugleest.

Gebruik bij twijfel eerst een Modbus-testtool of de officiële Deye-documentatie.

---

## 9. Dashboard

Het bestand `dashboards/hems_dashboard.yaml` bevat een volledig dashboard. Nodig zijn drie HACS-frontendkaarten:

- `power-flow-card-plus`
- `apexcharts-card`
- `button-card`

### Optie A — YAML-dashboard (aanbevolen, URL `/hems-dashboard`)

Kopieer `dashboards/hems_dashboard.yaml` naar `/config/dashboards/hems_dashboard.yaml` en voeg toe aan `configuration.yaml`:

```yaml
lovelace:
  mode: storage
  dashboards:
    hems-dashboard:
      mode: yaml
      title: HEMS
      icon: mdi:home-lightning-bolt
      show_in_sidebar: true
      filename: dashboards/hems_dashboard.yaml
```

Herstart Home Assistant. Het dashboard verschijnt in de zijbalk en is bereikbaar op `/hems-dashboard`.

### Optie B — UI-dashboard

1. **Instellingen → Dashboards → Dashboard toevoegen**.
2. Titel `HEMS`, URL `hems-dashboard`, zijbalk tonen.
3. Open het dashboard → **Bewerken → Raw configuration editor**.
4. Plak de inhoud van `dashboards/hems_dashboard.yaml`, maar laat de bovenste regel `title: HEMS` weg als de editor daarover klaagt. De configuratie begint dan met `views:`.

Het dashboard gebruikt de proxy-sensoren van de integratie, zodat je niet telkens je eigen entiteitsnamen hoeft aan te passen. De prijsgrafiek leest `attributes.prices` van `sensor.hems_current_price`.

---

## 10. Services

| Service | Functie |
|---|---|
| `hems_master.recalculate` | Ververs entiteiten en herbereken het grootboek. |
| `hems_master.reset_today` | Archiveer en wis de dagtellers. Onomkeerbaar. |
| `hems_master.apply_plan` | Evalueer en pas het plan direct toe. |
| `hems_master.export_audit` | Geef een auditpayload terug. |

Voorbeeld van een audit-export in een automation:

```yaml
action:
  - service: hems_master.export_audit
    response_variable: hems_audit
  - service: notify.persistent_notification
    data:
      title: HEMS audit
      message: "{{ hems_audit | tojson }}"
```

De payload bevat:

- dag- en maandcijfers;
- actieve en afgeronde laadsessies;
- MID-meterstanden;
- tarieven;
- historie.

De service bevat geen Modbus-host of wachtwoorden.

---

## 11. ERE en fiscaliteit

De integratie rekent met een **netto ERE-tarief per kWh** dat jij zelf instelt. Ze doet geen aannames over de inboekcommissie, de marktprijs of de fiscale status.

Achtergrond uit de opdracht:

- ERE volgt uit RED-III en de Nederlandse implementatie via wetsvoorstel 36.766.
- Brandstofleveranciers hebben een jaarlijks stijgende hernieuwbare-energieverplichting.
- Thuisladen moet via een laadpaal met **ingebouwde MID-meter** worden gemeten.
- De machtiging aan een inboekdienstverlener geldt minimaal één kalenderjaar.
- Volgens Belastingdienst Kennisgroepstandpunt **KG:204:2026:16** vormt de ERE-vergoeding voor zakelijke rijders in de beschreven situatie geen belastbaar loon uit dienstbetrekking.

Dit is **geen fiscaal advies**. Controleer je eigen situatie bij de Belastingdienst en je inboekdienstverlener. Bewaar de MID-sessies en de export uit `hems_master.export_audit` voor een NEa-steekproef.

---

## 12. Test- en acceptatieprotocol

### Geautomatiseerd

```bash
pytest -q
```

De tests dekken:

- de winstberekening uit SW-03 (inclusief het € 10,96-voorbeeld);
- negatieve importprijzen;
- de laadpaal-derating en pauze uit SW-04;
- de arbitragebeslissing bij lage, negatieve en hoge prijzen.

### Handmatig / op locatie

| ID | Test | Verwacht resultaat |
|---|---|---|
| SW-01 | Integratie toevoegen met ongeldig Modbus-IP. | Wizard blokkeert met **Cannot connect**. |
| SW-02 | Reg 130/145/166/172 wijzigen met writes aan. | Unlock 1100 → writes → verify → lock 1100; geen exception. |
| SW-03 | 40 kWh tegen € 0,10, € 0,25 en € 0,124. | Netto € 10,96. |
| SW-04 | Netstroom 34 A bij 16 A EV op 25 A zekering. | Binnen 5 s terug naar 6 A; bij < 6 A pauzeren. |
| SW-05 | Modbus > 60 s weg. | Status fallback, geen nieuwe writes; Deye blijft op interne TOU. |
| TP-01 | 35 mm², 24,5 Nm, 100 A automaten. | Visueel/mechanisch en NEN 1010 goedgekeurd. |
| TP-02 | Unlock/lock-transactie. | Reg 130 neemt waarde aan; zonder unlock geen write. |
| TP-03 | Ongeldige functie/register. | Exception frame binnen 500 ms. |
| TP-04 | Zero Export 0 W, 3 kW huislast. | Netimport beperkt tot balanceringswaarde. |
| TP-05 | Reg 172=1, 130=1, 166=100. | Deye laadt uit net. |
| TP-06 | Weheat 10 minuten monitoren. | Data ververst binnen 120 s. |
| TP-07 | MID-meter en audit-export. | Meterstand herleidbaar uit interne MID-meter. |

Voor TP-01 en de elektrische veiligheid geldt: laat dit uitvoeren door een gekwalificeerde installateur. De integratie vervangt geen NEN 1010-keuring.

---

## 13. Architectuur

```text
custom_components/hems_master/
├── __init__.py          setup, services, reload/unload
├── manifest.json        metadata en pymodbus-requirement
├── config_flow.py       UI-wizard en opties
├── const.py             constanten, registers, defaults
├── coordinator.py       30 s polling, 5 s balancing, 60 s plan, ledger
├── financial_engine.py  zuivere formules en laadpaal-/accu-algoritmen
├── modbus_client.py     Deye TCP-client met lock/unlock en readback
├── sensor.py            financiële, energie-, power- en statusentiteiten
├── binary_sensor.py     Modbus, fallback, EV-status
├── switch.py            auto/force-overrides
├── number.py            tarieven en netlimiet
├── diagnostics.py       geredigeerde diagnostiek
├── services.yaml        service-UI
├── strings.json         Engelse bronstrings
└── translations/
    ├── en.json
    └── nl.json
dashboards/hems_dashboard.yaml
tests/test_financial_engine.py
```

De coordinator is de enige plek die schrijft. Alle schrijfacties worden geserialiseerd met een `asyncio.Lock`; de Modbus-client heeft daarnaast zijn eigen transactielock. Zo kunnen een handmatige switch en de uurplanner elkaar niet interleaven.

---

## 14. Beperkingen en operationele aandachtspunten

1. **Geen energieprijs bij herstart:** een MID-verschil tijdens downtime wordt tegen de actuele prijs gewaardeerd en als schatting gemarkeerd. Voor exacte kosten is een intervalmeter of een historical price lookup nodig.
2. **MID-precisie:** de resolutie van de MID-meter bepaalt hoe vaak een sessie-energie-delta wordt gezien. Een meter met 0,1 kWh-resolutie geeft minder frequente updates.
3. **EV-actuator:** zonder `number`-entiteit kan HEMS niet veilig moduleren. Met alleen een schakelaar kan ze alleen pauzeren.
4. **Deye TOU:** de integratie schrijft alleen de registers uit de specificatie. Andere firmware kan een andere registermap hebben.
5. **Warmtepomp:** alleen monitoring; geen automatische temperatuurboost in deze versie.
6. **Netmeting:** load balancing is alleen veilig met een whole-service-stroomsensor of een betrouwbare afgeleide meting.
7. **Handmatige override:** `force_grid_charge` blijft actief tot je hem uitzet of Home Assistant herstart. Bij een HA-crash kan de Deye de laatst geschreven toestand vasthouden. Gebruik dit niet onbeheerd.
8. **Modbus-security:** Modbus TCP is onversleuteld. Zet de Deye niet rechtstreeks op internet; gebruik een geïsoleerd VLAN of firewallregel.
9. **Auditdata:** de export bevat energie- en laadgegevens. Behandel die als persoonsgegevens.

---

## 15. Ontwikkeling

De zuivere rekenmodule is bewust vrij van Home Assistant-imports, zodat de formules los getest kunnen worden. De integratie draait op Home Assistant 2024.6 of nieuwer en Python 3.12.

```bash
pytest -q
```

Voor een handmatige controle van de Python-syntax:

```bash
python -m compileall custom_components/hems_master
```

---

## 16. Licentie en aansprakelijkheid

Dit project is bedoeld als technische basis en moet vóór productie worden gevalideerd tegen jouw exacte Deye-model, firmware, laadpaal, MID-meter en netaansluiting. De software wordt geleverd zonder garantie. De gebruiker blijft verantwoordelijk voor elektrische veiligheid, netcode, ERE-regelgeving en fiscale behandeling.
