# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models
from odoo.exceptions import UserError

from odoo.addons.social_media_base.exceptions import SocialCredentialsError

from ..social_instagram_utils import shortcode_from_url


class WizardInstagramMarkPublished(models.TransientModel):
    """Link a planned Instagram publication to what the user published."""

    _name = "wizard.instagram.mark.published"
    _description = "Mark an Instagram Publication as Published"

    post_account_id = fields.Many2one(
        "social.post.account", required=True, ondelete="cascade"
    )
    account_id = fields.Many2one(related="post_account_id.account_id")
    message = fields.Text(related="post_account_id.message")
    permalink = fields.Char(
        string="Link to the Publication",
        help="Copy the link of the publication in the Instagram app (Share > "
        "Copy link). Leave it empty to let Odoo look for the publication by "
        "its caption among the latest ones of the account.",
    )

    def action_confirm(self):
        self.ensure_one()
        line = self.post_account_id
        if line.state != "manual":
            raise UserError(self.env._("This publication is not waiting to be published."))
        shortcode = shortcode_from_url(self.permalink)
        if self.permalink and not shortcode:
            raise UserError(
                self.env._(
                    "This does not look like the link of an Instagram "
                    "publication, which reads https://www.instagram.com/p/..."
                )
            )
        account = line.account_id
        try:
            account.with_context(not_notify=True)._instagram_validate_access_token()
        except SocialCredentialsError as error:
            account._flag_credentials_expired(str(error))
            if not self.permalink:
                raise UserError(
                    self.env._(
                        "The Instagram authorization of the account expired, so "
                        "Odoo cannot look the publication up. Paste its link, "
                        "or update the account first."
                    )
                ) from error
            line._instagram_mark_published_without_api(self.permalink)
            return {"type": "ir.actions.act_window_close"}
        medias = account._instagram_fetch_media(limit=50)
        media = False
        if shortcode:
            media = next(
                (
                    candidate
                    for candidate in medias
                    if candidate.get("shortcode") == shortcode
                    or shortcode_from_url(candidate.get("permalink")) == shortcode
                ),
                False,
            )
            if not media:
                raise UserError(
                    self.env._(
                        "This publication is not among the latest 50 of "
                        "@%(username)s. Check that it was published on this "
                        "account.",
                        username=account.username,
                    )
                )
        else:
            linked = set(
                self.env["social.post.account"]
                .search(
                    [
                        ("account_id", "=", account.id),
                        ("remote_ref", "!=", False),
                        ("post_id", "!=", False),
                    ]
                )
                .mapped("remote_ref")
            )
            candidates = [m for m in medias if m.get("id") not in linked]
            match = None
            for candidate in candidates:
                if account._instagram_match_pending(line, candidate):
                    match = candidate
                    break
            if not match:
                raise UserError(
                    self.env._(
                        "No recent publication of @%(username)s starts with the "
                        "planned caption. Paste the link of the publication.",
                        username=account.username,
                    )
                )
            media = match
        line._instagram_link_media(media)
        return {"type": "ir.actions.act_window_close"}
