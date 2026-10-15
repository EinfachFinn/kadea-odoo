from odoo import fields, models

from .wissen_vault import DEFAULT_MENTION_MODELS


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    wm_vault_path = fields.Char(
        string="Wissensordner",
        config_parameter="wissensmanagement.vault_path",
        help="Absoluter Pfad auf dem Odoo-Server zum Ordner mit den .md-Dateien, "
        "z. B. /srv/vault. Der Odoo-Prozess braucht Lese- und Schreibrechte.",
    )
    wm_template_folder = fields.Char(
        string="Vorlagen-Ordner",
        config_parameter="wissensmanagement.template_folder",
        default="Vorlagen",
        help="Unterordner im Wissensordner. Alle .md-Dateien darin werden beim Anlegen einer Seite als Vorlage angeboten.",
    )
    wm_attachment_folder = fields.Char(
        string="Anhänge-Ordner",
        config_parameter="wissensmanagement.attachment_folder",
        default="Anhänge",
        help="Relativer Pfad im Vault, in den eingefügte Bilder und Dateien abgelegt werden.",
    )
    wm_mention_models = fields.Char(
        string="Erwähnbare Modelle",
        config_parameter="wissensmanagement.mention_models",
        default=DEFAULT_MENTION_MODELS,
        help="Kommagetrennte technische Modellnamen, die per @ erwähnt werden können. "
        "Nicht installierte Modelle werden ignoriert.",
    )
    wm_default_mode = fields.Selection(
        [("read", "Lesen"), ("write", "Schreiben")],
        string="Standardmodus",
        config_parameter="wissensmanagement.default_mode",
        default="read",
    )
    wm_style_preset = fields.Selection(
        [("default", "Standard"), ("notion", "Notion"), ("serif", "Buch (Serif)"), ("compact", "Kompakt")],
        string="Stil-Vorlage",
        config_parameter="wissensmanagement.style_preset",
        help="Standard: Odoo-Look mit farbigen Links und Linien unter Überschriften. "
        "Notion: ruhig und reduziert, warmes Schwarz, graue Hinweisboxen, dezente Links. "
        "Buch: Serifenschrift mit viel Zeilenabstand zum Lesen langer Texte. "
        "Kompakt: wenig Abstand, viel Inhalt auf einen Blick.",
        default="default",
    )
    wm_accent_color = fields.Char(
        string="Akzentfarbe", config_parameter="wissensmanagement.accent_color", default="#714B67",
    )
    wm_wikilink_color = fields.Char(
        string="Farbe Seitenlinks", config_parameter="wissensmanagement.wikilink_color", default="#7c3aed",
    )
    wm_mention_color = fields.Char(
        string="Farbe Erwähnungen", config_parameter="wissensmanagement.mention_color", default="#0e7490",
    )
    wm_content_width = fields.Integer(
        string="Max. Textbreite (px)", config_parameter="wissensmanagement.content_width", default=860,
    )
    wm_font_size = fields.Integer(
        string="Schriftgröße (px)", config_parameter="wissensmanagement.font_size", default=16,
    )
    wm_custom_css = fields.Char(
        string="Eigenes CSS",
        config_parameter="wissensmanagement.custom_css",
        help="Wird nur innerhalb der Wissensmanagement-App angewendet (Container .o_wm_app).",
    )
