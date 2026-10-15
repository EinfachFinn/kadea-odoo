# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import pytz
from markupsafe import Markup

from odoo import api, fields, models
from odoo.tools import format_datetime, html2plaintext, human_size

from ..social_instagram_utils import (
    _IMAGE_MIMETYPES_INSTAGRAM,
    _MAX_CAPTION_LENGTH_INSTAGRAM,
    _MAX_CAROUSEL_ITEMS_INSTAGRAM,
    _MAX_HASHTAGS_INSTAGRAM,
    _MAX_MENTIONS_INSTAGRAM,
    _VIDEO_MIMETYPES_INSTAGRAM,
    count_hashtags,
    count_mentions,
)

# Instagram takes images of up to 8 MB in the app; bigger ones are compressed
# hard, which is worth a warning, not a refusal.
_LARGE_IMAGE_INSTAGRAM = 8 * 1024 * 1024


class SocialPost(models.Model):
    """Plan Instagram publications without ever uploading them.

    Instagram is the only social media of the suite that is published by
    hand. When the date of a post comes, the Instagram publications are not
    sent anywhere: they wait in the state *To publish manually*, the
    responsible user has a to-do with the caption and the medias, and the
    publication joins its plan as soon as the user publishes it in the
    Instagram app, either detected automatically or marked by hand.
    """

    _inherit = "social.post"

    state = fields.Selection(
        selection_add=[("manual", "To Publish Manually"), ("published",)],
        ondelete={"manual": "set default"},
    )
    has_instagram = fields.Boolean(compute="_compute_has_instagram")
    instagram_post_format = fields.Selection(
        [
            ("feed", "Feed post / Carousel"),
            ("reel", "Reel"),
            ("story", "Story"),
        ],
        string="Instagram Format",
        default="feed",
        help="How the post will be published on Instagram. A feed post with "
        "several medias is a carousel.",
    )
    instagram_first_comment = fields.Text(
        string="First Comment",
        help="Text to publish as the first comment right after the post, "
        "typically the hashtags. It is copied from the to-do, nothing is "
        "published automatically.",
    )
    instagram_publish_notes = fields.Text(
        string="Publishing Notes",
        help="Internal notes for whoever publishes: music, location, "
        "collaborators, people to tag, alternative text...",
    )
    instagram_activity_id = fields.Many2one(
        "mail.activity", copy=False, ondelete="set null", readonly=True
    )
    instagram_pending_count = fields.Integer(compute="_compute_instagram_pending_count")

    @api.depends("account_ids.media_type")
    def _compute_has_instagram(self):
        for post in self:
            post.has_instagram = "instagram" in post.account_ids.mapped("media_type")

    @api.depends("post_account_ids.state")
    def _compute_instagram_pending_count(self):
        for post in self:
            post.instagram_pending_count = len(
                post.post_account_ids.filtered(lambda line: line.state == "manual")
            )

    @api.depends("state")
    def _compute_color(self):
        res = super()._compute_color()
        for post in self.filtered(lambda post: post.state == "manual"):
            post.color = 3
        return res

    @api.depends("state", "any_failed_post", "send_post", "message", "account_ids")
    def _compute_hide_post(self):
        res = super()._compute_hide_post()
        for post in self.filtered(lambda post: post.state == "manual"):
            post.hide_post = True
        return res

    def _default_account_ids(self):
        res = super()._default_account_ids()
        accounts = (
            self.env["social.account"]
            .with_company(self.env.company)
            .search([("media_type", "=", "instagram")])
        )
        return accounts.ids + [account_id for account_id in res if account_id not in accounts.ids]

    # ------------------------------------------------------------------
    # Rules of Instagram
    # ------------------------------------------------------------------

    def _instagram_check(self):
        """Return what Instagram refuses, and what it publishes differently.

        :return: the errors and the warnings.
        :rtype: tuple
        """
        self.ensure_one()
        errors, warnings = [], []
        message = self.message or ""
        images, videos = self.image_ids, self.video_ids
        medias = len(images) + len(videos)
        if len(message) > _MAX_CAPTION_LENGTH_INSTAGRAM:
            errors.append(
                self.env._(
                    "Instagram: the caption has %(length)s characters, at most "
                    "%(limit)s are allowed.",
                    length=len(message),
                    limit=_MAX_CAPTION_LENGTH_INSTAGRAM,
                )
            )
        for text, label in (
            (message, self.env._("caption")),
            (self.instagram_first_comment, self.env._("first comment")),
        ):
            if count_hashtags(text) > _MAX_HASHTAGS_INSTAGRAM:
                errors.append(
                    self.env._(
                        "Instagram: the %(label)s has %(count)s hashtags, at "
                        "most %(limit)s are allowed.",
                        label=label,
                        count=count_hashtags(text),
                        limit=_MAX_HASHTAGS_INSTAGRAM,
                    )
                )
            if count_mentions(text) > _MAX_MENTIONS_INSTAGRAM:
                errors.append(
                    self.env._(
                        "Instagram: the %(label)s mentions %(count)s accounts, "
                        "at most %(limit)s are allowed.",
                        label=label,
                        count=count_mentions(text),
                        limit=_MAX_MENTIONS_INSTAGRAM,
                    )
                )
        if not medias:
            errors.append(
                self.env._("Instagram: a post needs at least one image or video.")
            )
        post_format = self.instagram_post_format or "feed"
        if post_format == "feed" and medias > _MAX_CAROUSEL_ITEMS_INSTAGRAM:
            errors.append(
                self.env._(
                    "Instagram: a carousel holds at most %(limit)s images and "
                    "videos, this post has %(count)s.",
                    limit=_MAX_CAROUSEL_ITEMS_INSTAGRAM,
                    count=medias,
                )
            )
        if post_format == "reel" and (len(videos) != 1 or images):
            errors.append(
                self.env._("Instagram: a reel is exactly one video, without images.")
            )
        if post_format == "story" and medias > 1:
            errors.append(
                self.env._(
                    "Instagram: a story is a single image or video; create one "
                    "post per story."
                )
            )
        wrong_images = images.filtered(
            lambda att: (att.mimetype or "").lower() not in _IMAGE_MIMETYPES_INSTAGRAM
        )
        if wrong_images:
            warnings.append(
                self.env._(
                    "Instagram: %(names)s may not be accepted by the app, use "
                    "JPG or PNG.",
                    names=", ".join(wrong_images.mapped("name")),
                )
            )
        large_images = images.filtered(lambda att: att.file_size > _LARGE_IMAGE_INSTAGRAM)
        if large_images:
            warnings.append(
                self.env._(
                    "Instagram: %(names)s are larger than %(size)s and will be "
                    "compressed by the app.",
                    names=", ".join(large_images.mapped("name")),
                    size=human_size(_LARGE_IMAGE_INSTAGRAM),
                )
            )
        wrong_videos = videos.filtered(
            lambda att: (att.mimetype or "").lower() not in _VIDEO_MIMETYPES_INSTAGRAM
        )
        if wrong_videos:
            warnings.append(
                self.env._(
                    "Instagram: %(names)s may not be accepted by the app, use "
                    "MP4 or MOV.",
                    names=", ".join(wrong_videos.mapped("name")),
                )
            )
        return errors, warnings

    @api.depends(
        "account_ids",
        "message",
        "image_ids",
        "video_ids",
        "instagram_post_format",
        "instagram_first_comment",
    )
    def _compute_message_info(self):
        res = super()._compute_message_info()
        for post in self.filtered("has_instagram"):
            errors, warnings = post._instagram_check()
            lines = [post.message_info] if post.message_info else []
            lines += errors + warnings
            post.message_info = "\n".join(lines) or False
        return res

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        posts = super().create(vals_list)
        posts._instagram_sync_activity()
        return posts

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get("instagram_no_activity_sync") and any(
            key in vals
            for key in ("state", "send_post", "send_post_date", "account_ids", "user_id", "active")
        ):
            self._instagram_sync_activity()
        return res

    def _action_create_post_account(self):
        res = super()._action_create_post_account()
        self._instagram_update_state()
        return res

    def action_draft(self):
        """Take the publications waiting to be published by hand back too.

        Back in draft, the post is planned again from scratch: the next time
        it is due, its Instagram publications are checked and announced
        again.
        """
        self.post_account_ids.filtered(lambda line: line.state == "manual").write(
            {"state": "ready"}
        )
        return super().action_draft()

    def _instagram_update_state(self):
        """Set the state of the posts from their publications.

        The base only knows *published*, and *draft* when everything failed;
        a post whose remaining publications wait to be published by hand is
        neither, it is *To publish manually*, which also keeps it out of the
        cron that sends the planned posts. A post whose only failures are
        Instagram rules goes back to draft instead of being retried every
        five minutes: nothing changes until the user fixes it.
        """
        for post in self:
            lines = post.post_account_ids
            if not lines or post.state not in ("publishing", "manual"):
                continue
            pending = lines.filtered(
                lambda line: line.state not in ("posted", "manual", "deleted")
            )
            if pending:
                if all(
                    line.state == "failed" and line.media_type == "instagram"
                    for line in pending
                ):
                    post.state = "draft"
                continue
            if lines.filtered(lambda line: line.state == "manual"):
                post.state = "manual"
            else:
                post.write(
                    {
                        "state": "published",
                        "published_date": post.published_date
                        or max(lines.mapped("published_date") or [fields.Datetime.now()]),
                    }
                )
        self._instagram_sync_activity()

    def action_instagram_open_pending(self):
        """Open what is left to publish by hand on Instagram for this post."""
        self.ensure_one()
        lines = self.post_account_ids.filtered(lambda line: line.state == "manual")
        if len(lines) == 1:
            return lines.action_instagram_mark_published()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "social_media_instagram.social_post_account_instagram_pending_action"
        )
        action["domain"] = [("id", "in", lines.ids)]
        return action

    # ------------------------------------------------------------------
    # To-do of the responsible user
    # ------------------------------------------------------------------

    def _instagram_pending_accounts(self):
        """The Instagram accounts this post still has to be published on."""
        self.ensure_one()
        if self.state == "planned":
            return self.account_ids.filtered(
                lambda account: account.media_type == "instagram"
            )
        if self.state == "manual":
            return self.post_account_ids.filtered(
                lambda line: line.state == "manual"
            ).account_id
        return self.env["social.account"]

    def _instagram_deadline(self):
        """The day the post is due, in the time zone of its responsible."""
        self.ensure_one()
        moment = self.send_post_date or fields.Datetime.now()
        tz = pytz.timezone(self.user_id.tz or self.env.user.tz or "UTC")
        return pytz.utc.localize(moment).astimezone(tz).date()

    def _instagram_activity_note(self, accounts):
        self.ensure_one()
        when = (
            format_datetime(self.env, self.send_post_date, tz=self.user_id.tz)
            if self.send_post_date
            else self.env._("now")
        )
        note = Markup(
            "<p>%s</p><ul>%s</ul><p><b>%s</b></p><p>%s</p>"
        ) % (
            self.env._("Publish this post by hand in the Instagram app on %(when)s.", when=when),
            Markup("").join(
                Markup("<li>@%s</li>") % (account.username or account.name)
                for account in accounts
            ),
            self.env._("Caption"),
            html2plaintext(self.message or "")[:300],
        )
        if self.instagram_publish_notes:
            note += Markup("<p><b>%s</b><br/>%s</p>") % (
                self.env._("Notes"),
                self.instagram_publish_notes,
            )
        return note + Markup("<p><i>%s</i></p>") % self.env._(
            "Odoo links the publication by itself once it appears on the "
            "account, or use 'Mark as published' on the post."
        )

    def _instagram_sync_activity(self):
        """Keep one to-do per post for what is left to publish on Instagram.

        It is created as soon as the post is planned, so the publication shows
        up in the agenda of its responsible ahead of time, moved with the
        date, and closed when the last Instagram publication is linked.
        """
        todo = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        for post in self.with_context(instagram_no_activity_sync=True):
            activity = post.instagram_activity_id.exists()
            accounts = post._instagram_pending_accounts() if post.active else False
            if accounts:
                values = {
                    "date_deadline": post._instagram_deadline(),
                    "user_id": post.user_id.id,
                    "note": post._instagram_activity_note(accounts),
                }
                if activity:
                    activity.write(values)
                else:
                    post.instagram_activity_id = post.activity_schedule(
                        activity_type_id=todo.id if todo else False,
                        summary=self.env._("Publish on Instagram"),
                        **values,
                    )
                continue
            if not activity:
                continue
            published = post.post_account_ids.filtered(
                lambda line: line.media_type == "instagram" and line.state == "posted"
            )
            if published and post.state in ("published", "publishing"):
                activity.action_feedback(
                    feedback=self.env._("Published on Instagram.")
                )
            else:
                activity.unlink()
            post.instagram_activity_id = False
