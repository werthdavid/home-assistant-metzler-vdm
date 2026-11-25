# Metzler VDM10 Home-Assistant-Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Custom Component (HACS), die gültige RFID-Entriegelungen der Metzler VDM10 per ISAPI-Polling als `event`-Entität pro Tür bereitstellt.

**Architecture:** Ein `DataUpdateCoordinator` pollt alle 2 s `AcsEvent` (major 5, minor 1) über gepinntes HTTPS mit Digest-Auth. Eine HA-freie Klasse `UnlockTracker` übernimmt Deduplizierung, Erststart-Regel und Positivliste. Entitäten hängen am Coordinator; Fehler führen nie zu einem Event.

**Tech Stack:** Python 3.13, Home Assistant ≥ 2025.6, aiohttp ≥ 3.12 (`DigestAuthMiddleware`, `Fingerprint`), pytest mit `pytest-homeassistant-custom-component`.

**Spec:** `docs/superpowers/specs/2026-11-25-metzler-vdm-design.md` (ergänzend das Handoff-Dokument aus der Session)

## Global Constraints

- Domain: `metzler_vdm`. Mindestversion HA `2025.6.0`.
- Nur `https://<host>` mit `ssl=aiohttp.Fingerprint(<32 Bytes SHA-256>)`. Kein `verify_ssl=False`, kein HTTP, kein Fallback.
- Nur `major: 5`, `minor: 1`. Nur IDs aus der Positivliste feuern.
- `cardNo` und `name` verlassen `api.py` nie: nicht in Datenklassen, Logs, Attributen, Diagnose, Exceptions.
- Passwort nie in Logs, Diagnose oder Fehlermeldungen. Exceptions enthalten keine Antwort-Rohdaten.
- Bei jedem Fehler: kein Event, Erreichbarkeit "aus".
- Konstanten (in `const.py`): `POLL_INTERVAL = 2`, `WINDOW_SECONDS = 60`, `END_AHEAD_HOURS = 24`, `PAGE_SIZE = 10`, `MAX_PAGES = 10`, `REQUEST_TIMEOUT = 5`, `SEEN_TTL = 7200`, `DRIFT_THRESHOLD = 10`, `DRIFT_CHECK_INTERVAL = 3600`.
- Tests und Fixtures nur mit Platzhaltern (`"1001"`, `"PLACEHOLDER_CARD"`, `"PLACEHOLDER_NAME"`, Host `192.0.2.10`).
- Testlauf: `.venv/bin/pytest -q` (venv mit Python 3.13).
- Code, Bezeichner und Commit-Messages englisch; README und `translations/de.json` deutsch.

## Review Focus

1. Antwort ohne `InfoList` bei `numOfMatches: 0` (Normalfall im Leerlauf): leere Liste, Poll gilt als erfolgreich. → Task 2
2. Datensatz ohne oder mit leerem `employeeNoString` (Karte ohne Person): wird übersprungen, kein Absturz, die übrigen Datensätze werden verarbeitet. → Task 2
3. Während des Blätterns (`MORE`) kommt ein neuer Eintrag hinzu und derselbe Datensatz erscheint auf zwei Seiten: genau ein Event. → Task 1
4. HTTP 200 mit Nicht-JSON (HTML-Fehlerseite) oder JSON ohne `AcsEvent`: Poll schlägt fehl, kein Event. → Task 2
5. Positivlisten-Eingabe mit Leerzeichen, Leerzeilen, doppelter ID oder Zeile ohne `=`: Leerzeichen werden entfernt, Leerzeilen ignoriert, Zeile ohne `=` nutzt die ID als Alias, doppelte ID ist ein Formularfehler. IDs werden als Zeichenketten exakt verglichen (`"0101"` ≠ `"101"`). → Task 1

---

### Task 1: Gerüst und Tracker-Logik

