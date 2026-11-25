# Metzler VDM (Home Assistant)

Benutzerdefinierte Home-Assistant-Integration für die Türstation Metzler VDM10
(Hikvision-OEM, ISAPI). Sie fragt die Station zyklisch nach gültigen
RFID-Entriegelungen ab und meldet sie in Home Assistant als Ereignis. Damit
kann eine Automation z. B. eine Alarmanlage entschärfen.

Der Einsatzzweck ist sicherheitsrelevant. Die Integration ist ein Hilfsmittel
für eine Automation, kein Ersatz für die Zutrittskontrolle der Station.

## Zweck und Grenzen

- Ausgewertet werden ausschließlich Ereignisse "gültige Karte" (ISAPI Major 5,
  Minor 1) für die konfigurierten Türen.
- Nur Karten-IDs auf der Positivliste (Allowlist) lösen ein Ereignis aus.
- Kartennummer und Name aus der Station werden weder gespeichert noch
  protokolliert, angezeigt oder in Diagnosedaten ausgegeben. Verwendet wird
  nur die Mitarbeiter-ID (`employeeNoString`).
- Es wird nur abgefragt (Polling alle 2 s über ein 60-s-Fenster). Es gibt
  keinen Push und kein Nachholen älterer Ereignisse (siehe "Bekannte Grenzen").

## Voraussetzungen

- Home Assistant 2025.6.0 oder neuer.
- HTTPS ist an der Station aktiv (die Verbindung erfolgt ausschließlich per
  HTTPS mit festgelegtem Zertifikat).
- Ein Benutzer der Station mit Zugriff auf die ISAPI. Ob ein Benutzer mit
  minimalen Rechten statt Admin ausreicht, ist ungeprüft.
- Die Station ist per IPv4-Adresse oder Hostname erreichbar. Port 443 ist fest,
  ein anderer Port kann nicht angegeben werden.

## Installation über HACS

1. HACS öffnen, Menü "Benutzerdefinierte Repositories".
2. Die Repository-URL dieses Projekts als Kategorie "Integration" eintragen.
3. "Metzler VDM" installieren und Home Assistant neu starten.

## Einrichtung

Einstellungen, Geräte & Dienste, Integration hinzufügen, "Metzler VDM".

1. Host (Hostname oder IPv4-Adresse, ohne `https://`, Port oder Pfad),
   Benutzername und Passwort eingeben.
2. Die Integration liest das Zertifikat der Station, ohne Zugangsdaten zu
   senden, und zeigt dessen SHA-256-Fingerabdruck an.
3. Diesen Fingerabdruck mit dem Zertifikat in der Web-Oberfläche der Station
   vergleichen und erst bei Übereinstimmung bestätigen (Vertrauen bei der
   ersten Verbindung, "trust on first use"). Erst nach der Bestätigung werden
   Zugangsdaten gesendet, und zwar nur noch an ein Zertifikat mit genau diesem
   Fingerabdruck.
4. Positivliste, Zeitzone der Station und Türen festlegen.

Zugangsdaten und Fingerabdruck liegen im Konfigurationseintrag von Home
Assistant. Sie erscheinen nicht in Logs oder Diagnosedaten.

### Positivliste

- Ein Eintrag pro Zeile im Format `ID=Alias`, z. B. `1001=Alias A`. Eine Zeile
  nur mit `ID` verwendet die ID als Alias.
- IDs werden als exakte Zeichenketten verglichen (`1001` und `01001` sind
  verschiedene IDs). Doppelte IDs werden abgelehnt.
- Eine leere Positivliste bedeutet: es wird nichts ausgelöst.
- Änderbar unter "Konfigurieren" der Integration (Optionen). Eine
  Änderung lädt die Integration neu. Der erste Abruf nach dem Neuladen
  bereitet nur den Zustand vor und löst nichts aus: eine Entriegelung in
  diesen etwa 2 Sekunden kann verpasst werden.

