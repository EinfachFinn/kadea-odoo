# Social Media Instagram (Odoo 19)

Instagram-Connector für die OCA-Suite `social_media_base` (19.0), aufgebaut wie
`social_media_linkedin`.

**Wichtig: Dieses Modul lädt nichts auf Instagram hoch.** Posts werden in Odoo
geplant. Du veröffentlichst sie selbst in der Instagram-App, und Odoo verknüpft
die Veröffentlichung danach automatisch und liest die Statistiken. Die
Berechtigung zum Veröffentlichen (`instagram_business_content_publish`) wird gar
nicht erst angefragt.

## Funktionen

- **Planen:** Instagram-Konten im normalen Post-Formular auswählen, Format
  (Feed/Karussell, Reel, Story), erster Kommentar (z. B. Hashtags) und interne
  Notizen (Musik, Ort, Collabs …). Instagram-Vorschau im Formular. Der Post
  erscheint im Kalender (`social_media_calendar`).
- **Prüfregeln vorab:** max. 2.200 Zeichen, 30 Hashtags, 20 Erwähnungen, 1–20
  Medien im Karussell, ein Reel = genau ein Video, eine Story = ein Medium.
  Formathinweise für Bilder/Videos.
- **To-do statt Upload:** Sobald ein Post geplant ist, bekommt die
  verantwortliche Person eine Aktivität „Auf Instagram veröffentlichen“, die
  mitwandert, wenn sich das Datum ändert. Zum Zeitpunkt wechselt die
  Veröffentlichung auf **Manuell veröffentlichen**, mit Benachrichtigung,
  Caption und erstem Kommentar zum Kopieren sowie den Medien zum Herunterladen
  (Menü *Social Media › Instagram › Manuell veröffentlichen*).
- **Automatische Erkennung:** Alle 30 Minuten schaut Odoo die neuesten
  Beiträge des Kontos an. Ein Beitrag, dessen Caption wie die geplante beginnt,
  wird automatisch verknüpft. Danach steht der Post auf *Veröffentlicht* und das
  To-do ist erledigt. Alternativ: **Als veröffentlicht markieren**, Link
  einfügen (oder leer lassen, dann sucht Odoo über die Caption).
- **Statistiken:** Aufrufe, Reichweite, Likes, Kommentare, Shares, Saves,
  Engagement-Rate pro Beitrag (täglicher Cron + Button). Konto-Karte im
  Dashboard, Follower-Zahlen, Diagramme im Menü *Charts* (Tag/Woche/Monat) sowie
  Liste/Pivot/Grafik unter *Instagram › Statistiken*. Beiträge, die außerhalb
  von Odoo veröffentlicht wurden, werden ebenfalls importiert.
- **Kommentare:** im Dashboard lesen und beantworten.
- **Token:** Langzeit-Token (60 Tage) werden automatisch verlängert. Läuft die
  Autorisierung trotzdem ab, wird das Konto markiert und die verantwortliche
  Person informiert.

## Einrichtung

1. Abhängigkeiten installieren: `social_media_base`, `social_media_calendar`
   (beide aus OCA/social 19.0).
2. Auf <https://developers.facebook.com/apps> eine App anlegen (Typ *Business*)
   und das Produkt **Instagram › API setup with Instagram login** hinzufügen.
3. Unter *Business login settings* die Redirect-URI eintragen:
   `https://DEINE-ODOO-DOMAIN/social_instagram/callback`. Sie wird auch im
   Verbindungs-Assistenten angezeigt. `web.base.url` muss die öffentliche
   HTTPS-Adresse sein.
4. Das Instagram-Konto muss ein **professionelles Konto** sein (Business oder
   Creator). Solange die App im Entwicklungsmodus ist, das Konto als
   *Instagram Tester* hinzufügen und die Einladung in der App annehmen.
5. In Odoo: *Social Media › Konfiguration › Social Medias › Instagram ›
   Konto hinzufügen*, Instagram App ID und App Secret eintragen und *Verbinden*.

Benötigte Berechtigungen: `instagram_business_basic`,
`instagram_business_manage_insights`, `instagram_business_manage_comments`.

Die Graph-API-Version (Standard `v25.0`) lässt sich über den Systemparameter
`social_media_instagram.graph_version` ändern.

## Bekannte Grenzen

- Die Instagram-API kann keine Beiträge löschen. „Beitrag löschen“ entfernt ihn
  nur aus Odoo.
- Story-Statistiken liefert Instagram nur 24 Stunden lang. Konto-Statistiken
  nur für die letzten ca. 2 Jahre, und Instagram verzögert Daten um bis zu 48
  Stunden.
- Die automatische Erkennung vergleicht den Anfang der Caption (die ersten 60
  Zeichen, ohne Groß/Klein und Leerzeichen). Wurde die Caption in der App stark
  geändert, „Als veröffentlicht markieren“ mit Link verwenden.