**Files:**
- Create: `custom_components/metzler_vdm/__init__.py` (vorerst leer), `custom_components/metzler_vdm/const.py`, `custom_components/metzler_vdm/tracker.py`
- Create: `custom_components/metzler_vdm/manifest.json`, `hacs.json`, `.gitignore`, `requirements_test.txt`, `pyproject.toml` (nur pytest-Konfiguration: `asyncio_mode = "auto"`, `testpaths = ["tests"]`)
- Create: `tests/__init__.py`, `tests/conftest.py` (Fixture `auto_enable_custom_integrations` autouse)
- Test: `tests/test_tracker.py`

**Interfaces:**
- Produces:

```python
# tracker.py (keine HA-Imports)
@dataclass(frozen=True, slots=True)
class AcsRecord:
    time: str          # unverändert aus der Antwort, z. B. "2026-11-25 08:57:25"
    employee_no: str
    door_no: int

@dataclass(frozen=True, slots=True)
class Unlock:
    employee_no: str
    alias: str
    door_no: int
    time: str

class AllowlistError(ValueError): ...

def parse_allowlist(text: str) -> dict[str, str]: ...   # Zeilen "ID=Alias"
def format_allowlist(allowlist: Mapping[str, str]) -> str: ...

class UnlockTracker:
    def __init__(self, allowlist: Mapping[str, str], seen_ttl: float = SEEN_TTL) -> None: ...
    @property
    def primed(self) -> bool: ...
    def process(self, records: Iterable[AcsRecord], now: float) -> list[Unlock]: ...
```

`manifest.json`: `domain`, `name: "Metzler VDM"`, `config_flow: true`, `iot_class: "local_polling"`, `integration_type: "device"`, `requirements: []`, `version: "0.1.0"`, `codeowners: []`, `documentation`/`issue_tracker` als Platzhalter-URL. `hacs.json`: `{"name": "Metzler VDM", "homeassistant": "2025.6.0", "render_readme": true}`.

- [ ] **Step 1: Testumgebung anlegen**

Run: `python3.13 -m venv .venv && .venv/bin/pip install -q -r requirements_test.txt` (Inhalt: `pytest-homeassistant-custom-component`)
Expected: Installation ohne Fehler, `.venv/bin/python -c "import aiohttp; print(aiohttp.DigestAuthMiddleware)"` gibt die Klasse aus.

- [ ] **Step 2: Fehlschlagende Tests schreiben** (`tests/test_tracker.py`)

```python
R = AcsRecord("2026-11-25 08:57:25", "1001", 1)
ALLOW = {"1001": "Alias A"}

def test_first_poll_only_primes():
    t = UnlockTracker(ALLOW)
    assert t.process([R], now=0) == []
    assert t.primed
    assert t.process([R], now=2) == []

def test_first_poll_empty_then_new_record_fires_once():
    t = UnlockTracker(ALLOW)
    t.process([], now=0)
    assert t.process([R], now=2) == [Unlock("1001", "Alias A", 1, "2026-11-25 08:57:25")]
    assert t.process([R], now=4) == []

def test_unknown_id_is_dropped_and_remembered():
    t = UnlockTracker(ALLOW); t.process([], now=0)
    assert t.process([AcsRecord("2026-11-25 08:57:25", "9999", 1)], now=2) == []

def test_duplicate_within_one_batch_fires_once():        # Review Focus 3
    t = UnlockTracker(ALLOW); t.process([], now=0)
    assert len(t.process([R, R], now=2)) == 1

def test_same_second_different_id_or_door_both_fire():
    t = UnlockTracker({"1001": "A", "1002": "B"}); t.process([], now=0)
    recs = [R, AcsRecord(R.time, "1002", 1), AcsRecord(R.time, "1001", 2)]
    assert len(t.process(recs, now=2)) == 3

def test_seen_key_kept_while_still_reported():
    t = UnlockTracker(ALLOW, seen_ttl=300); t.process([], now=0)
    t.process([R], now=2)
    for now in range(4, 400, 2):
        assert t.process([R], now=now) == []

def test_seen_key_pruned_after_ttl_without_sighting():
    t = UnlockTracker(ALLOW, seen_ttl=300); t.process([], now=0)
    t.process([R], now=2); t.process([], now=303)
    assert t.process([R], now=304) != []   # belegt nur das Verwerfen; real liegt R dann außerhalb des 60-s-Fensters

def test_parse_allowlist():                               # Review Focus 5
    assert parse_allowlist(" 1001 = Alias A \n\n0101\n") == {"1001": "Alias A", "0101": "0101"}
    with pytest.raises(AllowlistError):
        parse_allowlist("1001=A\n1001=B")
    assert parse_allowlist(format_allowlist({"1": "x"})) == {"1": "x"}
```

