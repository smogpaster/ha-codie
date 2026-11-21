# Codie für Home Assistant

Stand 21.11.2026 (Version 0.1.1). Quelle: DOKU.md im Repository smogpaster/ha-codie. Details stehen dort in README.md, Bedienung und Beispiel-Automationen ebenfalls in README.md.

## Zweck

Custom-Integration, die den Codie von CODE27 (Firmware 1.6.0) über dessen lokale API mit Home Assistant verbindet. Home Assistant kann Codie sprechen lassen, benachrichtigen und Bilder zeigen. Umgekehrt kann Codie per Sprache Geräte in Home Assistant abfragen und schalten.

## Adressen

- Repository: smogpaster/ha-codie (öffentlich, weil HACS das braucht)
- Releases: GitHub-Releases v0.1.0, v0.1.1
- Installation: HACS bzw. Marketplace als benutzerdefiniertes Repository, Typ Integration
- Keine Netlify-Seite, kein Server: läuft vollständig in Home Assistant

## Technik

Python, asyncio, aiohttp (aus Home Assistant), Config Flow, DataUpdateCoordinator. Keine zusätzlichen Pakete.

## Aufbau

- custom_components/codie/api.py: API-Client mit HMAC-Signatur, unabhängig von Home Assistant
- executor.py: Werkzeug-Katalog anmelden und die Executor-WebSocket dauerhaft offen halten, Reconnect mit Backoff
- catalog.py: die fünf Werkzeuge mit Parametern und Ergebnisschema
- tools.py: Ausführung in Home Assistant, Namensauflösung, Freigabeprüfung, Antworttexte
- config_flow.py: Kopplung mit Code vom Display, Reauth
- services.py: Aktionen say, notify, show_image
- binary_sensor.py, sensor.py: Entitäten
- tests: Tests mit pytest-homeassistant-custom-component

## Funktionen

- Aktionen: codie.say (TTS, 1–16000 Zeichen), codie.notify (max. 200 Zeichen, landet im Chatverlauf), codie.show_image (JPEG/PNG aus URL oder Pfad, optionale Dauer)
- Werkzeuge für Codie: ha_get_state (read), ha_turn_on, ha_turn_off, ha_toggle, ha_activate_scene (reversible)
- Nur Entitäten, die in Home Assistant für Assist freigegeben sind. Schlösser und Alarmanlagen sind ausgenommen.
- Namen: Anzeigename, Aliase, entity_id, Bereich plus Name, deutsche Komposita („Bürolicht“ findet „Büro“)
- Entitäten: Online, Tool-Executor verbunden, Charakter-Slot

## Daten

- Zugangsdaten (local_secret, user_id, auth_scope permanent) im Config Entry von Home Assistant
- Auf dem Codie: eine Tool-Integration mit fester ID 29b3f267-7fe0-5fcd-b2b4-8f04524501d0. Sie wird beim Löschen der Integration entfernt.

## Umgebungsvariablen

Keine.

## Abhängigkeiten

- Codie-API 1.6.0: Signatur nach GUIDE Abschnitt 4, Executor-WebSocket nach openapi.json x-websockets.tool-executor. Die API-Doku liegt lokal in docs/ und ist bewusst nicht im Repository (Betatest).
- Home Assistant ab 2025.11, getestet mit 2026.11

## Veröffentlichen

Kein dev/main-Schema und keine Netlify-Credits. Gearbeitet wird auf main. Eine neue Version heißt: Version in manifest.json erhöhen, Tag vX.Y.Z setzen, GitHub-Release anlegen. Danach in Home Assistant aktualisieren und neu starten. Prüfen vor jedem Release: pytest im Ordner tests (Python 3.13 mit pytest-homeassistant-custom-component).

## Testen

- Lokal: pytest für Werkzeuge, Config Flow, Setup, Aktionen und Reauth
- Am Gerät: Homebrew-Python wird von macOS beim Zugriff aufs lokale Netz blockiert, /usr/bin/python3 funktioniert. Kopplungscodes gelten 5 Minuten.
- Nach einem Neustart von Home Assistant muss der Home-Assistant-Connector in Claude neu verbunden werden.

## Offene Punkte

- Mehr Werkzeuge denkbar: Helligkeit, Temperatur setzen, Mediensteuerung
- Kein eigenes Icon im Brands-Repository
