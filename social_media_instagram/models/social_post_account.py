# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from markupsafe import Markup

from odoo import Command, fields, models
from odoo.exceptions import UserError
from odoo.tools import plaintext2html

from odoo.addons.social_media_base.social_utils import convert_date_in_time

from ..social_instagram_utils import (
    _MEDIA_FIELDS_INSTAGRAM,
    parse_instagram_datetime,
)

_logger = logging.getLogger(__name__)


class SocialPostAccount(models.Model):
    """An Instagram publication: planned, then published by hand.

    Nothing here uploads anything to Instagram. When its post is due, the
    publication goes to *To publish manually*; it becomes *Posted* once the
    publication made in the Instagram app is linked to it, and from then on
    its statistics are read back like those of any other social media.
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
    instagram_statistics_date = fields.Datetime(
        string="Statistics Read On", readonly=True
    )
    instagram_scheduled_date = fields.Datetime(
        related="post_id.send_post_date", string="Scheduled For", store=True
    )
    instagram_post_format = fields.Selection(related="post_id.instagram_post_format")
    instagram_first_comment = fields.Text(related="post_id.instagram_first_comment")
    instagram_publish_notes = fields.Text(related="post_id.instagram_publish_notes")
    instagram_username = fields.Char(related="account_id.username", string="Instagram Account")

    def _action_post(self, post_id):
        """Plan the Instagram publications of the post instead of sending them.

        The rules of Instagram are checked first, since what the app refuses
        is better read now than discovered with the phone in hand. A
        publication that passes waits for the user, with the medias of the
        post attached so they can be downloaded from the publication itself.
        """
        res = super()._action_post(post_id)
        lines = post_id.filter_by_media_types(["instagram"])
        if not lines:
            return res
        errors, __ = post_id._instagram_check()
        if errors:
            message = "\n".join(errors)
            lines.write({"state": "failed", "failed_description": plaintext2html(message)})
            post_id._message_error_post(message, "instagram")
            return res
        lines.write(
            {
                "state": "manual",
                "failed_description": False,
                "message": post_id.message,
                "image_ids": [Command.set(post_id.image_ids.ids)],
                "video_ids": [Command.set(post_id.video_ids.ids)],
                "has_video": bool(post_id.video_ids),
            }
        )
        post_id.message_post(
            body=Markup("%s") % self.env._(
                "The post is due on Instagram (%(accounts)s). Publish it in the "
                "Instagram app: it is linked here as soon as it appears on the "
                "account.",
                accounts=", ".join(
                    f"@{account.username or account.name}" for account in lines.account_id
                ),
            ),
            partner_ids=(post_id.user_id | lines.account_id.user_id).partner_id.ids,
        )
        self.env["social.account"]._instagram_notify_due(lines)
        return res

    def _anchor_media_attachments(self):
        """The medias of a planned publication belong to its post."""
        own = self.filtered(lambda line: line.media_type != "instagram" or not line.post_id)
        return super(SocialPostAccount, own)._anchor_media_attachments()

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
        self.ensure_one()
        if not self.remote_ref:
            raise UserError(
                self.env._("This publication is not linked to Instagram yet.")
            )
        self.account_id._instagram_sync_media(remote_ref=self.remote_ref)
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    def action_open_post_account_url(self):
        self.ensure_one()
        if not self.post_account_url:
            return False
        return {"type": "ir.actions.act_url", "url": self.post_account_url, "target": "new"}

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
        other = self.search(
            [
                ("account_id", "=", account.id),
                ("remote_ref", "=", media["id"]),
                ("id", "!=", self.id),
            ],
            limit=1,
        )
        if other and other.post_id:
            raise UserError(
                self.env._(
                    "This Instagram publication is already linked to another "
                    "post of Odoo."
                )
            )
        if other:
            # Imported by a synchronization before being linked: the plan
            # takes its place, so the publication is not shown twice.
            other.unlink()
        insights = account._instagram_media_insights(media)
        self.write(account._instagram_media_values(media, insights))
        self._instagram_log_published()
        self.post_id._instagram_update_state()
        account._instagram_update_totals()

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
        self.post_id._instagram_update_state()

    def delete_post_account(self):
        """Instagram publications are only removed from Odoo.

        The API does not delete publications; the user is told to do it in
        the app instead of reading that it was deleted.
        """
        self.ensure_one()
        online = self.media_type == "instagram" and self.remote_ref
        res = super().delete_post_account()
        if online and isinstance(res, dict) and res.get("params"):
            res["params"].update(
                {
                    "type": "warning",
                    "message": self.env._(
                        "The publication was removed from Odoo only. Delete it "
                        "in the Instagram app if it should disappear there too."
                    ),
                }
            )
        return res

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def get_comments(self):
        data = super().get_comments()
        if self.account_id.media_type != "instagram" or not self.remote_ref:
            return data
        try:
            payload = self.account_id._request_instagram(
                f"/{self.remote_ref}/comments",
                params={"fields": "id,text,username,timestamp,like_count"},
            )
        except UserError as error:
            return {"success": False, "message": str(error)}
        comments = []
        for comment in payload.get("data", []):
            published = parse_instagram_datetime(comment.get("timestamp"))
            comments.append(
                {
                    "id": comment.get("id"),
                    "text": comment.get("text", ""),
                    "actor": comment.get("username", ""),
                    "published_time": convert_date_in_time(
                        int(published.timestamp() * 1000), self.env.user.tz
                    )
                    if published
                    else "",
                    "images_url": [],
                }
            )
        return {"success": True, "data": data.get("data", []) + comments}

    def create_comment(self, post_data, context=None):
        """Answer on the publication, written by a user from the dashboard."""
        if self.account_id.media_type != "instagram":
            return super().create_comment(post_data, context)
        try:
            self.account_id._request_instagram(
                f"/{self.remote_ref}/comments",
                method="POST",
                params={"message": (post_data or {}).get("body", "")},
            )
        except UserError as error:
            return {"success": False, "message": str(error)}
        return {"success": True}

    def get_post_instagram(self):
        """Whether the publication still exists, asked by the dashboard."""
        self.ensure_one()
        if not self.remote_ref:
            return False
        try:
            self.account_id._request_instagram(
                f"/{self.remote_ref}", params={"fields": "id"}
            )
        except UserError as error:
            if getattr(error, "code", None) == 100:
                self.write({"state": "deleted", "post_account_url": False})
                return False
            _logger.warning("Could not check the Instagram media %s: %s", self.remote_ref, error)
        return True

    def _instagram_fetch_media_by_ref(self, remote_ref):
        return self.account_id._request_instagram(
            f"/{remote_ref}", params={"fields": _MEDIA_FIELDS_INSTAGRAM}
        )