### Neu konfigurieren und Neuanmeldung

- "Neu konfigurieren" (Reconfigure) ändert Host und Zugangsdaten und fragt
  den Fingerabdruck erneut ab. Er muss wieder bestätigt werden.
- Weist die Station das Passwort ab, startet Home Assistant die
  Neuanmeldung (Re-Auth) mit Passwortabfrage.

## Entitäten

Pro Tür und für die Station je Entität (die Entitäts-IDs hängen von der
Sprache ab und sind unten nur Beispiele):

| Entität | Beschreibung |
| --- | --- |
| Ereignis "Gültige Entriegelung Tür N" (`event`, englisch "Valid unlock door N") | Löst bei gültiger Entriegelung einer Karte der Positivliste an Tür N aus. Ereignistyp `valid_unlock`. |
| "Erreichbarkeit" (`binary_sensor`, Diagnose, englisch "Connectivity") | An, solange der letzte Abruf erfolgreich war. Aus bei Ausfall, Zertifikatsabweichung oder abgelehntem Passwort. Nie `unavailable`. |

Attribute des Ereignisses: `employee_no`, `alias`, `door`, `time` (Zeit wie von
der Station gemeldet, Sekundenauflösung).

Zusätzlich wird bei jeder gültigen Entriegelung ein Ereignis auf dem Home-
Assistant-Bus ausgelöst: `metzler_vdm_valid_unlock` mit den Daten `entry_id`,
`entity_id`, `employee_no`, `alias`, `door`, `time`.

### Reparaturmeldungen

- Zertifikat geändert: das Zertifikat der Station passt nicht mehr zum
  bestätigten Fingerabdruck. Solange das so ist, werden keine Ereignisse
  verarbeitet und die Konnektivität ist aus. Die Meldung ist nicht per Klick
  behebbar. Die Behebung erfolgt über "Neu konfigurieren" (neuer Fingerabdruck
  wird angezeigt und muss bestätigt werden), nicht durch stilles Übernehmen.
  Hinweis: der Text der Meldung empfiehlt derzeit stattdessen, die Integration
  zu entfernen und neu einzurichten. Beides führt zum Ziel, "Neu
  konfigurieren" ist der kürzere Weg.
- Uhrabweichung: etwa stündlich wird die Uhr der Station mit Home Assistant
  verglichen. Ab 10 s Abweichung erscheint eine Meldung, weil Ereignisse dann
  verspätet oder gar nicht erkannt werden können. Unterstützt die Firmware
  `/ISAPI/System/time` nicht, wird die Prüfung abgeschaltet.

## Beispiel-Automation

Die Automation löst auf das Bus-Ereignis aus, nicht auf den Zustand der
Ereignis-Entität. Home Assistant stellt den letzten Zustand von
Ereignis-Entitäten nach einem Neustart oder Neuladen der Integration wieder
her (Zeitstempel, dann `unavailable`, dann wieder der alte Zeitstempel). Ein
einfacher Zustands-Trigger hielte das für eine neue Entriegelung und würde den
Alarm entschärfen. Das Bus-Ereignis kann durch Wiederherstellen nicht
entstehen.

```yaml
automation:
  - alias: "Alarm entschärfen bei gültiger Karte"
    triggers:
      - trigger: event
        event_type: metzler_vdm_valid_unlock
        event_data:
          door: 1
    conditions:
      - condition: state
        entity_id: binary_sensor.metzler_vdm_erreichbarkeit   # Platzhalter, eigenen Entitätsnamen eintragen
        state: "on"
      # Optional: nur bestimmte IDs zulassen
      # - condition: template
      #   value_template: "{{ trigger.event.data.employee_no in ['1001'] }}"
      # Empfohlen: zweites Signal, z. B. Anwesenheit des Handys
      # - condition: state
      #   entity_id: person.beispiel
      #   state: "home"
    actions:
      - action: alarm_control_panel.alarm_disarm
        target:
          entity_id: alarm_control_panel.beispiel
        data:
          code: !secret alarm_code
```

