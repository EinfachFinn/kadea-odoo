# Wissensmanagement (Odoo 19)

Ein Notion-artiges Wiki direkt in Odoo, eng verzahnt mit allen Odoo-Daten: @-Erwähnungen mit Live-Karten,
Aufgaben aus Checklisten, Teilen im Chatter, „Wissen“-Knopf in jedem Formular. Die Seiten liegen als
Markdown-Dateien im Wissensordner auf dem Odoo-Server.

## Funktionen

| Bereich | Umfang |
| --- | --- |
| Inhaltsverzeichnis | Seitenbaum aus Ordnern und `.md`-Dateien, auf- und zuklappbar. Drag & Drop macht eine Seite zur Unterseite. |
| Lesen / Schreiben | Umschalter oben rechts (Strg+E). Lesen ist schön gerendert, Schreiben ist ein **WYSIWYG-Editor**: Man schreibt kein Markdown, gespeichert wird trotzdem `.md`. |
| Editor | Überschriften H1–H6, fett/kursiv/durchgestrichen, Listen, Checklisten, Tabellen (mit Tabellen-UI), Trennlinien, Zitate, Hinweisboxen, **Spalten nebeneinander** (2–3, mit Aufteilung), **Zentriert**, Code, Links, Bilder per Einfügen/Drag & Drop. Speichert automatisch. |
| Seiten | Anlegen (auch als Unterseite), umbenennen, verschieben, duplizieren (inkl. Unterseiten), löschen in den **Papierkorb** (30 Tage wiederherstellbar, mit Unterseiten, Versionen und Kommentaren). |
| Vorlagen | Alle Seiten im Ordner `Vorlagen` werden beim Anlegen angeboten, auch Vorlagen mit Unterseiten. Platzhalter: `{{title}}`, `{{date}}`, `{{time}}`, `{{date:DD.MM.YYYY}}`. |
| Seitenlinks | `[[Seite]]`, `[[Seite\|Alias]]`, `[[Seite#Abschnitt]]`, `![[bild.png]]`, farblich hervorgehoben. Nicht existierende Seiten erscheinen gestrichelt und lassen sich per Klick anlegen. Beim Umbenennen und Verschieben werden die Links in allen Seiten angepasst. |
| @-Erwähnungen | `@cl` schlägt „Claude Bot“ vor, `@aufgabe bank` sucht nur in Aufgaben. Allein in einer Zeile wird die Erwähnung zur **Live-Vorschaukarte** (Phase, Priorität, Zuständige, Tags, Deadline, Betrag …), im Fließtext zum Chip mit Hover-Vorschau. Ein Klick öffnet den Datensatz. |
| Sonstiges | Volltextsuche (`/`), Gliederung, Rückverweise („Verlinkt von“), Unterseiten-Karten, Checkboxen im Lesemodus anklickbar, Frontmatter als Eigenschaften, Hinweisboxen, Spalten, Zentriert, `==markiert==`, `#tags`, „Link zur Seite kopieren“. |
| Stil | Stil-Vorlage (Standard, Notion, Buch, Kompakt), Farben für Akzent, Seitenlinks und Erwähnungen, Schriftgröße, Textbreite und eigenes CSS. |
| Rechte | Gruppen **Leser**, **Bearbeiter** und **Administrator** (Benutzer › Wissensmanagement). |
| Zusammenarbeit | **Live-Mitschreiben** wie in Google Docs mit farbigen Cursorn. „Zuletzt bearbeitet von @Person · vor 5 Minuten“ auf jeder Seite (auch „direkt im Wissensordner geändert“, falls jemand die Datei auf dem Server ändert). Avatare zeigen, wer gerade auf der Seite ist, und ein Banner zeigt, wer gerade bearbeitet. Gleichzeitige Änderungen werden automatisch zusammengeführt. Kommentare (auch zu markierten Textstellen) mit Antworten und „Erledigt“. |
| Versionsverlauf | Jede Bearbeitung erzeugt eine Version. Speichert dieselbe Person mehrfach innerhalb von 10 Minuten, wird das zu einer Version zusammengefasst. Der Ausgangsstand und Änderungen direkt im Ordner werden ebenfalls festgehalten. Ansicht mit farbigem Unterschied (Diff) und **Wiederherstellen**. |
| Seitenleiste | Favoriten (Stern), „Zuletzt bearbeitet“ mit Avatar, eigene Reihenfolge per Drag & Drop (oberes Viertel = davor, unteres = dahinter, Mitte = als Unterseite). |
| Slash-Befehle | Im Editor `/` tippen: Überschriften, Listen, Checkliste, Tabelle, Trennlinie, Zitat, Hinweisbox, Code, Mermaid-Diagramm, Seitenlink, @-Erwähnung, **Aufgabe erwähnen / neue Odoo-Aufgabe anlegen**, **Social-Media-Post erwähnen**, **Vorlage einfügen**, Bild, Datum. |
| Inhalte | `![[Seite]]` bzw. `![[Seite#Abschnitt]]` bettet andere Seiten ein. Mermaid-Diagramme, Syntax-Highlighting, Drucken/PDF. |
| Odoo-Aktionen | Ein Klick auf „Aufgabe“ an einer Checkbox legt eine Odoo-Aufgabe an und verlinkt sie live. „In Chatter teilen“ postet Link und Auszug an einen beliebigen Datensatz. |
| Rückrichtung | In **jeder** Odoo-Formularansicht erscheint ein Button „📖 Wissen (n)“, wenn der Datensatz auf Wissensseiten erwähnt wird. |
| Übersicht | Graph aller Seiten und Links, Tag-Übersicht, verwaiste Seiten und kaputte Links (Button 🗺 in der Seitenleiste). |
| Freigabe | Pro Seite: öffentlicher Link (ohne Login, abschaltbar, Link erneuerbar) oder Kundenportal für ausgewählte Kontakte (`/my/wissen`). Freigegebene Seiten zeigen @-Erwähnungen nur als Namen und nur die eingebundenen Dateien. |
| Ordnerrechte | *Konfiguration › Ordnerrechte*: Pro Ordner festlegen, welche Gruppen lesen bzw. schreiben dürfen (z. B. „Vertrieb“ nur fürs Vertriebsteam). Die Rechte gelten überall: Baum, Suche, Rückverweise, Dateien und Wissen-Button. |


