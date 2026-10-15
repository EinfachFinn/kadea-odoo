"""Metadaten zu Wissensseiten.

Die Inhalte bleiben in den .md-Dateien. In der Datenbank liegt nur, was eine Datei
nicht speichern kann: wer zuletzt bearbeitet hat, Versionen, Kommentare, Favoriten,
manuelle Reihenfolge, Freigaben, Ordnerrechte und ein Index (Tags, Links, @-Erwähnungen)
für schnelle Übersichten und den "Wissen"-Button in allen Odoo-Formularen.
"""

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import ormcache


class WissenPage(models.Model):
    _name = "wissen.page"
    _description = "Wissensseite (Metadaten & Index)"
    _order = "path"
    _rec_name = "path"

    path = fields.Char(required=True, index=True, help="Pfad relativ zum Wissensordner, z. B. Kunden/Müller.md")
    name = fields.Char()
    is_folder = fields.Boolean(help="Eintrag nur für die Reihenfolge eines Ordners ohne eigene Seite")
    sequence = fields.Integer(default=0, help="Manuelle Reihenfolge im Seitenbaum (0 = alphabetisch)")
    mtime = fields.Float(digits=(16, 6))
    last_editor_id = fields.Many2one("res.users", ondelete="set null")
    last_edit_date = fields.Datetime()
    external_change = fields.Boolean(help="Zuletzt direkt im Wissensordner geändert (nicht über Odoo)")
    tags = fields.Char(help="Leerzeichen-getrennte Tags ohne #")
    link_targets = fields.Text(help="Ein Link-Ziel pro Zeile, wie im Text geschrieben")
    word_count = fields.Integer()
    favorite_user_ids = fields.Many2many("res.users", "wissen_page_favorite_rel", "page_id", "user_id")
    mention_ids = fields.One2many("wissen.mention", "page_id")
    version_ids = fields.One2many("wissen.version", "page_id")
    comment_ids = fields.One2many("wissen.comment", "page_id")
    share_mode = fields.Selection(
        [("off", "Nicht freigegeben"), ("link", "Jeder mit dem Link"), ("portal", "Ausgewählte Kontakte (Portal)")],
        default="off", required=True,
    )
    share_token = fields.Char(copy=False, index=True)
    share_partner_ids = fields.Many2many("res.partner", "wissen_page_share_partner_rel", "page_id", "partner_id")

    _path_uniq = models.Constraint("UNIQUE(path)", "Jede Seite darf nur einmal im Index stehen.")


class WissenMention(models.Model):
    _name = "wissen.mention"
    _description = "Wissen: @-Erwähnung eines Datensatzes"

    page_id = fields.Many2one("wissen.page", required=True, ondelete="cascade", index=True)
    res_model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)


class WissenVersion(models.Model):
    _name = "wissen.version"
    _description = "Wissen: Version einer Seite"
    _order = "id desc"

    page_id = fields.Many2one("wissen.page", required=True, ondelete="cascade", index=True)
    content = fields.Text()
    user_id = fields.Many2one("res.users", ondelete="set null")
    kind = fields.Selection(
        [("initial", "Ausgangsstand"), ("odoo", "Bearbeitet in Odoo"), ("external", "Extern geändert"),
         ("restore", "Wiederhergestellt")],
        default="odoo", required=True,
    )
    size = fields.Integer()


class WissenComment(models.Model):
    _name = "wissen.comment"
    _description = "Wissen: Kommentar"
    _order = "create_date, id"

    page_id = fields.Many2one("wissen.page", required=True, ondelete="cascade", index=True)
    parent_id = fields.Many2one("wissen.comment", ondelete="cascade")
    author_id = fields.Many2one("res.users", required=True, default=lambda self: self.env.user, ondelete="cascade")
    body = fields.Text(required=True)
    quote = fields.Char(help="Textstelle, auf die sich der Kommentar bezieht")
    resolved = fields.Boolean()


class WissenPresence(models.Model):
    _name = "wissen.presence"
    _description = "Wissen: Wer ist gerade auf welcher Seite"

    path = fields.Char(required=True, index=True)
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade", index=True)
    last_seen = fields.Datetime(required=True)
    editing = fields.Boolean()


