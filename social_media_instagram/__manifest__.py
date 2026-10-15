# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Social Media Instagram",
    "summary": "Plan Instagram posts (manual publication) and follow the "
    "statistics of your Instagram professional accounts",
    "version": "19.0.2.0.0",
    "category": "Social Network",
    "development_status": "Beta",
    "license": "AGPL-3",
    "uninstall_hook": "uninstall_hook",
    "author": "Kadea",
    "website": "https://github.com/EinfachFinn/kadea-odoo",
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
    "installable": True,
}