### So funktionieren die @-Erwähnungen

Im Markdown steht `[@Claude Bot](odoo://res.partner/15)`. Auch in der Datei bleibt das ein lesbarer Link mit Namen.
Odoo baut beim Anzeigen jedes Mal eine Karte mit den **aktuellen** Daten des Datensatzes. Die Karte entsteht
generisch aus den Feldern, die das Modell hat (`stage_id`, `state`, `priority`, `user_ids`, `tag_ids`,
`date_deadline`, `amount_total`, `email` …), und funktioniert deshalb für jedes Odoo-Modell. Welche Modelle per @
durchsuchbar sind, legst du in den Einstellungen fest; nicht installierte Modelle werden ignoriert. Es gelten
immer die Zugriffsrechte des Betrachters.

Kurzbefehle zum Filtern: `@kontakt`, `@benutzer`, `@projekt`, `@aufgabe`, `@angebot`, `@lead`, `@einkauf`,
`@rechnung`, `@produkt`, `@mitarbeiter`, `@ticket`, `@termin`, `@lieferung`, `@fertigung`, `@reparatur`, `@gerät`,
`@posts` (Social-Media-Posts, gesucht wird im Post-Text) und `@kanal` (Social-Media-Konten).

### Was passiert, wenn zwei Personen gleichzeitig arbeiten?

