# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class SocialMedia(models.Model):
    """Registers Instagram as an available social media."""

    _inherit = "social.media"

    media_type = fields.Selection(
        selection_add=[("instagram", "Instagram")],
        ondelete={"instagram": "cascade"},
    )

    def _get_utm_medium(self):
        """Instagram publications are reported under the Instagram medium.

        ``utm`` ships no medium for Instagram, so the connector brings its
        own; it is resolved here rather than written on the social media for
        the same reason LinkedIn gives: a medium chosen by the integrator on
        the social media must survive a module update.
        """
        self.ensure_one()
        if not self.utm_medium_id and self.media_type == "instagram":
            return (
                self.env.ref(
                    "social_media_instagram.utm_medium_instagram",
                    raise_if_not_found=False,
                )
                or super()._get_utm_medium()
            )
        return super()._get_utm_medium()

    def action_open_account(self):
        """Open the wizard associating an Instagram account.

        No ``ensure_one``: every connector chains to the next one, so an empty
        recordset has to travel down to the hook of the base.
        """
        res = super().action_open_account()
        if self.media_type == "instagram":
            return {
                "res_model": "wizard.social.account",
                "views": [[False, "form"]],
                "target": "new",
                "type": "ir.actions.act_window",
                "context": {"default_media_id": self.id},
            }
        return res