- [ ] **Step 3: Fehlschlag prüfen**

Run: `.venv/bin/pytest tests/test_tracker.py -q`
Expected: FAIL (ImportError)

- [ ] **Step 4: `const.py` und `tracker.py` implementieren**

`UnlockTracker` hält `dict[tuple[str, str, int], float]` (Schlüssel → zuletzt gesehen). `process` aktualisiert den Zeitstempel für jeden gemeldeten Schlüssel, verwirft danach Schlüssel mit `now - last_seen > seen_ttl`. Der erste Aufruf setzt `primed` und gibt immer `[]` zurück. Unbekannte IDs: `_LOGGER.debug` mit ID und Tür, ohne weitere Felder.

- [ ] **Step 5: Tests grün**

Run: `.venv/bin/pytest tests/test_tracker.py -q`
Expected: 8 passed

- [ ] **Step 6: Commit** `feat: scaffold integration and unlock tracker`

---

### Task 2: ISAPI-Client

**Files:**
- Create: `custom_components/metzler_vdm/api.py`
- Test: `tests/test_api.py`, `tests/fixtures.py` (Antwort-Bausteine mit Platzhaltern)

**Interfaces:**
- Consumes: `AcsRecord` aus Task 1, Konstanten aus `const.py`.
- Produces:

```python
class MetzlerError(Exception): ...
class MetzlerConnectionError(MetzlerError): ...     # Timeout, Verbindungsfehler, HTTP 5xx
class MetzlerAuthError(MetzlerError): ...           # HTTP 401/403
class MetzlerCertificateError(MetzlerError): ...    # aiohttp.ServerFingerprintMismatch
class MetzlerResponseError(MetzlerError): ...       # unerwartetes Format, MAX_PAGES überschritten
class MetzlerNotSupportedError(MetzlerError): ...   # HTTP 404 / notSupport

def parse_fingerprint(text: str) -> bytes: ...      # Hex, toleriert ":" , Leerzeichen, Groß/Klein; sonst ValueError
def format_fingerprint(raw: bytes) -> str: ...      # "AB:CD:..." in Großbuchstaben
async def async_fetch_fingerprint(host: str, port: int = 443) -> bytes: ...

class MetzlerClient:
    def __init__(self, session: aiohttp.ClientSession, host: str, fingerprint: bytes) -> None: ...
    async def async_search_valid_card_events(self, start: datetime, end: datetime) -> list[AcsRecord]: ...
    async def async_get_device_time(self, default_tz: tzinfo) -> datetime: ...   # immer tz-aware
```

Die Session bringt die Digest-Middleware mit (wird in Task 3 erzeugt); der Client setzt pro Request `ssl=aiohttp.Fingerprint(fingerprint)` und `timeout=ClientTimeout(total=REQUEST_TIMEOUT)`.
`async_fetch_fingerprint` öffnet eine TLS-Verbindung ohne Zertifikatsprüfung (`asyncio.open_connection`, `ssl.CERT_NONE`), liest nur das DER-Zertifikat, sendet keine Daten und schließt. Rückgabe: SHA-256 des DER.
Request-Body: wie im Handoff-Beispiel; `searchID` ist pro Suche eine neue `uuid4().hex`, `searchResultPosition` wächst um `numOfMatches`. `start`/`end` müssen tz-aware sein und werden als `isoformat(timespec="seconds")` gesendet.
`async_get_device_time`: `GET /ISAPI/System/time`, XML-Element `localTime` (ISO 8601). Ohne Offset wird `default_tz` angenommen. Am Gerät ungeprüft.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

