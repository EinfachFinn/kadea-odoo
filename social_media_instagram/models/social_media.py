# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class SocialMedia(models.Model):
    """Registers Instagram as an available social media."""

    _inherit = "social.media"

    media_type = fields.Selection(
        selection_add=[("instagram", "Instagram")],
        ondelete={"instagram": "cascade"},
    )

    def open_action_account(self):
        res = super().open_action_account()
        if self.media_type == "instagram":
            return {
                "res_model": "wizard.social.account",
                "views": [[False, "form"]],
                "target": "new",
                "type": "ir.actions.act_window",
                "context": {
                    "default_media_id": self.id,
                },
            }
        return res
