"""Dateisystem-Backend für den Markdown-Vault.

Die .md-Dateien auf der Platte sind die einzige Quelle der Wahrheit. Dieses
AbstractModel kapselt alle Zugriffe darauf und wird vom OWL-Client per ORM-Call
angesprochen. Jeder Pfad, der vom Client kommt, ist relativ zum Vault und wird
in ``_abs`` gegen Ausbruch aus dem Vault (``..``, Symlinks) geprüft.

Struktur-Konvention:
- ``Seite.md`` ist eine Seite.
- Unterseiten von ``Seite.md`` liegen im Geschwister-Ordner ``Seite/``.
- Alternativ wird auch ``Ordner/Ordner.md`` (Folder-Note) als Seite des Ordners erkannt.
- Versteckte Einträge (``.trash``, ``.git`` …) werden ignoriert.
- Gelöschte Seiten landen im versteckten Ordner ``.trash/`` und sind in Odoo im Papierkorb wiederherstellbar.
"""

import base64
import logging
import os
import re
import shutil
from datetime import datetime

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.misc import format_amount

_logger = logging.getLogger(__name__)

MD_EXT = ".md"
TRASH_DIR = ".trash"
INVALID_NAME_RE = re.compile(r'[\\/:*?"<>|#^\[\]]')
WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]|#^]+?)((?:[#^][^\[\]|]*)?)((?:\|[^\[\]]*)?)\]\]")
MENTION_RE = re.compile(r"\[@([^\]]+)\]\(odoo://([a-z0-9_.]+)/(\d+)\)")
TEMPLATE_VAR_RE = re.compile(r"\{\{\s*(title|date|time)(?::([^}]+))?\s*\}\}")
MAX_SEARCH_RESULTS = 50
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# Alles, was per @ erwähnt werden kann (nicht installierte Modelle werden ignoriert)
DEFAULT_MENTION_MODELS = (
    "res.partner,res.users,project.project,project.task,sale.order,crm.lead,"
    "purchase.order,account.move,product.template,hr.employee,helpdesk.ticket,"
    "calendar.event,stock.picking,mrp.production,repair.order,maintenance.equipment"
)
# "@aufgabe bank" -> nur Aufgaben durchsuchen
# Bereiche für das @-Menü (Reihenfolge = Anzeige). Erstes Kürzel ist das Hauptkürzel.
MENTION_CATEGORIES = [
    ("res.partner", ["kontakt", "kunde", "firma", "person"], "Kontakte"),
    ("res.users", ["benutzer", "user"], "Odoo-Benutzer (Kolleginnen und Kollegen)"),
    ("project.project", ["projekt"], "Projekte"),
    ("project.task", ["aufgabe", "task"], "Aufgaben"),
    ("sale.order", ["angebot", "auftrag", "verkauf"], "Angebote & Verkaufsaufträge"),
    ("crm.lead", ["lead", "chance", "opportunity"], "CRM-Leads & Verkaufschancen"),
    ("purchase.order", ["einkauf", "bestellung"], "Einkaufsbestellungen"),
    ("account.move", ["rechnung", "beleg"], "Rechnungen & Belege"),
    ("product.template", ["produkt", "artikel"], "Produkte"),
    ("hr.employee", ["mitarbeiter"], "Mitarbeiter"),
    ("helpdesk.ticket", ["ticket"], "Helpdesk-Tickets"),
    ("calendar.event", ["termin"], "Kalender-Termine"),
    ("stock.picking", ["lieferung"], "Lieferungen"),
    ("mrp.production", ["fertigung"], "Fertigungsaufträge"),
    ("repair.order", ["reparatur"], "Reparaturen"),
    ("maintenance.equipment", ["gerät"], "Geräte (Wartung)"),
]
# "@aufgabe bank" -> nur Aufgaben durchsuchen
MENTION_ALIASES = {alias: model for model, aliases, _label in MENTION_CATEGORIES for alias in aliases}
MODEL_ICONS = {
    "res.partner": "fa-user", "res.users": "fa-user-circle", "project.project": "fa-folder-open",
    "project.task": "fa-check-square-o", "sale.order": "fa-file-text-o", "crm.lead": "fa-handshake-o",
    "purchase.order": "fa-shopping-cart", "account.move": "fa-file-text", "product.template": "fa-cube",
    "product.product": "fa-cube", "hr.employee": "fa-id-badge", "helpdesk.ticket": "fa-life-ring",
    "calendar.event": "fa-calendar", "stock.picking": "fa-truck", "mrp.production": "fa-industry",
    "repair.order": "fa-wrench", "maintenance.equipment": "fa-cogs",
}
# Felder für die Vorschaukarte - generisch, gilt für jedes Modell, das sie besitzt
CARD_SUBTITLE_FIELDS = ["project_id", "parent_id", "partner_id", "company_name", "job_title", "function", "email"]
CARD_BADGE_FIELDS = ["stage_id", "state", "last_update_status", "kanban_state"]
EMPTY_BADGE_VALUES = {"to_define", "normal", "none"}
DEADLINE_FIELDS = {"date_deadline", "invoice_date_due", "validity_date", "date_deadline_order"}
CARD_FACT_FIELDS = [
    ("date_deadline", "Deadline"), ("invoice_date_due", "Fällig"), ("validity_date", "Gültig bis"),
    ("amount_total", "Betrag"), ("expected_revenue", "Erw. Umsatz"), ("list_price", "Preis"),
    ("email", "E-Mail"), ("phone", "Telefon"), ("mobile", "Mobil"), ("work_email", "E-Mail"),
    ("partner_id", "Kunde"), ("date_order", "Datum"), ("date_start", "Start"),
    ("date", "Datum"), ("start", "Beginn"), ("city", "Ort"), ("default_code", "Referenz"),
]