**Warnung:** Wer stattdessen auf den Zustand der Ereignis-Entität triggert,
muss `not_from: ["unavailable", "unknown"]` setzen, sonst kann ein Neustart
oder Neuladen wie eine Entriegelung wirken.

## Sicherheitshinweise

- Eine gültige Karte ist ein einzelner Faktor. Karten können kopiert werden
  (je nach Kartentyp). Ein zweites Signal in der Automation (Anwesenheit,
  Tageszeit) ist empfohlen.
- Die Integration ändert nichts an der Station und schaltet keine Tür.
- Der Alarmcode gehört in `secrets.yaml`, nicht in die Automation.
- Zugangsdaten werden nur an das bestätigte, festgelegte Zertifikat gesendet.
  Bei Abweichung wird keine Anfrage mit Zugangsdaten gesendet, die
  Konnektivität geht aus und die Reparaturmeldung erscheint.
- Diagnosedaten enthalten Host, Benutzer, Passwort, Fingerabdruck und
  Positivliste nicht (geschwärzt). Sie zeigen nur Status, Uhrabweichung und die
  Größe der Positivliste.

## Datenschutz (DSGVO)

Die Ereignis-Historie in Home Assistant (Logbuch, Recorder) ist ein
personenbezogenes Zutrittsprotokoll: sie enthält ID bzw. Alias und
Zeitpunkt der Entriegelung. Empfehlung:

- Die Aufbewahrungsdauer des Recorders (`purge_keep_days`) begrenzen und die
  Entitäten bei Bedarf über `recorder: exclude` ausschließen.
- Nur Aliase verwenden, die keine Rückschlüsse über das nötige Maß hinaus
  erlauben.
- Beachten, wer Zugriff auf Logbuch und Verlauf hat.

Kartennummer und Name aus der Station werden von der Integration nie
gespeichert oder angezeigt.

## Bekannte Grenzen

- Polling alle 2 s über ein 60-s-Fenster. Zeitauflösung der Station ist eine
  Sekunde. Mehrere Entriegelungen derselben ID an derselben Tür in derselben
  Sekunde werden als eine gezählt.
- Kein Nachholen: nach einem Ausfall der Station oder von Home Assistant werden
  Ereignisse außerhalb des 60-s-Fensters nicht mehr gemeldet. Der erste Abruf
  nach dem Start oder Neuladen löst nichts aus.
- Nur Ereignisse "gültige Karte" (Major 5, Minor 1). Fingerabdruck, Gesicht,
  Code oder abgelehnte Karten werden nicht ausgewertet.
- Nur IPv4-Adressen oder Hostnamen, ohne Port.
- Ungeprüft am echten Gerät: die Digest-Authentifizierung (Middleware) und das
  Festlegen des Zertifikats (die Unit-Tests simulieren die HTTP-Schicht), die
  Unterstützung von `/ISAPI/System/time` durch die Firmware und ein Benutzer
  mit minimalen Rechten statt Admin.

## Manuelle Abnahme am Gerät

Vor dem produktiven Einsatz prüfen (Positivliste mit mindestens einer
eigenen Karte, Logbuch öffnen):

1. Erlaubte Karte: genau ein Ereignis innerhalb von etwa 3 s.
2. Karte, deren ID nicht auf der Positivliste steht: kein Ereignis.
3. Home Assistant direkt nach einer Entriegelung neu starten: kein zweites
   Ereignis, die Automation löst nicht erneut aus.
4. Falschen Fingerabdruck erzwingen (z. B. Zertifikat der Station tauschen):
   kein Ereignis, Konnektivität aus, Reparaturmeldung sichtbar.
5. Station 30 s offline nehmen und wieder starten: die Integration erholt sich
   ohne Neustart. Entriegelungen außerhalb des 60-s-Fensters werden nicht
   nachgeholt.

## Entwicklung

```
python3.13 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest -q -W error
```
