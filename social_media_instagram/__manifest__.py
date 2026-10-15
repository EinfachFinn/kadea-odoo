# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Social Media Instagram",
    "summary": "Plan Instagram posts (manual publication), statistics and "
    "comments of your Instagram professional accounts",
    "version": "19.0.1.0.0",
    "category": "Social Network",
    "development_status": "Beta",
    "license": "AGPL-3",
    "uninstall_hook": "uninstall_hook",
    "author": "Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/social",
    "depends": [
        "social_media_base",
        "social_media_calendar",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/social_media_data.xml",
        "views/social_account_views.xml",
        "views/social_post_views.xml",
        "views/social_post_account_views.xml",
        "wizards/wizard_social_account.xml",
        "wizards/wizard_instagram_mark_published.xml",
        "views/social_media_instagram_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "social_media_instagram/static/src/js/**/*.js",
        ],
    },
    "installable": True,
}
