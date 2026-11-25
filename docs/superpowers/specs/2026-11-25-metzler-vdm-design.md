# Design: Home-Assistant-Integration Metzler VDM10 (Hikvision ISAPI)

Stand: 2026-11-25. Freigegeben vom Nutzer am 2026-11-25. Grundlage ist das Handoff-Dokument der Analyse-Session.

## Zweck

Gültige RFID-Entriegelungen der Türstation als HA-Events bereitstellen, damit eine Automation die Alarmanlage entschärfen kann.
Sicherheitskritisch: Im Zweifel feuert kein Event.

## Ziele

- `event`-Entität "gültige Entriegelung" pro Tür, Attribute: ID, Alias, Tür, Zeit.
- `binary_sensor` (connectivity) für die Erreichbarkeit.
- Config-Flow mit Zertifikats-Pinning, Positivliste und Zeitzone.
- Beispiel-Automation in der README.

## Nicht-Ziele

Türöffnen aus HA, Kamera, Klingel, Verwaltung von Karten/Personen, Push-Events (am Gerät verifiziert nicht verfügbar).

## Anti-Ziele

- Ein Event ohne verifizierte Gegenstelle (kein HTTP, kein `verify_ssl=False`).
- Ein Event für eine ID außerhalb der Positivliste oder für einen anderen Minor-Code als 1.
- Ein nachgeholtes oder doppelt gemeldetes Alt-Event.
- `cardNo`, `name` oder Passwort in Logs, Attributen oder Diagnosedaten.

## Ansätze

| Ansatz | Bewertung |
| --- | --- |
| Polling von `POST /ISAPI/AccessControl/AcsEvent?format=json` | **gewählt**, am Gerät verifiziert |
| Push über `alertStream` / `subscribeEvent` / `httpHosts` | am Gerät getestet, liefert keine Zutrittsereignisse |
| Hikvision-SDK, Web-UI-Scraping | zu schwergewichtig bzw. unnötig |

HTTP-Client: aiohttp mit `DigestAuthMiddleware` (ab aiohttp 3.12, HA 2025.6) statt httpx, weil HA aiohttp mitbringt und `aiohttp.Fingerprint` das Pinning direkt unterstützt.
Ungeprüft: ob die Middleware mit dem Digest-Verfahren der Station funktioniert (bisher nur mit curl getestet).

## Aufbau

```
custom_components/metzler_vdm/
  __init__.py        Setup, eigene ClientSession, Plattformen
  manifest.json      Mindestversion HA 2025.6
  const.py
  api.py             ISAPI-Client: Digest, Pinning, AcsEvent-Suche mit Paging, Gerätezeit
  tracker.py         reine Logik ohne HA: Dedup, Erststart, Positivliste
  coordinator.py     DataUpdateCoordinator, 2 s, Fehlerbehandlung, Repair-Issues
  config_flow.py     Einrichtung, Reconfigure, Options-Flow
  event.py           Event-Entität pro Tür
  binary_sensor.py   Erreichbarkeit
  diagnostics.py     mit async_redact_data
  strings.json, translations/de.json, translations/en.json
tests/
hacs.json, README.md
```

## Abfragelogik

1. Pro Poll: `startTime` = jetzt minus 60 s, `endTime` = jetzt plus 24 h, `major: 5`, `minor: 1`, `maxResults: 10`. Bei `MORE` mit `searchResultPosition` weiterblättern (Obergrenze 10 Seiten, darüber gilt der Poll als fehlgeschlagen).
2. Zeiten werden in der konfigurierten Stationszeitzone mit Offset formatiert. Das Fenster wird in UTC berechnet und erst danach in die Stationszeitzone umgerechnet (Wanduhr-Arithmetik würde es bei der Zeitumstellung um eine Stunde verschieben).
3. Dedup-Schlüssel: (`time`, `employeeNoString`, `doorNo`). Merkspeicher verwirft Schlüssel, die 7200 s lang in keiner Antwort mehr vorkamen (nicht anhand berechneter Zeiten, wegen Zeitumstellung). 7200 s statt 300 s, weil die Station naive Ortszeiten speichert und Einträge aus der doppelten Stunde am Ende der Sommerzeit im zweiten Durchlauf erneut liefern kann.
4. Erster erfolgreicher Poll nach Start füllt nur den Merkspeicher.
5. Nach einem Ausfall feuern Öffnungen, die noch im 60-s-Fenster liegen. Ältere nicht.
6. Polls überlappen nicht, Timeout 5 s.
7. Ein Poll, dessen Antwort nicht dem erwarteten Schema entspricht, gilt als fehlgeschlagen.

## Sicherheit

- Nur HTTPS, `ssl=aiohttp.Fingerprint(sha256)`. Kein Fallback.
- Config-Flow holt den Fingerabdruck vom Gerät und zeigt ihn zur Bestätigung an. Der Nutzer vergleicht ihn mit dem Zertifikat in der Web-UI der Station.
- Zertifikatsabweichung: kein Event, Erreichbarkeit "aus", Repair-Issue, Behebung über Reconfigure.
- Auth-Fehler (401 nach Digest): Erreichbarkeit "aus", Reauth über Reconfigure.
- Unbekannte IDs: verworfen, Debug-Log ohne Kartennummer und ohne Name.
- Die Integration schaltet nichts selbst.

## Uhrzeit-Abweichung

Das Suchfenster rechnet mit der HA-Uhr, die Station filtert mit ihrer eigenen.
Beim Setup und danach stündlich wird `GET /ISAPI/System/time` gelesen.
Ab 10 s Abweichung: Repair-Issue (Warnung), Events werden weiterhin nicht nachgeholt.
Ungeprüft: ob der Endpunkt auf Firmware V3.7.1 existiert. Falls nicht (404/notSupport), wird die Prüfung still deaktiviert und das im Debug-Log vermerkt.

## Datenschutz

- `cardNo` und `name` werden direkt nach dem Parsen verworfen, nie gespeichert, geloggt oder veröffentlicht.
- Zugangsdaten nur im Config-Entry, Diagnose mit `async_redact_data` (Passwort, Benutzer, Host, Positivliste).
- Tests und Fixtures nur mit Platzhaltern.
- DSGVO-Hinweis in der README: Die Event-Historie in HA ist ein personenbezogenes Zutrittsprotokoll (ID/Alias + Zeit). Aufbewahrung über den Recorder begrenzen.

## Entscheidungen des Umsetzers (nicht vom Nutzer vorgegeben, aber freigegeben)

- Mindestversion HA 2025.6, Python 3.13 für Tests.
- Positivliste und Aliase im Options-Flow, Format eine Zeile pro Eintrag `ID=Alias`.
- Türen: Standard Tür 1, weitere in den Optionen.
- `tracker.py` als HA-freie Logik, damit Dedup/Erststart/Positivliste ohne HA testbar sind.

## Abnahme

Kriterien 1 bis 7 aus dem Handoff. 1 bis 5 brauchen das echte Gerät, 6 und 7 werden durch Unit-Tests abgedeckt.