Die Tests ersetzen `session.request` durch einen Fake, der vorbereitete Antworten (Status, Body) der Reihe nach liefert und die Aufrufe aufzeichnet.

```python
async def test_search_sends_pinned_https_request(fake_session):
    # 1 Antwort "OK" mit 1 Datensatz
    assert records == [AcsRecord("2026-11-25 08:57:25", "1001", 1)]
    assert call.url == "https://192.0.2.10/ISAPI/AccessControl/AcsEvent?format=json"
    assert isinstance(call.kwargs["ssl"], aiohttp.Fingerprint)
    cond = call.kwargs["json"]["AcsEventCond"]
    assert (cond["major"], cond["minor"], cond["maxResults"], cond["searchResultPosition"]) == (5, 1, 10, 0)
    assert cond["startTime"] == "2026-11-25T08:56:00+01:00"

async def test_search_follows_more_paging(fake_session):
    # Seite 1: MORE, 10 Datensätze; Seite 2: OK, 3 Datensätze
    assert len(records) == 13
    assert [c.kwargs["json"]["AcsEventCond"]["searchResultPosition"] for c in calls] == [0, 10]

async def test_search_aborts_after_max_pages(fake_session):        # jede Seite MORE
    with pytest.raises(MetzlerResponseError): ...
    assert len(calls) == 10

async def test_no_matches_without_infolist_is_empty(fake_session): # Review Focus 1
    # {"AcsEvent": {"responseStatusStrg": "NO MATCH", "numOfMatches": 0, "totalMatches": 0}}
    assert records == []

async def test_record_without_employee_no_is_skipped(fake_session): # Review Focus 2
    # 3 Datensätze: ohne Feld, mit "", gültig
    assert records == [AcsRecord("2026-11-25 08:57:25", "1001", 1)]

async def test_record_with_wrong_minor_is_skipped(fake_session):    # Station ignoriert den Filter
    assert records == []

@pytest.mark.parametrize("body", ["<html>error</html>", "{}", '{"AcsEvent": []}'])
async def test_malformed_response_raises(fake_session, body):       # Review Focus 4
    with pytest.raises(MetzlerResponseError): ...

async def test_error_mapping(fake_session):
    # 401 -> MetzlerAuthError, 500 -> MetzlerConnectionError, TimeoutError -> MetzlerConnectionError,
    # aiohttp.ClientConnectionError -> MetzlerConnectionError,
    # aiohttp.ServerFingerprintMismatch -> MetzlerCertificateError

async def test_pii_never_leaves_client(fake_session, caplog):
    # Antwort enthält "PLACEHOLDER_CARD" und "PLACEHOLDER_NAME"; caplog auf DEBUG
    assert "PLACEHOLDER" not in repr(records) and "PLACEHOLDER" not in caplog.text
    # dasselbe für str(exc) bei einer fehlerhaften Antwort, die die Platzhalter enthält

def test_parse_fingerprint():
    assert parse_fingerprint("ab:CD " * 16) == bytes.fromhex("abcd" * 16)
    for bad in ("", "abcd", "zz" * 32): pytest.raises(ValueError, parse_fingerprint, bad)
    assert parse_fingerprint(format_fingerprint(b"\x01" * 32)) == b"\x01" * 32

async def test_device_time(fake_session):
    # "<Time><localTime>2026-11-25T08:57:25+01:00</localTime></Time>" -> aware datetime
    # ohne Offset -> default_tz; HTTP 404 -> MetzlerNotSupportedError
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `.venv/bin/pytest tests/test_api.py -q`
Expected: FAIL (ImportError)

- [ ] **Step 3: `api.py` implementieren**

Ein Datensatz wird nur übernommen, wenn `major == 5`, `minor == 1`, `time` eine nichtleere Zeichenkette, `employeeNoString` nach `strip()` nichtleer und `doorNo` eine Ganzzahl ist. Alle anderen Felder werden nicht gelesen. Exceptions tragen nur einen festen Text und ggf. den HTTP-Status.

- [ ] **Step 4: Tests grün**

Run: `.venv/bin/pytest tests/test_api.py -q`
Expected: alle bestanden

- [ ] **Step 5: Commit** `feat: add pinned ISAPI client for AcsEvent search`

---

### Task 3: Coordinator, Setup, Entitäten, Repair-Issues

**Files:**
- Create: `custom_components/metzler_vdm/coordinator.py`, `event.py`, `binary_sensor.py`
- Modify: `custom_components/metzler_vdm/__init__.py`
- Test: `tests/test_init.py`, `tests/test_coordinator.py`, `tests/conftest.py` (Fixtures `mock_client`, `config_entry`)

**Interfaces:**
- Consumes: `MetzlerClient`, Exceptions, `UnlockTracker`, `Unlock`.
- Produces:

```python
# Config-Entry
# data:    {CONF_HOST, CONF_USERNAME, CONF_PASSWORD, CONF_FINGERPRINT: "<64 Hex-Zeichen klein>"}
# options: {CONF_ALLOWLIST: {"<id>": "<alias>"}, CONF_TIMEZONE: "Europe/Berlin", CONF_DOORS: [1]}
type MetzlerConfigEntry = ConfigEntry[MetzlerCoordinator]

