{
    "name": "Wissensmanagement",
    "summary": "Wiki direkt in Odoo: Seitenbaum, WYSIWYG-Editor, Vorlagen, Seitenlinks und @-Erwähnungen mit Live-Karten aller Odoo-Datensätze",
    "version": "19.0.3.1.0",
    "category": "Productivity/Knowledge",
    "author": "Kadea",
    "license": "LGPL-3",
    "application": True,
    "depends": ["base_setup", "web", "bus"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/res_config_settings_views.xml",
        "views/wissen_access_rule_views.xml",
        "views/share_templates.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "wissensmanagement/static/lib/marked/marked.umd.js",
            "wissensmanagement/static/lib/toastui/toastui-editor.css",
            "wissensmanagement/static/lib/toastui/toastui-editor-all.min.js",
            "wissensmanagement/static/src/scss/*.scss",
            "wissensmanagement/static/src/js/*.js",
            "wissensmanagement/static/src/xml/*.xml",
        ],
    },
}
