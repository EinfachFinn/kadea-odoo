"""Live-Mitschreiben: mehrere Personen bearbeiten dieselbe Seite gleichzeitig.

Ablauf (Protokoll von prosemirror-collab):

1. ``collab_join``: Wer in den Schreibmodus wechselt, bekommt den Ausgangstext der laufenden Sitzung
   und alle bisherigen Schritte. Läuft keine Sitzung, startet eine neue mit dem aktuellen Dateiinhalt.
2. ``collab_push``: Eigene Schritte werden nur angenommen, wenn sie auf dem neuesten Stand aufbauen.
   Sonst holt sich der Browser erst die fremden Schritte, setzt seine eigenen darauf und schickt erneut.
3. Nach jeder Annahme gibt der Odoo-Bus allen auf der Seite Bescheid, die dann per ``collab_pull`` nachladen.
4. Gespeichert wird weiterhin als Datei über ``write_page``. In einer Sitzung zählt nur der neueste
   bestätigte Stand. Änderungen außerhalb der Sitzung (z. B. Checkbox im Lesemodus) werden erkannt
   und zusammengeführt statt überschrieben.
"""

import json
import os
import secrets
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .wissen_vault import MD_EXT

SESSION_IDLE = 40  # Sekunden ohne Lebenszeichen -> Sitzung gilt als beendet
ACTIVITY_THROTTLE = 10  # Lebenszeichen höchstens so oft in die Datenbank schreiben
MAX_STEPS_PER_PUSH = 500