class MetzlerCoordinator(DataUpdateCoordinator[list[Unlock]]):
    def __init__(self, hass, entry: MetzlerConfigEntry, client: MetzlerClient) -> None: ...
    clock_drift: float | None      # Sekunden, Station minus HA; None = unbekannt/nicht unterstützt
```

- `__init__.py`: `async_setup_entry` erzeugt die Session mit `async_create_clientsession(hass, middlewares=(aiohttp.DigestAuthMiddleware(user, password),))`, den Client und den Coordinator, ruft `async_config_entry_first_refresh` **nicht** auf, sondern `async_refresh()` (Station offline beim Start darf das Setup nicht blockieren), setzt `entry.runtime_data`, lädt `Platform.EVENT` und `Platform.BINARY_SENSOR`, registriert einen Update-Listener, der den Entry neu lädt.
- `_async_update_data`: Fenster `[now - 60 s, now + 24 h]` in der Stationszeitzone (`zoneinfo.ZoneInfo(options[CONF_TIMEZONE])`), Suche, `tracker.process(records, time.monotonic())`, Rückgabe der neuen `Unlock`s. Fehlerabbildung:
  - `MetzlerCertificateError` → Issue `certificate_mismatch` (`IssueSeverity.ERROR`, `is_fixable=False`, Übersetzungsschlüssel gleichnamig), `UpdateFailed`.
  - `MetzlerAuthError` → `ConfigEntryAuthFailed`.
  - übrige `MetzlerError` → `UpdateFailed`.
  - Erfolg → Issue `certificate_mismatch` löschen.
- Uhrzeit-Prüfung im selben Coordinator: beim ersten erfolgreichen Poll und danach alle `DRIFT_CHECK_INTERVAL` Sekunden `async_get_device_time`. `abs(drift) >= DRIFT_THRESHOLD` → Issue `clock_drift` (`IssueSeverity.WARNING`, Platzhalter `seconds`), sonst Issue löschen. `MetzlerNotSupportedError` → Prüfung dauerhaft aus, ein Debug-Log. Andere Fehler der Zeitabfrage lassen den Poll **nicht** fehlschlagen.
- `event.py`: `ValidUnlockEvent(CoordinatorEntity[MetzlerCoordinator], EventEntity)`, eine Instanz pro Tür aus `options[CONF_DOORS]`, `_attr_event_types = ["valid_unlock"]`, `_attr_has_entity_name = True`, `translation_key = "valid_unlock"`, `unique_id = f"{entry.entry_id}_door_{door}_valid_unlock"`. In `_handle_coordinator_update` nur bei `coordinator.last_update_success` für jeden `Unlock` der eigenen Tür `_trigger_event("valid_unlock", {"employee_no", "alias", "door", "time"})`. `Unlock`s für nicht konfigurierte Türen: Debug-Log, kein Event.
- `binary_sensor.py`: `BinarySensorDeviceClass.CONNECTIVITY`, `EntityCategory.DIAGNOSTIC`, `is_on = coordinator.last_update_success`, `available` immer `True` (soll "aus" zeigen, nicht "nicht verfügbar").
- Gemeinsames `DeviceInfo`: `identifiers={(DOMAIN, entry.entry_id)}`, `manufacturer="Metzler"`, `model="VDM10"`.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

`mock_client` patcht `MetzlerClient` in `__init__.py` durch einen `AsyncMock` mit einstellbarem `async_search_valid_card_events`; die Zeit wird mit `freezer` und `async_fire_time_changed` um 2 s weitergeschaltet.

```python
async def test_setup_creates_entities(hass, config_entry, mock_client):
    # event.<...>_valid_unlock mit state "unknown", binary_sensor "on"