# Datumsformate in Vorlagen-Platzhaltern (DD.MM.YYYY usw.) -> strftime
MOMENT_TOKENS = [
    ("YYYY", "%Y"), ("YY", "%y"), ("MMMM", "%B"), ("MMM", "%b"), ("MM", "%m"),
    ("DD", "%d"), ("dddd", "%A"), ("ddd", "%a"), ("HH", "%H"), ("mm", "%M"), ("ss", "%S"),
]


def _natural_key(name):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name)]


def _moment_to_strftime(fmt):
    out, i = "", 0
    while i < len(fmt):
        for token, repl in MOMENT_TOKENS:
            if fmt.startswith(token, i):
                out += repl
                i += len(token)
                break
        else:
            out += fmt[i].replace("%", "%%")
            i += 1
    return out


class WissenVault(models.AbstractModel):
    _name = "wissen.vault"
    _description = "Wissensmanagement Vault"

    # ------------------------------------------------------------------
    # Rechte & Konfiguration
    # ------------------------------------------------------------------
    def _check_read(self):
        if not self.env.user.has_group("wissensmanagement.group_reader"):
            raise AccessError(self.env._("Du hast keinen Lesezugriff auf das Wissensmanagement."))

    def _check_write(self):
        if not self.env.user.has_group("wissensmanagement.group_editor"):
            raise AccessError(self.env._("Du hast keinen Schreibzugriff auf das Wissensmanagement."))

    # Ordnerrechte (wissen.access.rule): die spezifischste passende Regel entscheidet
    def _rules(self):
        return self.env["wissen.access.rule"]._get_rules_cached()

    def _can(self, rel, write=False, rules=None):
        if self.env.su or self.env.user.has_group("wissensmanagement.group_manager"):
            return True
        rel = (rel or "").strip("/")
        best = None
        for folder, read_groups, write_groups in (self._rules() if rules is None else rules):
            if rel == folder or rel.startswith(folder + "/") or rel == folder + MD_EXT:
                if best is None or len(folder) > len(best[0]):
                    best = (folder, read_groups, write_groups)
        if not best:
            return True
        user_groups = set(self.env.user.all_group_ids.ids)
        groups = (best[2] or best[1]) if write else best[1]
        return not groups or bool(groups & user_groups)

    def _check_path(self, rel, write=False):
        if not self._can(rel, write=write):
            raise AccessError(self.env._("Keine Berechtigung für %s.", rel))

    def _param(self, key, default=""):
        return self.env["ir.config_parameter"].sudo().get_param("wissensmanagement." + key, default) or default

    def _root(self):
        path = self._param("vault_path")
        if not path:
            raise UserError(self.env._("Es ist noch kein Wissensordner konfiguriert (Einstellungen › Wissensmanagement)."))
        root = os.path.realpath(os.path.expanduser(path))
        if not os.path.isdir(root):
            raise UserError(self.env._("Der Wissensordner %s existiert nicht oder ist für Odoo nicht lesbar.", root))
        return root

    def _abs(self, rel, root=None):
        root = root or self._root()
        rel = (rel or "").replace("\\", "/").strip("/")
        full = os.path.realpath(os.path.join(root, rel))
        if full != root and not full.startswith(root + os.sep):
            raise AccessError(self.env._("Ungültiger Pfad: %s", rel))
        return full

    def _rel(self, full, root=None):
        root = root or self._root()
        return os.path.relpath(full, root).replace(os.sep, "/") if full != root else ""

    @staticmethod
    def _is_hidden(name):
        return name.startswith(".")

    def _clean_name(self, name):
        name = (name or "").strip()
        if name.lower().endswith(MD_EXT):
            name = name[: -len(MD_EXT)]
        if not name or name.startswith(".") or INVALID_NAME_RE.search(name):
            raise UserError(self.env._('Ungültiger Name "%s". Nicht erlaubt: \\ / : * ? " < > | # ^ [ ] und ein führender Punkt.', name))
        return name

    @staticmethod
    def _unique(path):
        """Hängt bei Kollision " 1", " 2", … an (vor der Endung)."""
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path) if path.endswith(MD_EXT) else (path, "")
        i = 1
        while os.path.exists(f"{base} {i}{ext}"):
            i += 1
        return f"{base} {i}{ext}"

    def _children_dir(self, page_full):
        """Ordner, in dem die Unterseiten einer Seite liegen."""
        if page_full.endswith(MD_EXT):
            parent, fname = os.path.split(page_full)
            stem = fname[: -len(MD_EXT)]
            # Folder-Note-Variante: Ordner/Ordner.md -> Unterseiten liegen im selben Ordner
            if os.path.basename(parent) == stem:
                return parent
            return os.path.join(parent, stem)
        return page_full

    def _companion_dir(self, page_full):
        """Geschwister-Ordner einer Seite, der beim Verschieben/Löschen mitwandert (falls vorhanden)."""
        if not page_full.endswith(MD_EXT):
            return None
        d = page_full[: -len(MD_EXT)]
        return d if os.path.isdir(d) else None

    def _target_dir(self, parent_rel, root):
        """Zielordner für neue/verschobene Seiten. parent_rel kann Ordner, Seite oder '' sein."""
        parent_full = self._abs(parent_rel, root)
        if os.path.isfile(parent_full):
            if not parent_full.endswith(MD_EXT):
                raise UserError(self.env._("Unterseiten können nur unter Seiten oder Ordnern angelegt werden."))
            parent_full = self._children_dir(parent_full)
        return parent_full

    @api.model
    def get_config(self):
        self._check_read()
        user = self.env.user
        configured = bool(self._param("vault_path"))
        return {
            "configured": configured,
            "can_write": user.has_group("wissensmanagement.group_editor"),
            "can_configure": user.has_group("base.group_system"),
            "is_manager": user.has_group("wissensmanagement.group_manager"),
            "template_folder": self._param("template_folder", "Vorlagen").strip("/"),
            "attachment_folder": self._param("attachment_folder", "Anhänge").strip("/"),
            "default_mode": self._param("default_mode", "read"),
            "style": {
                "preset": self._param("style_preset", "default"),
                "accent_color": self._param("accent_color", "#714B67"),
                "wikilink_color": self._param("wikilink_color", "#7c3aed"),
                "mention_color": self._param("mention_color", "#0e7490"),
                "content_width": int(self._param("content_width", "860") or 860),
                "font_size": int(self._param("font_size", "16") or 16),
                "custom_css": self._param("custom_css", ""),
            },
            "mention_models": [m["model"] for m in self._mention_models()],
            "mention_categories": self._mention_categories(),
            "has_project": "project.task" in self.env and self.env["project.task"].has_access("create"),
            "has_chatter": "mail.thread" in self.env,
        }

    # ------------------------------------------------------------------
    # Baum & Index
    # ------------------------------------------------------------------
    def _scan_dir(self, full, root, order=None, rules=None):
        if order is None:
            order = self._order_map()
        if rules is None:
            rules = self._rules()
        try:
            entries = [e for e in os.scandir(full) if not self._is_hidden(e.name)]
        except OSError as exc:
            _logger.warning("Wissensmanagement: Ordner %s nicht lesbar: %s", full, exc)
            return []
        dirs = {e.name: e for e in entries if e.is_dir(follow_symlinks=False)}
        pages = {e.name[: -len(MD_EXT)]: e for e in entries if e.is_file() and e.name.lower().endswith(MD_EXT)}
        folder_note = os.path.basename(full)
        if full != root and folder_note in pages:
            # Ordner/Ordner.md ist die Seite des Ordners selbst, nicht ein Kind
            del pages[folder_note]
        nodes = []
        for name in set(dirs) | set(pages):
            d, p = dirs.get(name), pages.get(name)
            page_path = self._rel(p.path, root) if p else None
            if d and not p:
                note = os.path.join(d.path, name + MD_EXT)
                if os.path.isfile(note):
                    page_path = self._rel(note, root)
            node_path = page_path or self._rel(d.path, root)
            if not self._can(self._rel(d.path, root) if d else node_path, rules=rules):
                continue
            children = self._scan_dir(d.path, root, order, rules) if d else []
            nodes.append({
                "name": name,
                "path": page_path or self._rel(d.path, root),
                "page_path": page_path,
                "folder_path": self._rel(d.path, root) if d else None,
                "is_page": bool(page_path),
                "children": children,
                "sequence": order.get(node_path, 0),
            })
        # manuelle Reihenfolge zuerst, Rest alphabetisch (natürliche Sortierung)
        nodes.sort(key=lambda n: (0 if n["sequence"] else 1, n["sequence"], _natural_key(n["name"])))
        return nodes

    def _order_map(self):
        pages = self.env["wissen.page"].sudo().search_read([("sequence", "!=", 0)], ["path", "sequence"])
        return {p["path"]: p["sequence"] for p in pages}

    def _walk_files(self, root):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not self._is_hidden(d)]
            for f in filenames:
                if not self._is_hidden(f):
                    yield os.path.join(dirpath, f)

    @api.model
    def get_tree(self):
        """Seitenbaum + flacher Index aller Dateien (für Wikilink-Auflösung im Client)."""
        self._check_read()
        root = self._root()
        self._sync_index(throttle=True)
        rules = self._rules()
        files = [r for r in (self._rel(f, root) for f in self._walk_files(root)) if self._can(r, rules=rules)]
        return {
            "root_name": os.path.basename(root),
            "tree": self._scan_dir(root, root, rules=rules),
            "files": sorted(files, key=_natural_key),
        }

    # ------------------------------------------------------------------
    # Lesen & Schreiben
    # ------------------------------------------------------------------
    @api.model
    def read_page(self, path):
        self._check_read()
        root = self._root()
        full = self._abs(path, root)
        self._check_path(self._rel(full, root))
        if not os.path.exists(full):
            raise UserError(self.env._("Die Seite %s existiert nicht (mehr).", path))
        is_page = os.path.isfile(full)
        if is_page and not full.endswith(MD_EXT):
            raise UserError(self.env._("%s ist keine Markdown-Datei.", path))
        content = ""
        mtime = None
        if is_page:
            with open(full, encoding="utf-8", errors="replace", newline="") as fh:
                content = fh.read()
            mtime = os.path.getmtime(full)
        children_full = self._children_dir(full) if is_page else full
        children = self._scan_dir(children_full, root) if os.path.isdir(children_full) else []
        crumbs, cur = [], os.path.dirname(full)
        while cur != root and cur.startswith(root):
            crumbs.insert(0, {"name": os.path.basename(cur), "path": self._rel(cur, root)})
            cur = os.path.dirname(cur)
        name = os.path.basename(full)
        stat = os.stat(full)
        rel = self._rel(full, root)
        meta = self._page_meta(self._index_file(full, rel, content), full) if is_page else {}
        return {
            "meta": meta,
            "can_write": self.env.user.has_group("wissensmanagement.group_editor") and self._can(rel, write=True),
            "path": self._rel(full, root),
            "name": name[: -len(MD_EXT)] if is_page else name,
            "is_page": is_page,
            "content": content,
            "mtime": mtime,
            "modified": fields.Datetime.to_string(datetime.fromtimestamp(stat.st_mtime)),
            "size": stat.st_size,
            "breadcrumbs": crumbs,
            "children": [{"name": c["name"], "path": c["path"], "is_page": c["is_page"],
                          "has_children": bool(c["children"])} for c in children],
        }

    @api.model
    def write_page(self, path, content, expected_mtime=None, force=False):
        """Speichert eine Seite. Wurde die Datei seit dem Laden extern geändert
        (z. B. von einer anderen Person), wird ohne ``force`` ein Konflikt zurückgegeben."""
        self._check_write()
        root = self._root()
        full = self._abs(path, root)
        rel = self._rel(full, root)
        self._check_path(rel, write=True)
        if not full.endswith(MD_EXT):
            raise UserError(self.env._("Nur Markdown-Dateien können bearbeitet werden."))
        if os.path.exists(full) and expected_mtime and not force:
            disk_mtime = os.path.getmtime(full)
            if abs(disk_mtime - float(expected_mtime)) > 0.001:
                with open(full, encoding="utf-8", errors="replace", newline="") as fh:
                    disk = fh.read()
                if disk != content:
                    page = self._index_file(full, rel, disk)
                    return {"conflict": True, "content": disk, "mtime": disk_mtime,
                            "last_editor": page.last_editor_id.name or False}
        previous = None
        if os.path.exists(full):
            with open(full, encoding="utf-8", errors="replace", newline="") as fh:
                previous = fh.read()
        self._atomic_write(full, content)
        page = self._after_odoo_write(full, rel, content, previous)
        return {"conflict": False, "mtime": os.path.getmtime(full), "path": rel, "meta": self._page_meta(page, full)}

    @staticmethod
    def _atomic_write(full, content):
        os.makedirs(os.path.dirname(full), exist_ok=True)
        tmp = os.path.join(os.path.dirname(full), f".{os.path.basename(full)}.odoo-tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp, full)

    # ------------------------------------------------------------------
    # Vorlagen
    # ------------------------------------------------------------------
    @api.model
    def list_templates(self):
        self._check_read()
        root = self._root()
        folder = self._param("template_folder", "Vorlagen").strip("/")
        tdir = self._abs(folder, root)
        if not os.path.isdir(tdir):
            return []
        result = []
        for f in self._walk_files(tdir):
            if f.endswith(MD_EXT) and self._can(self._rel(f, root)):
                rel_in_tpl = os.path.relpath(f, tdir).replace(os.sep, "/")
                result.append({
                    "path": self._rel(f, root),
                    "name": rel_in_tpl[: -len(MD_EXT)],
                    "has_children": bool(self._companion_dir(f)),
                })
        return sorted(result, key=lambda t: _natural_key(t["name"]))

    def _render_template(self, text, title):
        now = fields.Datetime.context_timestamp(self, fields.Datetime.now())

        def repl(m):
            var, fmt = m.group(1), m.group(2)
            if var == "title":
                return title
            default = "%Y-%m-%d" if var == "date" else "%H:%M"
            return now.strftime(_moment_to_strftime(fmt.strip()) if fmt else default)

        return TEMPLATE_VAR_RE.sub(repl, text)

    def _copy_template_children(self, src_dir, dst_dir, title):
        for dirpath, dirnames, filenames in os.walk(src_dir):
            dirnames[:] = [d for d in dirnames if not self._is_hidden(d)]
            target = os.path.join(dst_dir, os.path.relpath(dirpath, src_dir))
            os.makedirs(target, exist_ok=True)
            for f in filenames:
                if self._is_hidden(f):
                    continue
                src, dst = os.path.join(dirpath, f), os.path.join(target, f)
                if f.endswith(MD_EXT):
                    with open(src, encoding="utf-8", errors="replace", newline="") as fh:
                        text = self._render_template(fh.read(), title)
                    self._atomic_write(dst, text)
                    self._after_odoo_write(dst, self._rel(dst), text, None)
                else:
                    shutil.copy2(src, dst)

    # ------------------------------------------------------------------
    # Seiten-Operationen
    # ------------------------------------------------------------------
    @api.model
    def create_page(self, parent_path, title, template_path=None):
        self._check_write()
        root = self._root()
        name = self._clean_name(title)
        target_dir = self._target_dir(parent_path, root)
        self._check_path(self._rel(target_dir, root), write=True)
        os.makedirs(target_dir, exist_ok=True)
        full = self._unique(os.path.join(target_dir, name + MD_EXT))
        name = os.path.basename(full)[: -len(MD_EXT)]
        content = f"# {name}\n\n"
        if template_path:
            tpl = self._abs(template_path, root)
            if not os.path.isfile(tpl):
                raise UserError(self.env._("Vorlage %s nicht gefunden.", template_path))
            with open(tpl, encoding="utf-8", errors="replace", newline="") as fh:
                content = self._render_template(fh.read(), name)
            tpl_children = self._companion_dir(tpl)
            if tpl_children:
                self._copy_template_children(tpl_children, full[: -len(MD_EXT)], name)
        self._atomic_write(full, content)
        self._after_odoo_write(full, self._rel(full, root), content, None)
        return self._rel(full, root)

    @api.model
    def create_folder(self, parent_path, name):
        self._check_write()
        root = self._root()
        target = self._unique(os.path.join(self._target_dir(parent_path, root), self._clean_name(name)))
        self._check_path(self._rel(target, root), write=True)
        os.makedirs(target)
        return self._rel(target, root)

    def _move_entry(self, full, dest_full, root):
        """Verschiebt Seite inkl. Unterseiten-Ordner. Gibt neuen absoluten Pfad zurück."""
        companion = self._companion_dir(full)
        os.makedirs(os.path.dirname(dest_full), exist_ok=True)
        shutil.move(full, dest_full)
        if companion:
            shutil.move(companion, dest_full[: -len(MD_EXT)])
        return dest_full

    @api.model
    def rename(self, path, new_name, update_links=True):
        self._check_write()
        root = self._root()
        full = self._abs(path, root)
        if full == root:
            raise UserError(self.env._("Der Wissensordner selbst kann nicht umbenannt werden."))
        self._check_path(self._rel(full, root), write=True)
        name = self._clean_name(new_name)
        is_page = os.path.isfile(full)
        dest = os.path.join(os.path.dirname(full), name + (MD_EXT if is_page else ""))
        if os.path.exists(dest) and os.path.normcase(dest) != os.path.normcase(full):
            raise UserError(self.env._('Es existiert bereits ein Eintrag "%s" an diesem Ort.', name))
        old_rel = self._rel(full, root)
        if is_page:
            # Folder-Note: Ordner/Ordner.md -> Ordner mit umbenennen
            parent = os.path.dirname(full)
            if os.path.basename(parent) == os.path.basename(full)[: -len(MD_EXT)] and parent != root:
                new_parent = os.path.join(os.path.dirname(parent), name)
                if os.path.exists(new_parent):
                    raise UserError(self.env._('Es existiert bereits ein Ordner "%s".', name))
                shutil.move(parent, new_parent)
                os.rename(os.path.join(new_parent, os.path.basename(full)), os.path.join(new_parent, name + MD_EXT))
                dest = os.path.join(new_parent, name + MD_EXT)
            else:
                self._move_entry(full, dest, root)
        else:
            shutil.move(full, dest)
            note = os.path.join(dest, os.path.basename(full) + MD_EXT)
            if os.path.isfile(note):
                os.rename(note, os.path.join(dest, name + MD_EXT))
        new_rel = self._rel(dest, root)
        if is_page and os.path.dirname(full) != os.path.dirname(dest):
            # Folder-Note: der ganze Ordner wurde umbenannt
            self._meta_move(self._rel(os.path.dirname(full), root), self._rel(os.path.dirname(dest), root))
        self._meta_move(old_rel, new_rel)
        updated = self._update_links(old_rel, new_rel, root) if update_links and is_page else 0
        return {"path": new_rel, "updated_files": updated}

    @api.model
    def move(self, path, new_parent_path):
        self._check_write()
        root = self._root()
        full = self._abs(path, root)
        if full == root:
            raise UserError(self.env._("Der Wissensordner selbst kann nicht verschoben werden."))
        self._check_path(self._rel(full, root), write=True)
        target_dir = self._target_dir(new_parent_path, root)
        self._check_path(self._rel(target_dir, root), write=True)
        own_dirs = [full] if os.path.isdir(full) else [d for d in [self._companion_dir(full)] if d]
        for d in own_dirs:
            if target_dir == d or target_dir.startswith(d + os.sep):
                raise UserError(self.env._("Eine Seite kann nicht unter sich selbst verschoben werden."))
        dest = os.path.join(target_dir, os.path.basename(full))
        if dest == full:
            return {"path": self._rel(full, root), "updated_files": 0}
        if os.path.exists(dest):
            raise UserError(self.env._('Am Ziel existiert bereits "%s".', os.path.basename(full)))
        old_rel = self._rel(full, root)
        if os.path.isfile(full):
            self._move_entry(full, dest, root)
        else:
            shutil.move(full, dest)
        new_rel = self._rel(dest, root)
        self._meta_move(old_rel, new_rel)
        updated = self._update_links(old_rel, new_rel, root) if dest.endswith(MD_EXT) else 0
        return {"path": new_rel, "updated_files": updated}

    @api.model
    def duplicate(self, path):
        self._check_write()
        root = self._root()
        full = self._abs(path, root)
        if not os.path.isfile(full) or not full.endswith(MD_EXT):
            raise UserError(self.env._("Nur Seiten können dupliziert werden."))
        self._check_path(self._rel(full, root), write=True)
        stem = os.path.basename(full)[: -len(MD_EXT)]
        dest = self._unique(os.path.join(os.path.dirname(full), f"{stem} (Kopie){MD_EXT}"))
        shutil.copy2(full, dest)
        companion = self._companion_dir(full)
        if companion:
            shutil.copytree(companion, dest[: -len(MD_EXT)])
        with open(dest, encoding="utf-8", errors="replace", newline="") as fh:
            self._after_odoo_write(dest, self._rel(dest, root), fh.read(), None)
        return self._rel(dest, root)

    @api.model
    def delete(self, path):
        """Verschiebt Seite (inkl. Unterseiten) in den Papierkorb. Wiederherstellen und endgültiges
        Löschen laufen über ``list_trash`` / ``restore_trash`` / ``purge_trash``."""
        self._check_write()
        root = self._root()
        full = self._abs(path, root)
        if full == root:
            raise UserError(self.env._("Der Wissensordner selbst kann nicht gelöscht werden."))
        if not os.path.exists(full):
            raise UserError(self.env._("%s existiert nicht.", path))
        self._check_path(self._rel(full, root), write=True)
        trash = os.path.join(root, TRASH_DIR)
        os.makedirs(trash, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = os.path.basename(full)
        if base.endswith(MD_EXT):
            dest = self._unique(os.path.join(trash, f"{base[:-len(MD_EXT)]} {stamp}{MD_EXT}"))
            self._move_entry(full, dest, root)
        else:
            dest = self._unique(os.path.join(trash, f"{base} {stamp}"))
            shutil.move(full, dest)
        # Metadaten (Versionen, Kommentare) wandern mit in den Papierkorb
        origin = self._rel(full, root)
        self._meta_move(origin, self._rel(dest, root))
        self.env["wissen.trash"].sudo().create({
            "name": base[: -len(MD_EXT)] if base.endswith(MD_EXT) else base,
            "trash_path": self._rel(dest, root),
            "origin_path": origin,
            "is_page": base.endswith(MD_EXT),
            "user_id": self.env.uid,
        })
        return True

    # ------------------------------------------------------------------
    # Links
    # ------------------------------------------------------------------
    def _update_links(self, old_rel, new_rel, root):
        """Passt [[Wikilinks]] und [Markdown](links.md) in allen Seiten an."""
        old_noext, new_noext = old_rel[: -len(MD_EXT)], new_rel[: -len(MD_EXT)]
        old_stem, new_stem = os.path.basename(old_noext), os.path.basename(new_noext)
        if old_noext == new_noext:
            return 0

        def wiki(m):
            target = m.group(2).strip()
            t = target[: -len(MD_EXT)] if target.lower().endswith(MD_EXT) else target
            if t == old_noext or (t.endswith("/" + old_noext)):
                new_t = new_noext
            elif "/" not in t and t == old_stem:
                new_t = new_stem
            elif "/" in t and old_noext.endswith("/" + t):
                new_t = new_noext
            else:
                return m.group(0)
            return f"{m.group(1)}[[{new_t}{m.group(3)}{m.group(4)}]]"

        md_variants = {old_rel: new_rel, old_rel.replace(" ", "%20"): new_rel.replace(" ", "%20")}
        count = 0
        for f in self._walk_files(root):
            if not f.endswith(MD_EXT):
                continue
            try:
                with open(f, encoding="utf-8", newline="") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError) as exc:
                _logger.warning("Wissensmanagement: %s nicht lesbar, Links nicht angepasst: %s", f, exc)
                continue
            new_text = WIKILINK_RE.sub(wiki, text)
            for o, n in md_variants.items():
                new_text = new_text.replace(f"]({o})", f"]({n})").replace(f"](/{o})", f"](/{n})")
            if new_text != text:
                self._atomic_write(f, new_text)
                self._after_odoo_write(f, self._rel(f, root), new_text, text)
                count += 1
        return count

    @api.model
    def backlinks(self, path):
        """Seiten, die auf ``path`` verlinken (Wikilinks oder Markdown-Links)."""
        self._check_read()
        root = self._root()
        full = self._abs(path, root)
        rel = self._rel(full, root)
        noext = rel[: -len(MD_EXT)] if rel.endswith(MD_EXT) else rel
        stem = os.path.basename(noext)
        rules = self._rules()
        result = []
        for f in self._walk_files(root):
            if not f.endswith(MD_EXT) or f == full or not self._can(self._rel(f, root), rules=rules):
                continue
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            hit = None
            for m in WIKILINK_RE.finditer(text):
                t = m.group(2).strip()
                t = t[: -len(MD_EXT)] if t.lower().endswith(MD_EXT) else t
                if t == stem or t == noext or noext.endswith("/" + t):
                    hit = m.start()
                    break
            if hit is None:
                idx = text.find(f"]({rel})")
                if idx < 0:
                    idx = text.find(f"]({rel.replace(' ', '%20')})")
                hit = idx if idx >= 0 else None
            if hit is not None:
                start = max(0, text.rfind("\n", 0, hit) + 1)
                end = text.find("\n", hit)
                frel = self._rel(f, root)
                result.append({
                    "path": frel,
                    "name": os.path.basename(frel)[: -len(MD_EXT)],
                    "snippet": text[start: end if end > 0 else None].strip()[:240],
                })
        return sorted(result, key=lambda r: _natural_key(r["name"]))

    # ------------------------------------------------------------------
    # Suche
    # ------------------------------------------------------------------
    @api.model
    def search(self, query, limit=MAX_SEARCH_RESULTS):
        self._check_read()
        root = self._root()
        q = (query or "").strip().casefold()
        if not q:
            return []
        results = []
        rules = self._rules()
        for f in self._walk_files(root):
            if not f.endswith(MD_EXT):
                continue
            rel = self._rel(f, root)
            if not self._can(rel, rules=rules):
                continue
            name = os.path.basename(rel)[: -len(MD_EXT)]
            score, snippet = 0, ""
            if q in name.casefold():
                score = 100 if name.casefold().startswith(q) else 80
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            idx = text.casefold().find(q)
            if idx >= 0:
                score = score or 10 + min(text.casefold().count(q), 40)
                s = max(0, idx - 60)
                snippet = ("…" if s else "") + text[s: idx + len(q) + 100].replace("\n", " ") + "…"
            if score:
                results.append({"path": rel, "name": name, "score": score, "snippet": snippet})
        results.sort(key=lambda r: (-r["score"], _natural_key(r["name"])))
        return results[: int(limit or MAX_SEARCH_RESULTS)]

    # ------------------------------------------------------------------
    # Anhänge
    # ------------------------------------------------------------------
    @api.model
    def upload_file(self, page_path, filename, data_b64):
        """Legt eine Datei (z. B. eingefügtes Bild) im Anhänge-Ordner ab und gibt den Vault-Pfad zurück."""
        self._check_write()
        root = self._root()
        raw = base64.b64decode(data_b64 or "")
        if len(raw) > MAX_UPLOAD_BYTES:
            raise UserError(self.env._("Die Datei ist größer als 25 MB."))
        stem, ext = os.path.splitext(os.path.basename(filename or "datei"))
        stem = INVALID_NAME_RE.sub("-", stem).strip(". ") or "datei"
        ext = re.sub(r"[^A-Za-z0-9.]", "", ext)[:10]
        folder = self._abs(self._param("attachment_folder", "Anhänge").strip("/"), root)
        os.makedirs(folder, exist_ok=True)
        dest = self._unique(os.path.join(folder, stem + ext))
        if ext.lower() == MD_EXT:
            raise UserError(self.env._("Markdown-Dateien bitte als Seite anlegen."))
        with open(dest, "wb") as fh:
            fh.write(raw)
        return self._rel(dest, root)

    # ------------------------------------------------------------------
    # @-Erwähnungen von Odoo-Datensätzen
    # ------------------------------------------------------------------
    def _mention_models(self):
        names = [m.strip() for m in self._param("mention_models", DEFAULT_MENTION_MODELS).split(",") if m.strip()]
        result = []
        for name in names:
            if name not in self.env:
                continue
            model = self.env[name]
            if model._abstract or model._transient or not model.has_access("read"):
                continue
            label = self.env["ir.model"]._get(name).name or name
            result.append({"model": name, "label": label})
        return result

    def _mention_image(self, model, rec_id):
        fields_ = self.env[model]._fields
        for fname in ("avatar_128", "image_128"):
            if fname in fields_:
                return f"/web/image/{model}/{rec_id}/{fname}"
        return False

    def _mention_categories(self):
        """Bereiche für das @-Menü, nur installierte und freigeschaltete Modelle."""
        enabled = {m["model"] for m in self._mention_models()}
        cats = [
            {"model": model, "aliases": aliases, "label": label, "icon": MODEL_ICONS.get(model, "fa-database")}
            for model, aliases, label in MENTION_CATEGORIES if model in enabled
        ]
        # zusätzlich freigeschaltete Modelle ohne eigenes Kürzel (nur per Freitext durchsuchbar)
        known = {c["model"] for c in cats}
        for m in self._mention_models():
            if m["model"] not in known:
                cats.append({"model": m["model"], "aliases": [], "label": m["label"], "icon": MODEL_ICONS.get(m["model"], "fa-database")})
        return cats

    @api.model
    def search_mentions(self, term, limit=6):
        """Autovervollständigung für @. "@aufgabe bank" sucht nur in Aufgaben."""
        self._check_read()
        term = (term or "").strip()
        models_ = self._mention_models()
        first, _sep, rest = term.partition(" ")
        alias = MENTION_ALIASES.get(first.casefold().rstrip(":"))
        if alias and any(m["model"] == alias for m in models_):
            models_ = [m for m in models_ if m["model"] == alias]
            term = rest.strip()
            limit = max(limit, 12)
        result = []
        for m in models_:
            try:
                records = self.env[m["model"]].name_search(term, limit=limit)
            except Exception:  # noqa: BLE001 - ein defektes Modell soll die Suche nicht abbrechen
                _logger.exception("Wissensmanagement: @-Suche in %s fehlgeschlagen", m["model"])
                continue
            recs = self.env[m["model"]].browse([r[0] for r in records])
            for rec, (rec_id, name) in zip(recs, records):
                short = self._short_name(rec) or name
                result.append({
                    "model": m["model"], "model_label": m["label"], "id": rec_id, "name": short,
                    "context": name if name != short else False,
                    "icon": MODEL_ICONS.get(m["model"], "fa-database"),
                    "image": self._mention_image(m["model"], rec_id),
                })
        # Treffer, deren Name mit dem Suchbegriff beginnt, zuerst
        q = term.casefold()
        result.sort(key=lambda r: (not (r["name"] or "").casefold().startswith(q), len(r["name"] or "")))
        return result[:20]

    @staticmethod
    def _short_name(rec):
        """Eigener Name statt display_name (z. B. "Claude Bot" statt "Kadea, Claude Bot")."""
        f = rec._fields.get("name")
        if f is not None and f.type == "char" and rec.name:
            return rec.name
        return rec.display_name

    def _card_value(self, rec, fname, field):
        value = rec[fname]
        if field.type == "many2one":
            return value.display_name or False
        if field.type == "selection":
            return dict(field._description_selection(self.env)).get(value) if value else False
        if field.type == "monetary":
            currency = rec[field.get_currency_field(rec)] if hasattr(field, "get_currency_field") else False
            currency = currency or rec.env.company.currency_id
            return format_amount(self.env, value, currency) if value else False
        if field.type in ("float", "integer"):
            return value or False
        if field.type in ("date", "datetime"):
            return fields.Date.to_string(value) if field.type == "date" else fields.Datetime.to_string(value)
        return value or False

    def _mention_card(self, rec):
        """Generische Vorschaukarte für einen beliebigen Datensatz (nur Felder, die der Benutzer lesen darf)."""
        model = rec._name
        readable = set(rec.fields_get(attributes=["type"]).keys())
        fields_ = {n: f for n, f in rec._fields.items() if n in readable}
        card = {
            "exists": True,
            "model": model,
            "id": rec.id,
            "name": self._short_name(rec),
            "label": self.env["ir.model"]._get(model).name or model,
            "icon": MODEL_ICONS.get(model, "fa-database"),
            "image": self._mention_image(model, rec.id),
            "subtitle": False,
            "badges": [],
            "priority": False,
            "users": [],
            "tags": [],
            "facts": [],
            "deadline": False,
            "overdue": False,
            "active": rec["active"] if "active" in fields_ else True,
        }
        if model == "res.partner" and rec.is_company:
            card["icon"] = "fa-building"
        subtitle_field = None
        for fname in CARD_SUBTITLE_FIELDS:
            if fname in fields_ and rec[fname]:
                card["subtitle"] = self._card_value(rec, fname, fields_[fname])
                subtitle_field = fname
                break
        for fname in CARD_BADGE_FIELDS:
            if fname in fields_ and rec[fname] and rec[fname] not in EMPTY_BADGE_VALUES:
                card["badges"].append({"field": fname, "label": self._card_value(rec, fname, fields_[fname])})
        prio = fields_.get("priority")
        if prio and prio.type == "selection":
            keys = [k for k, _l in prio._description_selection(self.env)]
            if all(str(k).isdigit() for k in keys) and len(keys) > 1:
                card["priority"] = {"value": keys.index(rec.priority) if rec.priority in keys else 0, "max": len(keys) - 1}
        users = self.env["res.users"]
        for fname in ("user_ids", "user_id"):
            f = fields_.get(fname)
            if f and f.comodel_name == "res.users":
                users |= rec[fname]
        card["users"] = [{"id": u.id, "name": u.name, "image": f"/web/image/res.users/{u.id}/avatar_128"} for u in users[:5]]
        for fname in ("tag_ids", "category_id"):
            f = fields_.get(fname)
            if f and f.type == "many2many":
                for tag in rec[fname][:6]:
                    color = tag["color"] if "color" in tag._fields else 0
                    card["tags"].append({"name": tag.display_name, "color": color or 0})
                break
        for fname, label in CARD_FACT_FIELDS:
            if len(card["facts"]) >= 4:
                break
            f = fields_.get(fname)
            if not f or not rec[fname] or fname == subtitle_field:
                continue
            value = self._card_value(rec, fname, f)
            if value is False:
                continue
            if fname in DEADLINE_FIELDS:
                card["deadline"] = value[:10]
                card["overdue"] = value[:10] < fields.Date.to_string(fields.Date.context_today(self))
                continue
            card["facts"].append({"label": label or f.string, "value": value, "field": fname, "type": f.type})
        return card

    @api.model
    def mention_cards(self, refs):
        """refs: [[model, id], …] -> {"model/id": Karte}. Rechte des aktuellen Benutzers gelten."""
        self._check_read()
        out = {}
        for model, rec_id in refs or []:
            key = f"{model}/{rec_id}"
            if key in out:
                continue
            if model not in self.env or self.env[model]._abstract:
                out[key] = {"exists": False}
                continue
            try:
                rec = self.env[model].browse(int(rec_id)).exists()
                if not rec:
                    out[key] = {"exists": False}
                    continue
                rec.check_access("read")
                out[key] = self._mention_card(rec)
            except AccessError:
                out[key] = {"exists": True, "restricted": True, "model": model, "id": int(rec_id),
                            "label": self.env["ir.model"]._get(model).name or model,
                            "icon": MODEL_ICONS.get(model, "fa-database")}
        return out

    # Abwärtskompatibel
    resolve_mentions = mention_cards

    @api.model
    def find_mentions(self, model, res_id):
        """Alle Seiten, in denen ein Datensatz erwähnt wird (Rückrichtung Odoo -> Wissen)."""
        self._check_read()
        root = self._root()
        needle = f"(odoo://{model}/{int(res_id)})"
        result = []
        for f in self._walk_files(root):
            if not f.endswith(MD_EXT):
                continue
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            if needle in text and self._can(self._rel(f, root)):
                rel = self._rel(f, root)
                result.append({"path": rel, "name": os.path.basename(rel)[: -len(MD_EXT)]})
        return result
