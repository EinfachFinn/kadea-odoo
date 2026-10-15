# Social Media Instagram (Odoo 19)

Instagram-Connector für die KADEA-Social-Basis (`social_media_base` 19.0.1.0.2),
aufgebaut wie `social_media_linkedin`.

**Wichtig: Dieses Modul lädt nichts auf Instagram hoch.** Posts werden in Odoo
geplant. Du veröffentlichst sie selbst in der Instagram-App, und Odoo verknüpft
die Veröffentlichung danach und liest die Statistiken. Die Berechtigung zum
Veröffentlichen (`instagram_business_content_publish`) wird gar nicht erst
angefragt.

## Funktionen

- **Planen:** Instagram-Konten im normalen Post-Formular auswählen. Dazu kommen
  das Format (Feed/Karussell, Reel, Story), ein erster Kommentar (z. B.
  Hashtags), interne Notizen (Musik, Ort, Collabs …) und eine
  Instagram-Vorschau. Der Post erscheint im Kalender (`social_media_calendar`).
- **Prüfregeln (blockierend):** Über `_get_post_errors` der Basis, weil auch die
  Instagram-App sie verweigert: Caption über 2.200 Zeichen, mehr als 30
  Hashtags, mehr als 20 Erwähnungen, kein Bild bzw. Video, mehr als 20 Medien
  im Karussell, ein Reel ist nicht genau ein Video, eine Story hat mehr als ein
  Medium.
- **Prüfregeln (nur Hinweis):** Über `_get_post_warnings`: Bild- und
  Videoformate, sehr große Bilder.
- **To-do statt Upload:** Sobald ein Post geplant ist, bekommt die
  verantwortliche Person eine Aktivität „Publish on Instagram“, die mitwandert,
  wenn sich das Datum ändert. Beim Senden wechselt die Instagram-Veröffentlichung
  auf **To Publish Manually**, der Post ebenfalls, auch wenn LinkedIn im selben
  Post schon veröffentlicht hat. Caption und erster Kommentar stehen zum
  Kopieren bereit, die Medien zum Herunterladen (Menü
  *Social Media › Instagram › To publish manually*).
- **Erkennung der Hand-Veröffentlichung:** Der Cron „Social: Checking social
  media updates“ (alle 2 Stunden) liest die neuesten 25 Beiträge. Ein Beitrag,
  dessen Caption wie die geplante beginnt, wird verknüpft. Danach steht der Post
  auf *Published* und das To-do ist erledigt. Alternativ: **Mark as published**
  mit Link (oder leer lassen, dann sucht Odoo über die Caption).
- **Medien-Import:** Beiträge, die außerhalb von Odoo veröffentlicht wurden,
  werden mit Vorschaubild importiert. Fehlt ein Beitrag in der Liste, fragt Odoo
  bei Instagram nach, bevor er als gelöscht markiert wird.
- **Statistiken pro Beitrag:** Aufrufe, Reichweite, Likes, Kommentare, Shares,
  Saves, Engagement (über `_refresh_post_statistics`, täglicher Cron der Basis
  für die letzten 30 Tage).
- **Statistiken pro Konto:** Tageszeilen in `social.account.statistics` mit
  Aufrufen, Profil-Link-Klicks, Likes, Kommentaren, Shares, Engagement sowie
  Reichweite und Saves. Beim Verknüpfen werden 90 Tage gelesen, danach laufend
  die letzten 7 Tage. Dazu kommen die Dashboard-Karte, die Follower-Zahlen und
  *Instagram › Statistics* (Liste/Pivot/Grafik).
- **Token:** Langzeit-Token (60 Tage) werden automatisch verlängert. Lehnt
  Instagram die Autorisierung ab, markiert die Basis das Konto (*need update*)
  und informiert die verantwortliche Person.

Kommentare lesen oder beantworten ist nicht Teil dieses Moduls.

## Einrichtung

1. Abhängigkeiten: `social_media_base`, `social_media_calendar` (KADEA-Social-Basis).
2. Auf <https://developers.facebook.com/apps> eine App anlegen (Typ *Business*)
   und das Produkt **Instagram › API setup with Instagram login** hinzufügen.
3. Unter *Business login settings* die Redirect-URI eintragen:
   `https://DEINE-ODOO-DOMAIN/social_instagram/callback`. Sie wird auch im
   Verbindungs-Assistenten angezeigt. `web.base.url` muss die öffentliche
   HTTPS-Adresse sein.
4. Das Instagram-Konto muss ein **professionelles Konto** sein (Business oder
   Creator). Solange die App im Entwicklungsmodus ist, das Konto als
   *Instagram Tester* hinzufügen und die Einladung in der App annehmen.
5. In Odoo: *Social Media › Configuration › Social medias › Instagram ›
   Konto hinzufügen*, Instagram App ID und App Secret eintragen und verknüpfen.

Benötigte Berechtigungen: `instagram_business_basic` und
`instagram_business_manage_insights`. Mehr fragt das Modul nicht an.

Die Graph-API-Version (Standard `v25.0`) lässt sich über den Systemparameter
`social_media_instagram.graph_version` ändern.

## Bekannte Grenzen

- Die Instagram-API kann keine Beiträge löschen. „Delete post“ entfernt ihn nur
  aus Odoo, die Meldung sagt das auch so.
- Story-Statistiken liefert Instagram nur 24 Stunden lang. Konto-Statistiken
  gibt es nur für die letzten ca. 2 Jahre, und Instagram liefert Daten mit bis
  zu 48 Stunden Verzögerung.
- Die automatische Erkennung vergleicht den Anfang der Caption (die ersten 60
  Zeichen, ohne Groß/Klein und Leerzeichen). Wurde die Caption in der App stark
  geändert, „Mark as published“ mit Link verwenden.