async def test_restart_does_not_refire(hass, config_entry, mock_client):     # Abnahme 3
    # Client liefert ab dem ersten Poll [R]; nach 3 Polls: Event-State weiterhin "unknown"

async def test_allowed_unlock_fires_exactly_once(hass, config_entry, mock_client):   # Abnahme 1
    # Poll 1: [], Poll 2..4: [R] -> genau 1 state_changed der Event-Entität
    # Attribute == {"event_type": "valid_unlock", "employee_no": "1001", "alias": "Alias A", "door": 1,
    #               "time": "2026-11-25 08:57:25", ...}; kein Attribut enthält "PLACEHOLDER"

async def test_unlisted_id_fires_nothing(hass, config_entry, mock_client):   # Abnahme 2

async def test_unconfigured_door_fires_nothing(hass, config_entry, mock_client):

async def test_query_window(hass, config_entry, mock_client, freezer):
    # start == now - 60 s, end == now + 24 h, beide mit tzinfo der Stationszeitzone

async def test_error_sets_connectivity_off_and_recovers(hass, config_entry, mock_client):   # Abnahme 5
    # MetzlerConnectionError für 15 Polls -> binary_sensor "off", kein Event
    # danach [R_neu] -> binary_sensor "on", genau 1 Event (R_neu liegt im Fenster)

async def test_certificate_mismatch(hass, config_entry, mock_client):        # Abnahme 4
    # MetzlerCertificateError -> binary_sensor "off", kein Event, Issue "certificate_mismatch" vorhanden
    # nächster Erfolg -> Issue gelöscht

async def test_auth_error_starts_reauth(hass, config_entry, mock_client):
    # MetzlerAuthError -> hass.config_entries.flow.async_progress() enthält einen reauth-Flow

async def test_setup_with_station_offline_still_loads(hass, config_entry, mock_client):
    # erster Poll schlägt fehl -> Entry LOADED, binary_sensor "off"; erster erfolgreicher Poll primed nur

async def test_clock_drift_issue(hass, config_entry, mock_client):
    # Gerätezeit = now + 30 s -> Issue "clock_drift"; Gerätezeit = now + 2 s -> kein Issue
    # MetzlerNotSupportedError -> kein Issue, async_get_device_time wird danach nicht mehr aufgerufen
    # MetzlerConnectionError bei der Zeitabfrage -> binary_sensor bleibt "on"
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `.venv/bin/pytest tests/test_init.py tests/test_coordinator.py -q`
Expected: FAIL