class WissenAccessRule(models.Model):
    _name = "wissen.access.rule"
    _description = "Wissen: Rechte pro Ordner"
    _order = "folder"

    name = fields.Char(required=True)
    folder = fields.Char(required=True, help="Ordner relativ zum Wissensordner, z. B. Vertrieb. Gilt inkl. aller Unterordner.")
    read_group_ids = fields.Many2many(
        "res.groups", "wissen_access_read_group_rel", "rule_id", "group_id", string="Lesen",
        help="Nur Mitglieder dieser Gruppen sehen den Ordner. Leer = alle Wissensmanagement-Leser.",
    )
    write_group_ids = fields.Many2many(
        "res.groups", "wissen_access_write_group_rel", "rule_id", "group_id", string="Schreiben",
        help="Nur Mitglieder dieser Gruppen dürfen im Ordner bearbeiten. Leer = wie Lesen.",
    )
    active = fields.Boolean(default=True)

    _folder_uniq = models.Constraint("UNIQUE(folder)", "Pro Ordner ist nur eine Regel möglich.")

    @api.constrains("folder")
    def _check_folder(self):
        for rule in self:
            parts = (rule.folder or "").split("/")
            if not rule.folder or any(p in ("", ".", "..") or p.startswith(".") for p in parts):
                raise ValidationError(self.env._(
                    "Ungültiger Ordner „%s“: bitte einen Pfad relativ zum Wissensordner angeben, z. B. Vertrieb oder Kunden/Intern.",
                    rule.folder,
                ))

    @api.model
    @ormcache()
    def _get_rules_cached(self):
        """Alle aktiven Regeln als unveränderliche Struktur. Wird bei jeder Seitenansicht vielfach
        gebraucht und ändert sich selten -> ormcache, Invalidierung in create/write/unlink."""
        rules = self.sudo().search([("active", "=", True)])
        return tuple((r.folder, frozenset(r.read_group_ids.ids), frozenset(r.write_group_ids.ids)) for r in rules)

    def write(self, vals):
        if "folder" in vals:
            vals["folder"] = (vals["folder"] or "").strip().strip("/")
        res = super().write(vals)
        self.env.registry.clear_cache()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "folder" in vals:
                vals["folder"] = (vals["folder"] or "").strip().strip("/")
        records = super().create(vals_list)
        self.env.registry.clear_cache()
        return records

    def unlink(self):
        res = super().unlink()
        self.env.registry.clear_cache()
        return res


class WissenTrash(models.Model):
    _name = "wissen.trash"
    _description = "Wissen: Papierkorb"
    _order = "create_date desc, id desc"

    name = fields.Char(required=True)
    trash_path = fields.Char(required=True, index=True, help="Ablage im versteckten Papierkorb-Ordner")
    origin_path = fields.Char(required=True, help="Ursprünglicher Pfad, an den wiederhergestellt wird")
    is_page = fields.Boolean(default=True)
    user_id = fields.Many2one("res.users", ondelete="set null", default=lambda self: self.env.user)


class WissenCollabSession(models.Model):
    """Gemeinsame Bearbeitung einer Seite: Ausgangstext plus fortlaufend nummerierte Editor-Schritte.

    Alle Mitschreibenden starten vom selben ``base`` und wenden dieselben Schritte in derselben
    Reihenfolge an. So haben alle denselben Stand, ohne dass der Server den Editor verstehen muss."""

    _name = "wissen.collab.session"
    _description = "Wissen: Live-Mitschreiben"

    path = fields.Char(required=True, index=True)
    token = fields.Char(required=True, help="Wechselt bei jedem Neustart der Sitzung")
    version = fields.Integer(default=0, help="Anzahl der bestätigten Schritte")
    base = fields.Text(help="Ausgangstext beim Start der Sitzung")
    saved_version = fields.Integer(default=0)
    saved_mtime = fields.Float(digits=(16, 6))
    saved_content = fields.Text()
    last_activity = fields.Datetime()
    step_ids = fields.One2many("wissen.collab.step", "session_id")

    _path_uniq = models.Constraint("UNIQUE(path)", "Pro Seite gibt es nur eine Sitzung.")


class WissenCollabStep(models.Model):
    _name = "wissen.collab.step"
    _description = "Wissen: Schritt beim Live-Mitschreiben"
    _order = "version"

    session_id = fields.Many2one("wissen.collab.session", required=True, ondelete="cascade", index=True)
    version = fields.Integer(required=True)
    client_id = fields.Char(required=True)
    data = fields.Text(required=True, help="ProseMirror-Schritt als JSON")
    user_id = fields.Many2one("res.users", ondelete="set null")

    _version_uniq = models.Constraint("UNIQUE(session_id, version)", "Jeder Schritt hat eine eindeutige Nummer.")
