# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from markupsafe import Markup

from odoo import fields, models
from odoo.exceptions import UserError

from .social_account import InstagramApiError

_logger = logging.getLogger(__name__)

# What Instagram answers for a media that does not exist (any more):
# "Unsupported get request. Object with ID ... does not exist".
_MEDIA_GONE_CODE = 100
_MEDIA_GONE_SUBCODE = 33


class SocialPostAccount(models.Model):
    """An Instagram publication: planned, then published by hand.

    Nothing here uploads anything to Instagram. When its post is sent, the
    publication goes to *To publish manually*; it becomes *Posted* once the
    publication made in the Instagram app is linked to it, and from then on
    its figures are read back like those of any other social media.
    """

    _inherit = "social.post.account"

    state = fields.Selection(
        selection_add=[("manual", "To Publish Manually"), ("posted",)],
        ondelete={"manual": "set default"},
    )
    instagram_media_type = fields.Char(string="Instagram Media Type", readonly=True)
    instagram_media_product_type = fields.Char(string="Product Type", readonly=True)
    instagram_shortcode = fields.Char(string="Shortcode", readonly=True)
    instagram_reach = fields.Integer(string="Reach", readonly=True)
    instagram_saved = fields.Integer(string="Saves", readonly=True)
    instagram_scheduled_date = fields.Datetime(
        related="post_id.send_post_date", string="Scheduled For", store=True
    )
    instagram_post_format = fields.Selection(related="post_id.instagram_post_format")
    instagram_first_comment = fields.Text(related="post_id.instagram_first_comment")
    instagram_publish_notes = fields.Text(related="post_id.instagram_publish_notes")

    def _action_post(self, post_id):
        """Plan the Instagram publications of the post instead of sending them.

        Each publication goes through the guard and the checks of the base, so
        a post Instagram would refuse fails on its own line with the reason,
        exactly as a refused publication of another social media does. A
        publication that passes waits for the user; its message is the one
        the base prepared, tracked links included.
        """
        res = super()._action_post(post_id)
        if not any(account.media_type == "instagram" for account in post_id.account_ids):
            return res
        planned = self.browse()
        lines = post_id._filter_by_media_types(["instagram"]).filtered(
            lambda line: line.media_type == "instagram"
        )
        for line in lines:
            with line._publish_guard():
                line._check_publishable()
                line.write({"state": "manual", "failed_description": False})
                planned |= line
        planned = planned.filtered(lambda line: line.state == "manual")
        if planned:
            post_id.message_post(
                body=self.env._(
                    "The post is due on Instagram (%(accounts)s). Publish it in "
                    "the Instagram app: it is linked here as soon as it appears "
                    "on the account.",
                    accounts=", ".join(
                        f"@{account.username or account.name}"
                        for account in planned.account_id
                    ),
                ),
                partner_ids=(post_id.user_id | planned.account_id.user_id).partner_id.ids,
            )
            self.env["social.account"]._instagram_notify_due(planned)
        return res

    def action_instagram_mark_published(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Mark as published on Instagram"),
            "res_model": "wizard.instagram.mark.published",
            "view_mode": "form",
            "target": "new",
            "context": {"default_post_account_id": self.id},
        }

    def action_instagram_refresh(self):
        """Read the figures of this publication now."""
        self.ensure_one()
        if not self.remote_ref:
            raise UserError(
                self.env._("This publication is not linked to Instagram yet.")
            )
        self.account_id._refresh_post_statistics(self)
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    def _instagram_log_published(self):
        """Leave a trace on the post when a publication joins its plan."""
        for line in self.filtered("post_id"):
            line.post_id.message_post(
                body=Markup('%s <a href="%s" target="_blank">%s</a>')
                % (
                    self.env._(
                        "Published on Instagram (@%(username)s):",
                        username=line.account_id.username or line.account_id.name,
                    ),
                    line.post_account_url or "#",
                    line.post_account_url or "",
                )
            )

    def _instagram_link_media(self, media):
        """Link this planned publication to a media published by hand."""
        self.ensure_one()
        account = self.account_id
        other = (
            self.sudo()
            .with_context(active_test=False)
            .search(
                [
                    ("account_id", "=", account.id),
                    ("remote_ref", "=", media["id"]),
                    ("id", "!=", self.id),
                ],
                limit=1,
            )
        )
        if other.post_id:
            raise UserError(
                self.env._(
                    "This Instagram publication is already linked to another "
                    "post of Odoo."
                )
            )
        if other:
            # Imported by the check for updates before being linked: the plan
            # takes its place, so the publication is not shown twice.
            other.unlink()
        self.write(account._instagram_media_values(media))
        account._refresh_post_statistics(self)
        self._instagram_log_published()
        self.post_id._instagram_after_manual_publication()

    def _instagram_mark_published_without_api(self, url):
        """Close a planned publication Odoo cannot read back from Instagram."""
        self.ensure_one()
        self.write(
            {
                "state": "posted",
                "post_account_url": url or False,
                "published_date": fields.Datetime.now(),
            }
        )
        self._instagram_log_published()
        self.post_id._instagram_after_manual_publication()

    def _check_remote_post_exists(self):
        """Read the media on Instagram to know whether it is still online.

        Fails open, as the base asks: only the answer Instagram gives for a
        media that does not exist marks it as deleted. A refused token, a
        rate limit or a network error leave it untouched.
        """
        if self.media_type != "instagram" or not self.remote_ref:
            return super()._check_remote_post_exists()
        try:
            self.account_id._request_instagram(
                f"/{self.remote_ref}", params={"fields": "id"}
            )
        except InstagramApiError as error:
            if error.code == _MEDIA_GONE_CODE and error.subcode in (
                None,
                _MEDIA_GONE_SUBCODE,
            ):
                self._register_remote_post_gone()
                return False
            _logger.warning(
                "Could not check the Instagram media %s, it is left untouched: %s",
                self.remote_ref,
                error,
            )
        except UserError as error:
            _logger.warning(
                "Could not check the Instagram media %s, it is left untouched: %s",
                self.remote_ref,
                error,
            )
        return True

    def _delete_post_account(self):
        """Instagram publications cannot be deleted through the API."""
        if self.media_type == "instagram":
            return None
        return super()._delete_post_account()

    def action_delete_post_account(self):
        """Say that an Instagram publication was only removed from Odoo.

        What the base answers on success reads as if the publication were
        gone from the social media, which is not true for Instagram. Its
        answer to a failure is left as it is.
        """
        self.ensure_one()
        online = self.media_type == "instagram" and bool(self.remote_ref)
        res = super().action_delete_post_account()
        params = res.get("params") if isinstance(res, dict) else None
        if online and params and params.get("type") == "success":
            params.update(
                {
                    "type": "warning",
                    "message": self.env._(
                        "The publication was removed from Odoo only. Delete it "
                        "in the Instagram app if it should disappear there too."
                    ),
                }
            )
        return res