- [ ] **Step 3: Implementieren** (Verhalten siehe Interfaces)

- [ ] **Step 4: Tests grün**

Run: `.venv/bin/pytest -q`
Expected: alle bestanden

- [ ] **Step 5: Commit** `feat: add coordinator, event and connectivity entities`

---

### Task 4: Config-Flow, Optionen, Reauth, Reconfigure

**Files:**
- Create: `custom_components/metzler_vdm/config_flow.py`, `strings.json`, `translations/en.json`, `translations/de.json`
- Test: `tests/test_config_flow.py`

**Interfaces:**
- Consumes: `async_fetch_fingerprint`, `format_fingerprint`, `MetzlerClient`, Exceptions, `parse_allowlist`, `format_allowlist`, Entry-Format aus Task 3.
- Produces: Config-Entries im Format aus Task 3, `unique_id` = Host in Kleinbuchstaben.

Schritte:
1. `user`: Host, Benutzer, Passwort. Holt den Fingerabdruck. Fehler `cannot_connect`.
2. `fingerprint`: zeigt den Fingerabdruck (`AB:CD:…`) als Platzhalter im Beschreibungstext, Pflicht-Checkbox `confirm`. Ohne Haken Fehler `not_confirmed`. Danach Probeabfrage (`async_search_valid_card_events` über die letzten 60 s) mit gepinnter Verbindung. Fehler `invalid_auth`, `cannot_connect`, `certificate_changed`, `unexpected_response`.
3. `access`: Positivliste (mehrzeiliges Textfeld, `ID=Alias`), Zeitzone (Auswahl aus `zoneinfo.available_timezones()`, Default `hass.config.time_zone`), Türen (kommagetrennte Ganzzahlen ≥ 1, Default `1`). Fehler `invalid_allowlist`, `invalid_doors`. Eine leere Positivliste ist erlaubt (dann feuert nichts).
- Options-Flow: Formular wie `access`, vorbelegt.
- `reauth`: nur Passwort, Probeabfrage, dann `async_update_reload_and_abort`.
- `reconfigure`: Schritte 1 und 2 erneut (neuer Host oder neues Zertifikat), aktualisiert `data`, lässt `options` unverändert.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

