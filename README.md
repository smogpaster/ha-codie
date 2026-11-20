# Codie (CODE27) für Home Assistant

Inoffizielle Custom-Integration für den **Codie** von CODE27 (Firmware 1.6.0) über dessen lokale HTTP-API. Alles läuft im LAN, ohne Cloud.

**Funktionen**

- **Kopplung per Code** vom Codie-Display, dauerhafte Zugangsdaten (`auth_scope: permanent`), automatischer Reauth-Flow, falls Codie die Zugangsdaten ablehnt.
- **Aktionen**
  - `codie.say`: Codie liest einen Text mit der Stimme des aktuellen Charakters vor (1–16000 Zeichen, ohne KI-Antwort, ohne Chatverlauf).
  - `codie.notify`: kurze Meldung (max. 200 Zeichen), die Codie spricht, sobald er frei ist. Sie landet im Chatverlauf.
  - `codie.show_image`: JPEG/PNG aus einer URL oder einer lokalen Datei im Vollbild anzeigen, optional mit Anzeigedauer.
- **Sprachsteuerung von Home Assistant über Codie**: Die Integration meldet bei Codie die Werkzeuge `ha_get_state`, `ha_turn_on`, `ha_turn_off`, `ha_toggle` und `ha_activate_scene` an und hält dafür dauerhaft eine WebSocket-Verbindung offen. Danach versteht Codie z. B. „Mach das Licht im Wohnzimmer an“ oder „Wie warm ist es im Bad?“.
- **Entitäten**: `Online`, `Tool-Executor verbunden` und `Charakter-Slot` (mit Bildschirm-, Sperr- und Dialogstatus als Attribute).

## Installation über HACS

1. In HACS oben rechts das Menü (⋮) öffnen und **Benutzerdefinierte Repositories** wählen.
2. Repository `https://github.com/smogpaster/ha-codie` mit dem Typ **Integration** hinzufügen.
3. **Codie (CODE27)** suchen, herunterladen und Home Assistant neu starten.
4. **Einstellungen → Geräte & Dienste → Integration hinzufügen → Codie (CODE27)**.
5. IP-Adresse des Codie (z. B. `192.168.178.112`) und Port `8081` eingeben. Codie muss eingeschaltet, entsperrt und angemeldet sein.
6. Den sechsstelligen Code vom Codie-Display eintippen (5 Minuten gültig; ist er abgelaufen, zeigt Codie automatisch einen neuen).

Tipp: Gib dem Codie in deinem Router eine feste IP-Adresse.

## Welche Geräte darf Codie steuern?

Nur Entitäten, die in Home Assistant **für den Sprachassistenten freigegeben** sind:
**Einstellungen → Sprachassistenten → Entitäten freigeben**. Alles, was dort für „Assist“ freigegeben ist, kann Codie lesen und schalten. Was nicht freigegeben ist, lehnt die Integration ab.

Codie findet Geräte über den Anzeigenamen, die Aliase aus den Entitätseinstellungen, die `entity_id` sowie Kombinationen mit dem Bereich („Wohnzimmer Stehlampe“). Bei mehreren Treffern fragt Codie nach. Schalten lassen sich Lichter, Schalter, Ventilatoren, `input_boolean`, Mediaplayer, Klima, Luftbefeuchter, Sirenen, Fernbedienungen, Automationen, Skripte, Abdeckungen (öffnen/schließen) und Ventile. Schlösser und Alarmanlagen sind absichtlich ausgenommen.

Neue Werkzeuge stehen in **neuen** Codie-Unterhaltungen zur Verfügung. Läuft gerade ein Gespräch, einmal kurz warten oder ein neues beginnen.

## Beispiel-Automationen

Waschmaschine fertig:

```yaml
alias: Codie meldet fertige Waschmaschine
triggers:
  - trigger: state
    entity_id: binary_sensor.waschmaschine_lauft
    from: "on"
    to: "off"
    for: "00:02:00"
actions:
  - action: codie.notify
    data:
      text: "Die Waschmaschine ist fertig. Zeit zum Aufhängen!"
```

Klingel: Foto der Türkamera zeigen und ansagen:

```yaml
alias: Codie zeigt, wer klingelt
triggers:
  - trigger: state
    entity_id: binary_sensor.turklingel
    to: "on"
actions:
  - action: camera.snapshot
    target:
      entity_id: camera.haustur
    data:
      filename: /config/www/codie/klingel.jpg
  - action: codie.show_image
    data:
      path: /config/www/codie/klingel.jpg
      duration: 30
  - action: codie.say
    data:
      text: "Es hat an der Haustür geklingelt."
```

Guten-Morgen-Ansage mit Temperatur:

```yaml
alias: Codie Guten Morgen
triggers:
  - trigger: time
    at: "07:00:00"
actions:
  - action: codie.say
    data:
      text: >-
        Guten Morgen! Draußen sind es
        {{ states('sensor.aussentemperatur') | round(0) | int }} Grad.
```

## Hinweise

- `codie.say` unterbricht ein laufendes Gespräch. `codie.notify` wartet, bis Codie frei ist.
- Bilder: JPEG oder PNG, max. 8 MiB, max. 8192 px Kantenlänge. Lokale Pfade müssen in `allowlist_external_dirs` liegen (`/config/www` und `/media` sind standardmäßig erlaubt).
- Eine Aktion mit `response_variable` liefert bei `say` die `utterance_id`, bei `show_image` die `session_id`.
- Beim Löschen der Integration wird die Werkzeug-Anmeldung auf dem Codie wieder entfernt.
- Fehlersuche: in der Integration (⋮) **Debug-Protokollierung aktivieren**, dann erscheinen Tool-Aufrufe und Verbindungsabbrüche im Log.

Diese Integration steht in keiner Verbindung zu CODE27.
