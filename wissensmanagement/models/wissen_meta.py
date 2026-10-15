"""Zusatzfunktionen des Vaults, die Metadaten in der Datenbank brauchen:
Index (Tags/Links/@-Erwähnungen), "zuletzt bearbeitet von", Versionen, Anwesenheit,
Kommentare, Favoriten, Reihenfolge, Freigaben, Übersichten und Odoo-Aktionen."""

import json
import logging
import os
import re
import secrets
import shutil
import time
from datetime import datetime, timedelta, timezone

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from .wissen_vault import MD_EXT, MENTION_RE, TRASH_DIR, WIKILINK_RE, _natural_key

_logger = logging.getLogger(__name__)

TAG_RE = re.compile(r"(?:^|(?<=\s))#([^\s#.,;:!?()\[\]{}\"'`]+)")
MDLINK_RE = re.compile(r"\]\(([^)\s]+\.md)\)")
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.S)
FENCE_RE = re.compile(r"^(```|~~~).*?^\1", re.S | re.M)
TASK_LINE_RE = re.compile(r"^(\s*(?:>\s*)*(?:[-*+]|\d+[.)])\s+\[)([ xX])(\]\s*)(.*)$")

PRESENCE_TIMEOUT = 45  # Sekunden
VERSION_MERGE_MINUTES = 10  # Speichern derselben Person innerhalb dieser Zeit = eine Version
MAX_VERSIONS = 100
SYNC_THROTTLE = 60  # Sekunden
TRASH_DAYS = 30  # so lange bleiben gelöschte Seiten wiederherstellbar
_last_sync = {}  # dbname -> timestamp


def _utc_from_ts(ts):
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def parse_markdown(text):
    """Tags, Link-Ziele und Erwähnungen aus einer Seite lesen (Code-Blöcke ignorieren)."""
    body = FENCE_RE.sub("", text or "")
    tags = set()
    fm = FRONTMATTER_RE.match(body)
    if fm:
        m = re.search(r"^tags:\s*(.*)$", fm.group(1), re.M)
        if m:
            inline = m.group(1).strip().strip("[]")
            tags |= {t.strip().strip("'\"#") for t in inline.split(",") if t.strip()}
            after = fm.group(1)[m.end():]
            tags |= {t.strip().strip("'\"#") for t in re.findall(r"^\s*-\s+(.+)$", after, re.M)}
        body = body[fm.end():]
    for line in body.splitlines():
        if re.match(r"^\s{0,3}#{1,6}\s", line):
            line = re.sub(r"^\s{0,3}#{1,6}\s", "", line)
        tags |= set(TAG_RE.findall(re.sub(r"`[^`]*`", "", line)))
    links = [m.group(2).strip() for m in WIKILINK_RE.finditer(body) if not m.group(1)]
    links += [m.group(1).replace("%20", " ") for m in MDLINK_RE.finditer(body)]
    mentions = {(m.group(2), int(m.group(3))) for m in MENTION_RE.finditer(body)}
    words = len(re.findall(r"\w+", body))
    return {"tags": sorted(t for t in tags if t), "links": links, "mentions": mentions, "words": words}


class LinkResolver:
    """Auflösung von Link-Zielen auf Seitenpfade (Python-Gegenstück zu markdown.js)."""

    def __init__(self, paths):
        self.lower = {p.lower(): p for p in paths}
        self.by_name = {}
        for p in paths:
            self.by_name.setdefault(p.lower().rsplit("/", 1)[-1], []).append(p)

    def resolve(self, target, from_path=""):
        t = (target or "").strip().lstrip("/")
        if not t:
            return None
        t = t if t.lower().endswith(MD_EXT) else t + MD_EXT
        lt = t.lower()
        from_dir = from_path.rsplit("/", 1)[0] if "/" in from_path else ""
        rel = os.path.normpath(os.path.join(from_dir, t)).replace(os.sep, "/").lower()
        if rel in self.lower:
            return self.lower[rel]
        if lt in self.lower:
            return self.lower[lt]
        cands = [p for p in self.by_name.get(lt.rsplit("/", 1)[-1], []) if "/" not in lt or p.lower().endswith("/" + lt)]
        if not cands:
            return None
        same = [c for c in cands if (c.rsplit("/", 1)[0] if "/" in c else "") == from_dir]
        return (same or sorted(cands, key=len))[0]