```python
async def test_full_flow_creates_entry(hass):
    # data == {host, username, password, fingerprint: "01"*32}
    # options == {allowlist: {"1001": "Alias A"}, timezone: "Europe/Berlin", doors: [1]}

async def test_fingerprint_must_be_confirmed(hass)            # errors == {"base": "not_confirmed"}
async def test_fingerprint_is_shown(hass)                     # description_placeholders["fingerprint"] == "01:01:..."
@pytest.mark.parametrize(("exc", "error"), [
    (MetzlerAuthError, "invalid_auth"), (MetzlerConnectionError, "cannot_connect"),
    (MetzlerCertificateError, "certificate_changed"), (MetzlerResponseError, "unexpected_response")])
async def test_probe_errors(hass, exc, error)
async def test_fetch_fingerprint_failure(hass)                # OSError -> "cannot_connect" im Schritt user
async def test_invalid_allowlist_and_doors(hass)              # "1001=A\n1001=B" -> invalid_allowlist; "0", "a", "" -> invalid_doors
async def test_duplicate_host_aborts(hass)                    # "already_configured"
async def test_options_flow_updates_allowlist_and_reloads(hass, config_entry, mock_client)
async def test_reauth_updates_password(hass, config_entry, mock_client)
async def test_reconfigure_updates_fingerprint_keeps_options(hass, config_entry, mock_client)
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `.venv/bin/pytest tests/test_config_flow.py -q`
Expected: FAIL

- [ ] **Step 3: Implementieren**, inklusive aller Texte in `strings.json`/`en.json` (englisch) und `de.json` (deutsch): Flow-Schritte, Fehler, Abbrüche, Entitätsnamen (`valid_unlock` → "Valid unlock door {door}" / "Gültige Entriegelung Tür {door}", `connectivity`), Issues `certificate_mismatch` und `clock_drift`.

Der Text zum Schritt `fingerprint` sagt, dass der Wert mit dem Zertifikat der Station (Web-UI) verglichen werden muss, bevor bestätigt wird.

- [ ] **Step 4: Tests grün**

Run: `.venv/bin/pytest -q`
Expected: alle bestanden

- [ ] **Step 5: Commit** `feat: add config, options, reauth and reconfigure flows`

---

### Task 5: Diagnose, README, Abschlussprüfung

**Files:**
- Create: `custom_components/metzler_vdm/diagnostics.py`, `README.md`
- Test: `tests/test_diagnostics.py`

**Interfaces:**
- Consumes: `MetzlerConfigEntry`, `MetzlerCoordinator`.
- Produces: `async_get_config_entry_diagnostics(hass, entry) -> dict` mit `entry` (`data` und `options` durch `async_redact_data` mit `TO_REDACT = {CONF_HOST, CONF_USERNAME, CONF_PASSWORD, CONF_FINGERPRINT, CONF_ALLOWLIST}`), `last_update_success`, `clock_drift`, `tracker_primed`, `allowlist_size`.

- [ ] **Step 1: Fehlschlagenden Test schreiben**

```python
async def test_diagnostics_redacted(hass, config_entry, mock_client):
    dump = json.dumps(await async_get_config_entry_diagnostics(hass, config_entry))
    for secret in ("192.0.2.10", "test-user", "test-password", "01" * 32, "1001", "Alias A", "PLACEHOLDER"):
        assert secret not in dump                      # Abnahme 6
    assert '"allowlist_size": 1' in dump
```

- [ ] **Step 2: Fehlschlag prüfen**, **Step 3: implementieren**, **Step 4: grün**

Run: `.venv/bin/pytest -q`
Expected: alle bestanden

- [ ] **Step 5: README schreiben** (deutsch) mit: Zweck und Grenzen, Voraussetzungen (HA ≥ 2025.6, HTTPS an der Station aktiv), Installation über HACS (Custom Repository), Einrichtung inklusive Fingerabdruck-Vergleich, Entitäten und Attribute, Beispiel-Automation, Sicherheitshinweise, Datenschutz-Hinweis (Event-Historie ist ein personenbezogenes Zutrittsprotokoll, Recorder-Aufbewahrung begrenzen), bekannte Grenzen (2-s-Polling, Sekundenauflösung, kein Nachholen, ungeprüfte Punkte: Minimalrechte-Benutzer, `/ISAPI/System/time`, Digest-Middleware am echten Gerät), manuelle Abnahme-Checkliste für die Kriterien 1 bis 5.

Beispiel-Automation (Entitätsnamen als Platzhalter kennzeichnen):

```yaml
automation:
  - alias: "Alarm entschärfen bei gültiger Karte"
    triggers:
      - trigger: state
        entity_id: event.metzler_vdm_gueltige_entriegelung_tuer_1
        not_from: "unavailable"
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.attributes.event_type == 'valid_unlock' }}"
      - condition: state
        entity_id: binary_sensor.metzler_vdm_erreichbarkeit
        state: "on"
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

- [ ] **Step 6: Abschlussprüfung**

Run: `.venv/bin/pytest -q` und `grep -rniE "cardNo|\"name\"" custom_components/ | grep -v strings`
Expected: alle Tests bestanden; `cardNo` kommt in `custom_components/` nicht vor, `name` nur als HA-Metadatum (Manifest, Entitäts-/Gerätename).

- [ ] **Step 7: Commit** `feat: add diagnostics and README`