1. **Live-Mitschreiben.** Alle im Schreibmodus arbeiten in einer gemeinsamen Sitzung. Jede Eingabe wird als
   ProseMirror-Schritt an Odoo geschickt, nummeriert und über den Odoo-Bus an die anderen verteilt
   ([prosemirror-collab](https://github.com/ProseMirror/prosemirror-collab)). Wer gleichzeitig getippt hat,
   setzt seine Schritte automatisch auf den neuesten Stand (Rebase). Cursor und Markierungen der anderen sind
   farbig mit Namen sichtbar. Rückgängig betrifft nur die eigenen Änderungen.
2. **Man sieht sich.** Oben rechts erscheinen die Avatare aller auf der Seite, ein Banner zeigt, wer gerade schreibt.
3. **Speichern.** Gespeichert wird von denen, die selbst getippt haben, und nur ein vom Server bestätigter Stand.
   Wurde die Datei außerhalb der Sitzung geändert (Checkbox im Lesemodus, Aufgabe angelegt, Version
   wiederhergestellt), wird das per Drei-Wege-Abgleich übernommen und live an alle verteilt.
4. **Lesen aktualisiert sich selbst**, sobald gespeichert wurde.
5. **Es geht nichts verloren:** Jeder Stand liegt im Versionsverlauf und lässt sich wiederherstellen.

## Installation

1. Ordner `wissensmanagement` in einen Addons-Pfad deines Odoo 19 kopieren und das Modul **Wissensmanagement** installieren.
2. Auf dem Server einen Ordner anlegen, z. B. `/var/lib/odoo/wissen`, und dem Odoo-Benutzer Lese- und Schreibrechte geben.
   Den Ordner ins Server-Backup aufnehmen (die Datenbank enthält nur Metadaten wie Versionen und Kommentare).
3. *Wissensmanagement › Konfiguration › Einstellungen*: diesen Pfad als **Wissensordner** eintragen.
4. Benutzern unter *Einstellungen › Benutzer* die Rolle Leser, Bearbeiter oder Administrator geben.
5. Für das Live-Mitschreiben nutzt das Modul den Odoo-Bus (Websocket). Läuft Odoo mit `workers > 0`, muss der
   Reverse-Proxy `/websocket` an den Gevent-Port (Standard 8072) weiterleiten, wie es Odoo ohnehin für Discuss
   und Benachrichtigungen verlangt. Fehlt das, funktioniert es trotzdem, nur mit bis zu 4 s Verzögerung.

Hinweis: Odoo Online (SaaS) erlaubt keine eigenen Module. Möglich sind Odoo.sh (Ordner dann im persistenten
Dateispeicher, z. B. `/home/odoo/data/wissen`) oder eine eigene Installation.

## Technik

- `models/wissen_vault.py`: Dateisystem-Backend (AbstractModel `wissen.vault`). Prüft jeden Pfad auf
  Ausbruch aus dem Wissensordner, schreibt atomar und erkennt Konflikte über `mtime`.
- `controllers/main.py`: Liefert Bilder und Anhänge aus dem Wissensordner aus (`/wissensmanagement/file`).
- `static/src/js/`: OWL-Client-Action. `markdown.js` rendert mit [marked](https://marked.js.org) und DOMPurify,
  `editor.js` nutzt den [Toast UI Editor](https://ui.toast.com/tui-editor) (WYSIWYG, Markdown-nativ),
  `mention_cards.js` baut die Vorschaukarten.
- `static/lib/`: marked 15.0.12 und Toast UI Editor 3.2.2 mit ProseMirror, alles unter MIT-Lizenz.
  Das Toast-UI-Bundle wurde mit esbuild als IIFE gebaut (`window.toastui.Editor`, inkl. de-DE).
- `models/wissen_page.py` und `models/wissen_meta.py`: Metadaten in der Datenbank (Index für Tags, Links und Erwähnungen, „zuletzt bearbeitet von“, Versionen, Kommentare, Anwesenheit, Favoriten, Reihenfolge, Freigaben, Ordnerrechte, Papierkorb). Ein Cronjob gleicht den Index alle 15 Minuten mit dem Ordner ab und leert Papierkorb-Einträge, die älter als 30 Tage sind. Die Metadaten hängen am relativen Pfad: Wer den Wissensordner wechselt, sollte den Index leeren.
- `models/wissen_collab.py` und `static/src/js/collab_sync.js`: Live-Mitschreiben (Sitzung mit Ausgangstext und nummerierten Schritten, `SELECT … FOR UPDATE` gegen gleichzeitige Pushes, Benachrichtigung über den Odoo-Bus plus Abfrage alle 4 s als Sicherheitsnetz). Eine Sitzung endet nach 40 s ohne Lebenszeichen.
- `controllers/share.py`: öffentliche Links, Kundenportal (`/my/wissen`) und Druckansicht.
- `static/lib/`: zusätzlich prosemirror-collab 1.2.2 (MIT, im Toast-UI-Bundle), mermaid 11.4.1 (MIT, wird nur bei Bedarf geladen) und diff-match-patch 1.0.5 (Apache-2.0) für Diff und Zusammenführen.
- `views/share_templates.xml`: QWeb-Templates für freigegebene Seiten, Portal und Druckansicht.
  Das Rendering im Browser liegt in `static/src/public/`.
- Sicherheit: Leser greifen nie direkt per ORM auf die Metadaten-Modelle zu (nur Administratoren haben ACLs),
  sondern ausschließlich über `wissen.vault`. Dort werden Gruppen- und Ordnerrechte geprüft. Die Ordnerrechte
  liegen im `ormcache` und werden bei jeder Änderung einer Regel automatisch geleert.
- Code-Qualität: `ruff` mit Odoos `ruff.toml` ist fehlerfrei, ebenso `pylint-odoo` (außer dem OCA-Autorenfeld).
- Tests: `odoo-bin -d <db> -u wissensmanagement --test-enable --test-tags=/wissensmanagement`
