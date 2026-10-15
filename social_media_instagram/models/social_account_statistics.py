# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class SocialAccountStatistics(models.Model):
    """The two daily figures Instagram reports beyond the common ones."""

    _inherit = "social.account.statistics"

    instagram_reach = fields.Integer(
        string="Reach",
        aggregator="avg",
        help="Accounts that saw the content of the account that day. The same "
        "account counts once a day but again the next one, so periods are "
        "averaged instead of added up.",
    )
    instagram_saves = fields.Integer(
        string="Saves",
        help="Times the publications of the account were saved that day.",
    )