class WissenVaultCollab(models.AbstractModel):
    _inherit = "wissen.vault"

    # ------------------------------------------------------------------
    # Hilfen
    # ------------------------------------------------------------------
    def _collab_rel(self, path, write=True):
        (self._check_write if write else self._check_read)()
        root = self._root()
        full = self._abs(path, root)
        rel = self._rel(full, root)
        self._check_path(rel, write=write)
        if not rel.endswith(MD_EXT):
            raise UserError(self.env._("Nur Seiten können gemeinsam bearbeitet werden."))
        return full, rel

    def _collab_session(self, rel, token):
        session = self.env["wissen.collab.session"].sudo().search([("path", "=", rel)], limit=1)
        return session if session and session.token == token else None

    def _collab_touch(self, session):
        now = fields.Datetime.now()
        if not session.last_activity or (now - session.last_activity).total_seconds() > ACTIVITY_THROTTLE:
            session.last_activity = now

    def _collab_notify(self, rel, payload):
        """Allen, die gerade auf der Seite sind (auch eigenen weiteren Tabs), über den Bus Bescheid geben."""
        since = fields.Datetime.now() - timedelta(seconds=SESSION_IDLE)
        users = self.env["wissen.presence"].sudo().search([("path", "=", rel), ("last_seen", ">=", since)]).user_id
        users |= self.env.user
        users.partner_id._bus_send("wissen_collab", dict(payload, path=rel))

    @staticmethod
    def _collab_steps_payload(steps):
        return [{"version": s.version, "client_id": s.client_id, "step": json.loads(s.data)} for s in steps]

    # ------------------------------------------------------------------
    # API für den Editor
    # ------------------------------------------------------------------
    @api.model
    def collab_join(self, path):
        full, rel = self._collab_rel(path)
        Session = self.env["wissen.collab.session"].sudo()
        session = Session.search([("path", "=", rel)], limit=1)
        now = fields.Datetime.now()
        alive = session and session.last_activity and (now - session.last_activity).total_seconds() < SESSION_IDLE
        if not alive:
            content = ""
            mtime = 0.0
            if os.path.isfile(full):
                with open(full, encoding="utf-8", errors="replace", newline="") as fh:
                    content = fh.read()
                mtime = os.path.getmtime(full)
            vals = {"token": secrets.token_hex(8), "version": 0, "base": content, "saved_version": 0,
                    "saved_mtime": mtime, "saved_content": content, "last_activity": now}
            if session:
                session.step_ids.unlink()
                session.write(vals)
            else:
                session = Session.create(dict(vals, path=rel))
        else:
            session.last_activity = now
        return {
            "token": session.token,
            "version": session.version,
            "base": session.base or "",
            "steps": self._collab_steps_payload(session.step_ids),
        }

    @api.model
    def collab_push(self, path, token, version, steps, client_id):
        """Nimmt Schritte an, wenn sie auf ``version`` (dem neuesten Stand) aufbauen."""
        _full, rel = self._collab_rel(path)
        session = self._collab_session(rel, token)
        if not session:
            return {"ok": False, "reset": True}
        if not steps or len(steps) > MAX_STEPS_PER_PUSH:
            return {"ok": False}
        # Gleichzeitige Pushes derselben Seite nacheinander abarbeiten
        self.env.cr.execute("SELECT id FROM wissen_collab_session WHERE id = %s FOR UPDATE", [session.id])
        session.invalidate_recordset(["version"])
        if int(version) != session.version:
            return {"ok": False, "version": session.version}
        Step = self.env["wissen.collab.step"].sudo()
        Step.create([{
            "session_id": session.id,
            "version": session.version + i,
            "client_id": str(client_id),
            "data": json.dumps(step),
            "user_id": self.env.uid,
        } for i, step in enumerate(steps)])
        session.write({"version": session.version + len(steps), "last_activity": fields.Datetime.now()})
        self._collab_notify(rel, {"kind": "steps", "token": token, "version": session.version})
        return {"ok": True, "version": session.version}

    @api.model
    def collab_pull(self, path, token, since):
        """Alle Schritte ab ``since``. Dient zugleich als Lebenszeichen der Sitzung."""
        _full, rel = self._collab_rel(path)
        session = self._collab_session(rel, token)
        if not session or int(since) > session.version:
            return {"reset": True}
        self._collab_touch(session)
        steps = session.step_ids.filtered(lambda s: s.version >= int(since))
        return {"version": session.version, "steps": self._collab_steps_payload(steps)}

    @api.model
    def collab_cursor(self, path, token, client_id, version, anchor, head):
        """Cursor-Position an die anderen weitergeben (nur über den Bus, nichts wird gespeichert)."""
        _full, rel = self._collab_rel(path)
        if not self._collab_session(rel, token):
            return False
        user = self.env.user
        self._collab_notify(rel, {
            "kind": "cursor", "token": token, "client_id": client_id, "user_id": user.id, "name": user.name,
            "version": int(version), "anchor": anchor, "head": head,
        })
        return True

    @api.model
    def collab_leave(self, path, token, client_id):
        _full, rel = self._collab_rel(path)
        if self._collab_session(rel, token):
            self._collab_notify(rel, {"kind": "leave", "token": token, "client_id": client_id})
        return True

    # ------------------------------------------------------------------
    # Speichern während einer Sitzung
    # ------------------------------------------------------------------
    @api.model
    def write_page(self, path, content, expected_mtime=None, force=False, collab=None):
        if not collab:
            return super().write_page(path, content, expected_mtime, force)
        full, rel = self._collab_rel(path)
        session = self._collab_session(rel, collab.get("token"))
        if not session:
            return super().write_page(path, content, expected_mtime, force)
        self.env.cr.execute("SELECT id FROM wissen_collab_session WHERE id = %s FOR UPDATE", [session.id])
        session.invalidate_recordset()
        version = int(collab.get("version") or 0)
        disk_mtime = os.path.getmtime(full) if os.path.exists(full) else 0.0
        if version <= session.saved_version and not force:
            # Jemand aus der Sitzung hat diesen oder einen neueren Stand schon gespeichert
            page = self._index_file(full, rel) if os.path.exists(full) else None
            return {"conflict": False, "skipped": True, "mtime": disk_mtime, "path": rel,
                    "meta": self._page_meta(page, full) if page else False}
        if os.path.exists(full) and abs(disk_mtime - (session.saved_mtime or 0.0)) > 0.001 and not force:
            # Datei wurde außerhalb der Sitzung geändert (Lesemodus, Aufgabe angelegt, Version wiederhergestellt …)
            with open(full, encoding="utf-8", errors="replace", newline="") as fh:
                disk = fh.read()
            base = session.saved_content or ""
            session.write({"saved_mtime": disk_mtime, "saved_content": disk})
            if disk != base:
                page = self._index_file(full, rel, disk)
                return {"conflict": True, "content": disk, "base": base, "mtime": disk_mtime,
                        "last_editor": page.last_editor_id.name or False}
        res = super().write_page(path, content, None, True)
        session.write({"saved_version": version, "saved_mtime": res["mtime"], "saved_content": content,
                       "last_activity": fields.Datetime.now()})
        return res