class WissenVaultMeta(models.AbstractModel):
    _inherit = "wissen.vault"

    # ------------------------------------------------------------------
    # Index
    # ------------------------------------------------------------------
    def _Page(self):
        return self.env["wissen.page"].sudo()

    def _index_file(self, full, rel, content=None, page=None):
        """Bringt den Index-Eintrag einer Datei auf den Stand der Platte.
        Wurde die Datei außerhalb von Odoo geändert, wird das vermerkt (inkl. Version)."""
        Page = self._Page()
        page = page or Page.search([("path", "=", rel)], limit=1)
        try:
            mtime = os.path.getmtime(full)
        except OSError as exc:
            _logger.warning("Wissensmanagement: %s nicht lesbar: %s", full, exc)
            return page
        if page and abs((page.mtime or 0) - mtime) < 0.001:
            return page
        if content is None:
            with open(full, encoding="utf-8", errors="replace", newline="") as fh:
                content = fh.read()
        vals = self._index_vals(rel, content, mtime)
        if not page:
            vals.update({"path": rel, "last_edit_date": _utc_from_ts(mtime)})
            page = Page.create(vals)
            self._set_mentions(page, content)
            return page
        last = self._versions(page, 1)
        external = page.mtime and (not last or last.content != content)
        if external:
            vals.update({"external_change": True, "last_editor_id": False, "last_edit_date": _utc_from_ts(mtime)})
            self._add_version(page, content, "external", user=False)
        page.write(vals)
        self._set_mentions(page, content)
        return page

    def _index_vals(self, rel, content, mtime):
        info = parse_markdown(content)
        return {
            "name": os.path.basename(rel)[: -len(MD_EXT)],
            "mtime": mtime,
            "tags": " ".join(info["tags"]),
            "link_targets": "\n".join(info["links"]),
            "word_count": info["words"],
        }

    def _set_mentions(self, page, content):
        wanted = parse_markdown(content)["mentions"]
        current = {(m.res_model, m.res_id): m for m in page.mention_ids}
        if set(current) == wanted:
            return
        (page.mention_ids.filtered(lambda m: (m.res_model, m.res_id) not in wanted)).unlink()
        self.env["wissen.mention"].sudo().create([
            {"page_id": page.id, "res_model": model, "res_id": rid} for model, rid in wanted - set(current)
        ])

    def _after_odoo_write(self, full, rel, content, previous):
        """Nach jedem Speichern durch Odoo: Index, "zuletzt bearbeitet von" und Version."""
        Page = self._Page()
        page = Page.search([("path", "=", rel)], limit=1)
        vals = self._index_vals(rel, content, os.path.getmtime(full))
        vals.update({"last_editor_id": self.env.uid, "last_edit_date": fields.Datetime.now(), "external_change": False})
        if not page:
            vals["path"] = rel
            page = Page.create(vals)
        else:
            page.write(vals)
        if previous is not None and previous != content and not self._versions(page, 1):
            self._add_version(page, previous, "initial", user=False)
        self._add_version(page, content, "odoo", user=self.env.user)
        self._set_mentions(page, content)
        return page

    def _versions(self, page, limit=None):
        return self.env["wissen.version"].sudo().search([("page_id", "=", page.id)], limit=limit)

    def _add_version(self, page, content, kind, user):
        Version = self.env["wissen.version"].sudo()
        last = self._versions(page, 1)
        if last and last.content == content:
            return last
        now = fields.Datetime.now()
        if (kind == "odoo" and last and last.kind == "odoo" and user and last.user_id == user
                and last.create_date and now - last.create_date < timedelta(minutes=VERSION_MERGE_MINUTES)):
            last.write({"content": content, "size": len(content)})
            return last
        version = Version.create({
            "page_id": page.id, "content": content, "kind": kind,
            "user_id": user.id if user else False, "size": len(content or ""),
        })
        old = self._versions(page)[MAX_VERSIONS:]
        if old:
            old.unlink()
        return version

    def _meta_move(self, old_rel, new_rel):
        """Pfade im Index, in Ordnerrechten und Anwesenheit nach Umbenennen/Verschieben nachziehen."""
        if not old_rel or old_rel == new_rel:
            return
        Page = self._Page()
        old_dir = old_rel[: -len(MD_EXT)] if old_rel.endswith(MD_EXT) else old_rel
        new_dir = new_rel[: -len(MD_EXT)] if new_rel.endswith(MD_EXT) else new_rel
        Page.search([("path", "=", new_rel)]).filtered(lambda p: p.path != old_rel).unlink()
        for page in Page.search(["|", ("path", "=", old_rel), ("path", "=like", old_dir.replace("%", r"\%").replace("_", r"\_") + "/%")]):
            new_path = new_rel if page.path == old_rel else new_dir + page.path[len(old_dir):]
            page.write({"path": new_path, "name": os.path.basename(new_path).removesuffix(MD_EXT)})
        like = old_dir.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "/%"
        rules = self.env["wissen.access.rule"].sudo().with_context(active_test=False).search(
            ["|", ("folder", "=", old_dir), ("folder", "=like", like)],
        )
        for rule in rules:
            rule.folder = new_dir + rule.folder[len(old_dir):]
        self.env["wissen.presence"].sudo().search([("path", "=", old_rel)]).write({"path": new_rel})

    def _sync_index(self, throttle=False):
        """Gleicht den Index mit dem Vault ab (neue, geänderte, gelöschte Dateien)."""
        key = self.env.cr.dbname
        if throttle and time.time() - _last_sync.get(key, 0) < SYNC_THROTTLE:
            return
        _last_sync[key] = time.time()
        root = self._root()
        Page = self._Page()
        pages = {p.path: p for p in Page.search([("is_folder", "=", False)])}
        seen = set()
        for f in self._walk_files(root):
            if not f.endswith(MD_EXT):
                continue
            rel = self._rel(f, root)
            seen.add(rel)
            self._index_file(f, rel, page=pages.get(rel))
        gone = Page.browse([p.id for path, p in pages.items()
                            if path not in seen and not path.startswith(TRASH_DIR + "/")
                            and not os.path.exists(os.path.join(root, path))])
        if gone:
            _logger.info("Wissensmanagement: %d gelöschte Seiten aus dem Index entfernt", len(gone))
        gone.unlink()

    @api.model
    def _cron_sync_index(self):
        if not self._param("vault_path"):
            return
        try:
            self.sudo()._sync_index()
            self.sudo()._purge_old_trash()
        except UserError as exc:
            # z. B. Vault-Ordner nicht erreichbar (Netzlaufwerk weg) - Cron nicht dauerhaft rot werden lassen
            _logger.warning("Wissensmanagement: Cron-Abgleich übersprungen: %s", exc)

    # ------------------------------------------------------------------
    # Papierkorb
    # ------------------------------------------------------------------
    def _trash_entry(self, entry_id):
        entry = self.env["wissen.trash"].sudo().browse(int(entry_id)).exists()
        if not entry:
            raise UserError(self.env._("Der Eintrag ist nicht mehr im Papierkorb."))
        self._check_path(entry.origin_path, write=True)
        return entry

    @api.model
    def list_trash(self):
        """Gelöschte Seiten, die der Benutzer am ursprünglichen Ort bearbeiten dürfte."""
        self._check_write()
        root = self._root()
        rules = self._rules()
        res = []
        for e in self.env["wissen.trash"].sudo().search([]):
            if not os.path.exists(os.path.join(root, e.trash_path)) or not self._can(e.origin_path, write=True, rules=rules):
                continue
            folder = os.path.dirname(e.origin_path)
            res.append({
                "id": e.id,
                "name": e.name,
                "origin": folder,
                "is_page": e.is_page,
                "user": e.user_id.name or "",
                "user_image": f"/web/image/res.users/{e.user_id.id}/avatar_128" if e.user_id else False,
                "date": fields.Datetime.to_string(e.create_date),
            })
        return res

    @api.model
    def restore_trash(self, entry_id):
        """Stellt eine Seite (inkl. Unterseiten, Versionen, Kommentaren) am alten Ort wieder her.
        Ist der Name dort inzwischen vergeben, bekommt sie eine laufende Nummer."""
        self._check_write()
        entry = self._trash_entry(entry_id)
        root = self._root()
        src = self._abs(entry.trash_path, root)
        if not os.path.exists(src):
            entry.unlink()
            raise UserError(self.env._("Die Datei liegt nicht mehr im Papierkorb."))
        dest = self._unique(self._abs(entry.origin_path, root))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if entry.is_page:
            self._move_entry(src, dest, root)
        else:
            shutil.move(src, dest)
        new_rel = self._rel(dest, root)
        self._meta_move(entry.trash_path, new_rel)
        entry.unlink()
        return new_rel

    @api.model
    def purge_trash(self, entry_id):
        """Löscht einen Papierkorb-Eintrag endgültig (Dateien und Metadaten)."""
        self._check_write()
        self._purge_entries(self._trash_entry(entry_id))
        return True

    @api.model
    def empty_trash(self):
        self._check_write()
        if not self.env.user.has_group("wissensmanagement.group_manager"):
            raise AccessError(self.env._("Nur Administratoren können den ganzen Papierkorb leeren."))
        self._purge_entries(self.env["wissen.trash"].sudo().search([]))
        return True

    def _purge_entries(self, entries):
        root = self._root()
        Page = self._Page()
        for entry in entries:
            full = self._abs(entry.trash_path, root)
            targets = [full]
            if entry.is_page:
                targets.append(full[: -len(MD_EXT)])
            for t in targets:
                if os.path.isdir(t):
                    shutil.rmtree(t)
                elif os.path.isfile(t):
                    os.remove(t)
            stem = entry.trash_path[: -len(MD_EXT)] if entry.is_page else entry.trash_path
            like = stem.replace("%", r"\%").replace("_", r"\_") + "/%"
            Page.search(["|", ("path", "=", entry.trash_path), ("path", "=like", like)]).unlink()
        entries.unlink()

    def _purge_old_trash(self):
        """Einträge, die länger als TRASH_DAYS im Papierkorb liegen, endgültig löschen."""
        limit = fields.Datetime.now() - timedelta(days=TRASH_DAYS)
        old = self.env["wissen.trash"].sudo().search([("create_date", "<", limit)])
        if old:
            _logger.info("Wissensmanagement: %d alte Einträge aus dem Papierkorb gelöscht", len(old))
            self._purge_entries(old)

    def _page_meta(self, page, full=None):
        user = self.env.user
        editor = page.last_editor_id
        return {
            "last_editor": {"id": editor.id, "name": editor.name,
                            "image": f"/web/image/res.users/{editor.id}/avatar_128"} if editor else False,
            "last_edit_date": fields.Datetime.to_string(page.last_edit_date) if page.last_edit_date else False,
            "external": page.external_change,
            "favorite": user in page.favorite_user_ids,
            "comment_count": len(page.comment_ids.filtered(lambda c: not c.resolved and not c.parent_id)),
            "version_count": self.env["wissen.version"].sudo().search_count([("page_id", "=", page.id)]),
            "share_mode": page.share_mode,
            "tags": (page.tags or "").split(),
            "words": page.word_count,
        }

    def _page_for(self, path, write=False):
        root = self._root()
        full = self._abs(path, root)
        rel = self._rel(full, root)
        self._check_path(rel, write=write)
        if not os.path.isfile(full) or not full.endswith(MD_EXT):
            raise UserError(self.env._("%s ist keine Seite.", path))
        return self._index_file(full, rel), full, rel

    # ------------------------------------------------------------------
    # Seitenleiste: Favoriten, zuletzt bearbeitet, Reihenfolge
    # ------------------------------------------------------------------
    @api.model
    def sidebar_data(self):
        self._check_read()
        Page = self._Page()
        rules = self._rules()
        root = self._root()

        def entry(p):
            return {
                "path": p.path, "name": p.name or p.path,
                "editor": p.last_editor_id.name or False,
                "editor_image": f"/web/image/res.users/{p.last_editor_id.id}/avatar_128" if p.last_editor_id else False,
                "date": fields.Datetime.to_string(p.last_edit_date) if p.last_edit_date else False,
                "external": p.external_change,
            }

        def visible(p):
            return (not p.path.startswith(".") and self._can(p.path, rules=rules)
                    and os.path.isfile(os.path.join(root, p.path)))

        favorites = Page.search([("favorite_user_ids", "in", self.env.uid)])
        recent = Page.search([("is_folder", "=", False), ("last_edit_date", "!=", False)], order="last_edit_date desc", limit=30)
        return {
            "favorites": sorted([entry(p) for p in favorites if visible(p)], key=lambda e: _natural_key(e["name"])),
            "recent": [entry(p) for p in recent if visible(p)][:8],
        }

    @api.model
    def toggle_favorite(self, path):
        self._check_read()
        page, _full, _rel = self._page_for(path)
        if self.env.user in page.favorite_user_ids:
            page.favorite_user_ids = [(3, self.env.uid)]
            return False
        page.favorite_user_ids = [(4, self.env.uid)]
        return True

    @api.model
    def reorder(self, path, target_path, position):
        """Drag & Drop im Baum. position: 'before' | 'after' | 'inside'."""
        self._check_write()
        if position == "inside":
            return self.move(path, target_path)
        root = self._root()
        target_full = self._abs(target_path, root)
        # Ordner, in dem das Ziel liegt (bei Folder-Notes: der Ordner über dem Ordner)
        parent_dir = os.path.dirname(target_full)
        if target_full.endswith(MD_EXT) and os.path.basename(parent_dir) == os.path.basename(target_full)[: -len(MD_EXT)]:
            parent_dir = os.path.dirname(parent_dir)
        parent_rel = self._rel(parent_dir, root)
        res = {"path": path, "updated_files": 0}
        src_full = self._abs(path, root)
        if os.path.dirname(src_full) != parent_dir:
            res = self.move(path, parent_rel)
            path = res["path"]
        siblings = [n["path"] for n in self._scan_dir(parent_dir, root)]
        if path in siblings:
            siblings.remove(path)
        idx = siblings.index(target_path) if target_path in siblings else len(siblings)
        siblings.insert(idx + (1 if position == "after" else 0), path)
        Page = self._Page()
        existing = {p.path: p for p in Page.search([("path", "in", siblings)])}
        for seq, p in enumerate(siblings, start=1):
            rec = existing.get(p)
            if rec:
                rec.sequence = seq * 10
            else:
                Page.create({"path": p, "name": os.path.basename(p).removesuffix(MD_EXT),
                             "is_folder": not p.endswith(MD_EXT), "sequence": seq * 10})
        return res

    # ------------------------------------------------------------------
    # Versionsverlauf
    # ------------------------------------------------------------------
    @api.model
    def list_versions(self, path):
        self._check_read()
        page, _full, _rel = self._page_for(path)
        return [{
            "id": v.id, "date": fields.Datetime.to_string(v.write_date or v.create_date), "kind": v.kind,
            "kind_label": dict(v._fields["kind"]._description_selection(self.env))[v.kind],
            "user": v.user_id.name or False,
            "user_image": f"/web/image/res.users/{v.user_id.id}/avatar_128" if v.user_id else False,
            "size": v.size,
        } for v in self._versions(page)]

    @api.model
    def get_version(self, version_id):
        self._check_read()
        version = self.env["wissen.version"].sudo().browse(int(version_id)).exists()
        if not version:
            raise UserError(self.env._("Version nicht gefunden."))
        self._check_path(version.page_id.path)
        return {"id": version.id, "content": version.content or ""}

    @api.model
    def restore_version(self, version_id):
        self._check_write()
        version = self.env["wissen.version"].sudo().browse(int(version_id)).exists()
        if not version:
            raise UserError(self.env._("Version nicht gefunden."))
        page = version.page_id
        root = self._root()
        full = self._abs(page.path, root)
        self._check_path(page.path, write=True)
        previous = None
        if os.path.exists(full):
            with open(full, encoding="utf-8", errors="replace", newline="") as fh:
                previous = fh.read()
        content = version.content or ""
        self._atomic_write(full, content)
        page = self._after_odoo_write(full, page.path, content, previous)
        self._versions(page, 1).kind = "restore"
        return {"path": page.path, "mtime": os.path.getmtime(full)}

    # ------------------------------------------------------------------
    # Zusammenarbeit: Anwesenheit & Kommentare
    # ------------------------------------------------------------------
    @api.model
    def heartbeat(self, path, editing=False):
        """Meldet "ich bin auf dieser Seite" und liefert, wer sonst da ist und ob sich die Datei geändert hat."""
        self._check_read()
        root = self._root()
        full = self._abs(path, root)
        rel = self._rel(full, root)
        self._check_path(rel)
        Presence = self.env["wissen.presence"].sudo()
        now = fields.Datetime.now()
        mine = Presence.search([("user_id", "=", self.env.uid)])
        if mine:
            mine[0].write({"path": rel, "last_seen": now, "editing": bool(editing)})
            mine[1:].unlink()
        else:
            Presence.create({"path": rel, "user_id": self.env.uid, "last_seen": now, "editing": bool(editing)})
        since = now - timedelta(seconds=PRESENCE_TIMEOUT)
        others = Presence.search([("path", "=", rel), ("user_id", "!=", self.env.uid), ("last_seen", ">=", since)])
        result = {
            "others": [{"id": p.user_id.id, "name": p.user_id.name, "editing": p.editing,
                        "image": f"/web/image/res.users/{p.user_id.id}/avatar_128"} for p in others],
            "mtime": os.path.getmtime(full) if os.path.isfile(full) else False,
        }
        if os.path.isfile(full) and full.endswith(MD_EXT):
            page = self._index_file(full, rel)
            result["meta"] = self._page_meta(page, full)
        return result

    @api.model
    def leave_page(self):
        self.env["wissen.presence"].sudo().search([("user_id", "=", self.env.uid)]).unlink()
        return True

    def _comment_dict(self, c):
        return {
            "id": c.id, "body": c.body, "quote": c.quote or False, "resolved": c.resolved,
            "parent_id": c.parent_id.id or False, "author": c.author_id.name, "author_id": c.author_id.id,
            "author_image": f"/web/image/res.users/{c.author_id.id}/avatar_128",
            "date": fields.Datetime.to_string(c.create_date),
            "can_edit": c.author_id == self.env.user or self.env.user.has_group("wissensmanagement.group_manager"),
        }

    @api.model
    def list_comments(self, path):
        self._check_read()
        page, _full, _rel = self._page_for(path)
        return [self._comment_dict(c) for c in page.comment_ids]

    @api.model
    def add_comment(self, path, body, quote=False, parent_id=False):
        self._check_read()
        if not (body or "").strip():
            raise UserError(self.env._("Der Kommentar ist leer."))
        page, _full, _rel = self._page_for(path)
        comment = self.env["wissen.comment"].sudo().create({
            "page_id": page.id, "body": body.strip(), "quote": (quote or "")[:500] or False,
            "parent_id": parent_id or False, "author_id": self.env.uid,
        })
        return self._comment_dict(comment)

    def _own_comment(self, comment_id):
        comment = self.env["wissen.comment"].sudo().browse(int(comment_id)).exists()
        if not comment:
            raise UserError(self.env._("Kommentar nicht gefunden."))
        self._check_path(comment.page_id.path)
        return comment

    @api.model
    def resolve_comment(self, comment_id, resolved=True):
        self._check_read()
        self._own_comment(comment_id).resolved = bool(resolved)
        return True

    @api.model
    def delete_comment(self, comment_id):
        comment = self._own_comment(comment_id)
        if comment.author_id != self.env.user and not self.env.user.has_group("wissensmanagement.group_manager"):
            raise AccessError(self.env._("Nur eigene Kommentare können gelöscht werden."))
        comment.unlink()
        return True

    # ------------------------------------------------------------------
    # Freigaben (öffentlicher Link / Kundenportal)
    # ------------------------------------------------------------------
    def _share_info(self, page):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        return {
            "mode": page.share_mode,
            "url": f"{base}/wissen/s/{page.share_token}" if page.share_token and page.share_mode == "link" else False,
            "portal_url": f"{base}/my/wissen/{page.share_token}" if page.share_token and page.share_mode == "portal" else False,
            "partners": [{"id": p.id, "name": p.display_name} for p in page.share_partner_ids],
        }

    @api.model
    def get_share(self, path):
        self._check_read()
        page, _full, _rel = self._page_for(path)
        return self._share_info(page)

    @api.model
    def set_share(self, path, mode, partner_ids=None):
        self._check_write()
        page, _full, _rel = self._page_for(path, write=True)
        vals = {"share_mode": mode, "share_partner_ids": [(6, 0, partner_ids or [])]}
        if mode != "off" and not page.share_token:
            vals["share_token"] = secrets.token_urlsafe(18)
        page.write(vals)
        return self._share_info(page)

    @api.model
    def regenerate_share_token(self, path):
        self._check_write()
        page, _full, _rel = self._page_for(path, write=True)
        page.share_token = secrets.token_urlsafe(18)
        return self._share_info(page)

    # ------------------------------------------------------------------
    # Übersicht: Tags, Graph, verwaiste Seiten, kaputte Links
    # ------------------------------------------------------------------
    @api.model
    def vault_stats(self):
        self._check_read()
        self._sync_index(throttle=True)
        root = self._root()
        rules = self._rules()
        pages = [p for p in self._Page().search([("is_folder", "=", False)])
                 if not p.path.startswith(".") and self._can(p.path, rules=rules)]
        all_files = [self._rel(f, root) for f in self._walk_files(root)]
        resolver = LinkResolver([f for f in all_files if f.endswith(MD_EXT)])
        file_resolver = {f.lower().rsplit("/", 1)[-1] for f in all_files}
        template_folder = self._param("template_folder", "Vorlagen").strip("/")
        tags, edges, broken, inbound = {}, set(), [], {p.path: 0 for p in pages}
        for p in pages:
            for tag in (p.tags or "").split():
                tags.setdefault(tag, []).append({"path": p.path, "name": p.name})
            for target in (p.link_targets or "").splitlines():
                resolved = resolver.resolve(target, p.path)
                if resolved:
                    if resolved != p.path:
                        edges.add((p.path, resolved))
                        if resolved in inbound:
                            inbound[resolved] += 1
                elif target.lower().rsplit("/", 1)[-1] not in file_resolver:
                    broken.append({"path": p.path, "name": p.name, "target": target})
        orphans = [{"path": p.path, "name": p.name} for p in pages
                   if not inbound.get(p.path) and not p.path.startswith(template_folder + "/")
                   and not any(e[0] == p.path for e in edges)]
        degree = {}
        for a, b in edges:
            degree[a] = degree.get(a, 0) + 1
            degree[b] = degree.get(b, 0) + 1
        return {
            "tags": sorted(({"tag": t, "pages": sorted(v, key=lambda x: _natural_key(x["name"]))} for t, v in tags.items()),
                           key=lambda x: (-len(x["pages"]), x["tag"].casefold())),
            "orphans": sorted(orphans, key=lambda x: _natural_key(x["name"])),
            "broken": sorted(broken, key=lambda x: (_natural_key(x["name"]), x["target"])),
            "graph": {
                "nodes": [{"id": p.path, "name": p.name, "degree": degree.get(p.path, 0),
                           "folder": p.path.split("/", 1)[0] if "/" in p.path else ""} for p in pages],
                "edges": [list(e) for e in sorted(edges)],
            },
            "page_count": len(pages),
        }

    # ------------------------------------------------------------------
    # Rückrichtung: Seiten, die einen Odoo-Datensatz erwähnen
    # ------------------------------------------------------------------
    @api.model
    def record_pages(self, model, res_id):
        if not self.env.user.has_group("wissensmanagement.group_reader") or not self._param("vault_path"):
            return []
        try:
            self._sync_index(throttle=True)
        except UserError as exc:
            _logger.warning("Wissensmanagement: Index-Abgleich nicht möglich: %s", exc)
            return []
        rules = self._rules()
        mentions = self.env["wissen.mention"].sudo().search([("res_model", "=", model), ("res_id", "=", int(res_id))])
        pages = mentions.page_id.filtered(lambda p: not p.path.startswith(".") and self._can(p.path, rules=rules))
        return [{
            "path": p.path, "name": p.name,
            "editor": p.last_editor_id.name or False,
            "date": fields.Datetime.to_string(p.last_edit_date) if p.last_edit_date else False,
        } for p in pages.sorted(lambda p: p.last_edit_date or datetime.min, reverse=True)]

    # ------------------------------------------------------------------
    # Odoo-Aktionen aus dem Text
    # ------------------------------------------------------------------
    @api.model
    def create_task_from_line(self, path, task_index, vals):
        """Macht aus der n-ten Checkbox einer Seite eine Odoo-Aufgabe und verlinkt sie im Text."""
        self._check_write()
        if "project.task" not in self.env:
            raise UserError(self.env._("Die App Projekt ist nicht installiert."))
        page, full, rel = self._page_for(path, write=True)
        with open(full, encoding="utf-8", errors="replace", newline="") as fh:
            content = fh.read()
        lines = content.split("\n")
        n, in_fence, line_no = -1, False, None
        for i, line in enumerate(lines):
            if re.match(r"^\s*(```|~~~)", line):
                in_fence = not in_fence
                continue
            if not in_fence and TASK_LINE_RE.match(line):
                n += 1
                if n == int(task_index):
                    line_no = i
                    break
        if line_no is None:
            raise UserError(self.env._("Checkbox nicht gefunden – wurde die Seite inzwischen geändert?"))
        m = TASK_LINE_RE.match(lines[line_no])
        text = re.sub(r"\[@[^\]]+\]\(odoo://[^)]+\)", "", m.group(4)).strip()
        name = (vals.get("name") or text or self.env._("Aufgabe aus Wissen")).strip()
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        link = f"{base}/odoo/action-wissensmanagement.action_wm_app?wm_path={rel}"
        task_vals = {
            "name": name,
            "project_id": vals.get("project_id") or False,
            "description": Markup("<p>%s <a href='%s'>%s</a></p>") % (self.env._("Aus der Wissensseite"), link, page.name),
        }
        if vals.get("user_ids"):
            task_vals["user_ids"] = [(6, 0, vals["user_ids"])]
        if vals.get("date_deadline"):
            task_vals["date_deadline"] = vals["date_deadline"]
        task = self.env["project.task"].create(task_vals)
        mention = f"[@{task.name.replace('[', '(').replace(']', ')')}](odoo://project.task/{task.id})"
        lines[line_no] = lines[line_no].rstrip() + " " + mention
        new_content = "\n".join(lines)
        self._atomic_write(full, new_content)
        self._after_odoo_write(full, rel, new_content, content)
        return {"task_id": task.id, "mtime": os.path.getmtime(full), "content": new_content}

    @api.model
    def share_to_chatter(self, path, model, res_id, note=""):
        """Postet einen Link auf die Seite (mit Auszug) in den Chatter eines Datensatzes."""
        self._check_read()
        page, full, rel = self._page_for(path)
        record = self.env[model].browse(int(res_id)).exists()
        if not record:
            raise UserError(self.env._("Datensatz nicht gefunden."))
        record.check_access("read")
        if not hasattr(record, "message_post"):
            raise UserError(self.env._("%s hat keinen Chatter.", record.display_name))
        with open(full, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        text = FRONTMATTER_RE.sub("", text).strip()
        excerpt = re.sub(r"\s+", " ", re.sub(r"[#*_>`\[\]]|\(odoo://[^)]+\)", "", text))[:280]
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        link = f"{base}/odoo/action-wissensmanagement.action_wm_app?wm_path={rel}"
        body = Markup(
            "<p>📖 <b>%s</b> <a href='%s'>%s</a></p>%s<blockquote>%s…</blockquote>",
        ) % (self.env._("Wissensseite:"), link, page.name, Markup("<p>%s</p>") % note if note else "", excerpt)
        record.message_post(body=body, message_type="comment", subtype_xmlid="mail.mt_note")
        return True

    # ------------------------------------------------------------------
    # Für den Freigabe-Controller
    # ------------------------------------------------------------------
    def _shared_page(self, token, partner=None):
        page = self._Page().search([("share_token", "=", token), ("share_mode", "!=", "off")], limit=1) if token else False
        if not page:
            return None
        if page.share_mode == "portal":
            if not partner:
                return None
            allowed = page.share_partner_ids | page.share_partner_ids.child_ids
            if partner not in allowed and partner.commercial_partner_id not in page.share_partner_ids:
                return None
        return page

    def _shared_payload(self, page):
        root = self._root()
        full = self._abs(page.path, root)
        with open(full, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        shared = self._Page().search([("share_mode", "=", page.share_mode), ("share_token", "!=", False)])
        return {
            "title": page.name,
            "content": content,
            "links": {p.path: p.share_token for p in shared},
            # nur Dateien, die auf der Seite tatsächlich eingebunden sind
            "files": [r for r in (self._rel(f, root) for f in self._walk_files(root) if not f.endswith(MD_EXT))
                      if r.rsplit("/", 1)[-1] in content],
            "pages": [p.path for p in shared],
            "updated": fields.Datetime.to_string(page.last_edit_date) if page.last_edit_date else "",
            "editor": page.last_editor_id.name or "",
        }

    def _json(self, data):
        # sicher in <script type="application/json"> einbettbar
        return json.dumps(data).replace("</", "<\\/").replace("<!--", "\\u003c!--")
