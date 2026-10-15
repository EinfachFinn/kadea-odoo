# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.social_media_instagram import uninstall_hook
from odoo.addons.social_media_instagram.models.social_account import SocialAccount

PIXEL = (
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
    b"hKmMIQAAAABJRU5ErkJggg=="
)


class FakeInstagram:
    """Answer the calls of the connector the way Instagram does."""

    def __init__(self):
        self.calls = []
        self.medias = []
        self.gone = set()
        self.token_refused = False
        self.profile = {
            "user_id": "17840000000000001",
            "id": "app-scoped-1",
            "username": "acme",
            "name": "ACME",
            "account_type": "BUSINESS",
            "followers_count": 1200,
            "follows_count": 10,
            "media_count": 3,
            "profile_picture_url": "https://cdn.example/p.jpg",
        }

    def __call__(self, model, method, url, params=None, data=None, timeout=20):
        params = params or {}
        self.calls.append((method, url, dict(params), dict(data or {})))
        if self.token_refused:
            return 400, {
                "error": {"message": "Error validating access token", "code": 190}
            }
        if url.endswith("/oauth/access_token"):
            return 200, {
                "data": [
                    {
                        "access_token": "short",
                        "user_id": 1,
                        "permissions": "instagram_business_basic,"
                        "instagram_business_manage_insights",
                    }
                ]
            }
        if url.endswith("/access_token"):
            return 200, {"access_token": "long", "expires_in": 5184000}
        if url.endswith("/refresh_access_token"):
            return 200, {"access_token": "refreshed", "expires_in": 5184000}
        if url.endswith("/me/insights"):
            return 200, {
                "data": [
                    {"name": "views", "total_value": {"value": 100}},
                    {"name": "reach", "total_value": {"value": 50}},
                    {"name": "likes", "total_value": {"value": 8}},
                    {"name": "comments", "total_value": {"value": 2}},
                    {"name": "shares", "total_value": {"value": 1}},
                    {"name": "saves", "total_value": {"value": 4}},
                    {"name": "total_interactions", "total_value": {"value": 15}},
                    {"name": "profile_links_taps", "total_value": {"value": 3}},
                ]
            }
        if url.endswith("/me/media"):
            return 200, {"data": list(self.medias)}
        if url.endswith("/me"):
            return 200, dict(self.profile)
        if url.endswith("/insights"):
            return 200, {
                "data": [
                    {"name": "views", "values": [{"value": 500}]},
                    {"name": "reach", "values": [{"value": 300}]},
                    {"name": "likes", "values": [{"value": 40}]},
                    {"name": "comments", "values": [{"value": 5}]},
                    {"name": "shares", "values": [{"value": 3}]},
                    {"name": "saved", "values": [{"value": 12}]},
                    {"name": "total_interactions", "values": [{"value": 60}]},
                ]
            }
        media_id = url.rsplit("/", 1)[-1]
        if media_id in self.gone:
            return 400, {
                "error": {
                    "message": "Unsupported get request.",
                    "code": 100,
                    "error_subcode": 33,
                }
            }
        return 200, {"id": media_id}

    def methods_called(self):
        return {method for method, *__ in self.calls}


@tagged("post_install", "-at_install")
class TestInstagram(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media = cls.env.ref("social_media_instagram.social_media_instagram")
        cls.account = cls.env["social.account"].create(
            {
                "name": "ACME",
                "username": "acme",
                "media_id": cls.media.id,
                "remote_ref": "17840000000000001",
                "access_token": "long",
                "expire_access_token_date": fields.Date.today() + timedelta(days=50),
                "instagram_token_date": fields.Datetime.now() - timedelta(days=10),
            }
        )
        cls.image = cls.env["ir.attachment"].create(
            {"name": "photo.jpg", "datas": PIXEL, "mimetype": "image/jpeg"}
        )

    def setUp(self):
        super().setUp()
        self.fake = FakeInstagram()
        self.startPatcher(
            patch.object(
                SocialAccount, "_instagram_http", autospec=True, side_effect=self.fake
            )
        )
        self.startPatcher(
            patch.object(
                SocialAccount, "_instagram_download", autospec=True, return_value=PIXEL
            )
        )

    def _linkedin_account(self):
        """A LinkedIn account, only where a test is about living with it.

        Kept out of the other tests: the crons of the base walk every
        account, and a LinkedIn one would try to reach LinkedIn.
        """
        return self.env["social.account"].create(
            {
                "name": "ACME LinkedIn",
                "media_id": self.env.ref("social_media_linkedin.social_media_linkedin").id,
                "remote_ref": "urn:li:organization:42",
                "access_token": "li-token",
                "expire_access_token_date": fields.Date.today() + timedelta(days=50),
            }
        )

    def _post(self, accounts=None, **values):
        return self.env["social.post"].create(
            {
                "account_ids": [(6, 0, (accounts or self.account).ids)],
                "message": "Autumn collection is here! #fashion",
                "image_ids": [(6, 0, self.image.ids)],
                "send_post": "schedule",
                **values,
            }
        )

    def _make_due(self, post):
        # The base refuses a schedule date behind the clock, so the date is
        # moved where only the cron would see it.
        past = fields.Datetime.now() - timedelta(minutes=1)
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE social_post SET send_post_date = %s WHERE id = %s", (past, post.id)
        )
        post.invalidate_recordset(["send_post_date"])
        self.env["social.post"]._run_send_post()

    def _published_media(self, media_id="9001", caption=None):
        return {
            "id": media_id,
            "caption": caption or "Autumn collection is here!  #fashion #new",
            "media_type": "IMAGE",
            "media_product_type": "FEED",
            "permalink": f"https://www.instagram.com/p/SC{media_id}/",
            "shortcode": f"SC{media_id}",
            "timestamp": fields.Datetime.now().strftime("%Y-%m-%dT%H:%M:%S+0000"),
            "like_count": 40,
            "comments_count": 5,
            "media_url": "https://cdn.example/m.jpg",
        }

    def test_open_account_wizard(self):
        action = self.media.action_open_account()
        self.assertEqual(action["res_model"], "wizard.social.account")
        self.assertEqual(action["context"]["default_media_id"], self.media.id)

    def test_planned_post_gets_a_todo(self):
        post = self._post()
        self.assertEqual(post.state, "planned")
        activity = post.instagram_activity_id
        self.assertTrue(activity)
        self.assertEqual(activity.date_deadline, post._instagram_deadline())
        post.write({"send_post_date": fields.Datetime.now() + timedelta(days=3)})
        self.assertEqual(activity.date_deadline, post._instagram_deadline())
        post.action_cancel()
        self.assertFalse(activity.exists())

    def test_due_post_is_not_uploaded(self):
        post = self._post()
        self._make_due(post)
        line = post.post_account_ids
        self.assertEqual(line.state, "manual")
        self.assertEqual(post.state, "manual")
        self.assertEqual(line.image_ids, self.image)
        self.assertTrue(post.instagram_activity_id)
        self.assertTrue(post.hide_post)
        self.assertFalse(self.fake.calls, "Nothing may be sent to Instagram")
        # Out of the reach of the cron, so it is not sent again.
        self.env["social.post"]._run_send_post()
        self.assertEqual(line.state, "manual")
        self.assertEqual(post.state, "manual")

    def test_mixed_linkedin_and_instagram(self):
        linkedin_account = self._linkedin_account()
        post = self._post(accounts=self.account | linkedin_account)
        with patch(
            "odoo.addons.social_media_linkedin.models.social_account."
            "SocialAccount._linkedin_create_post",
            return_value=("urn:li:share:1", {}),
        ):
            self._make_due(post)
        lines = post.post_account_ids
        self.assertEqual(
            lines.filtered(lambda line: line.media_type == "linkedin").state, "posted"
        )
        instagram = lines.filtered(lambda line: line.media_type == "instagram")
        self.assertEqual(instagram.state, "manual")
        self.assertEqual(post.state, "manual")
        activity = post.instagram_activity_id
        self.assertTrue(activity)
        self.fake.medias = [self._published_media("2", caption="Edited in the app")]
        wizard = self.env["wizard.instagram.mark.published"].create(
            {
                "post_account_id": instagram.id,
                "permalink": "https://www.instagram.com/p/SC2/?igsh=abc",
            }
        )
        wizard.action_confirm()
        self.assertEqual(instagram.state, "posted")
        self.assertEqual(instagram.remote_ref, "2")
        self.assertEqual(instagram.impression_count, 500)
        self.assertEqual(post.state, "published")
        self.assertFalse(post.instagram_activity_id)
        self.assertFalse(activity.exists() and activity.active)

    def test_mark_published_wizard_rejects_foreign_link(self):
        post = self._post()
        self._make_due(post)
        self.fake.medias = [self._published_media("1", caption="Other")]
        wizard = self.env["wizard.instagram.mark.published"].create(
            {
                "post_account_id": post.post_account_ids.id,
                "permalink": "https://instagram.com/p/XX/",
            }
        )
        with self.assertRaises(UserError):
            wizard.action_confirm()

    def test_published_by_hand_is_detected(self):
        post = self._post()
        self._make_due(post)
        self.fake.medias = [self._published_media()]
        self.env["social.account"]._run_check_media_updates()
        line = post.post_account_ids
        self.assertEqual(line.state, "posted")
        self.assertEqual(line.remote_ref, "9001")
        self.assertEqual(line.post_account_url, "https://www.instagram.com/p/SC9001/")
        self.assertEqual(line.impression_count, 500)
        self.assertEqual(line.instagram_reach, 300)
        self.assertEqual(line.instagram_saved, 12)
        self.assertEqual(line.engagement, 0.2)
        self.assertTrue(line.statistics_date)
        self.assertEqual(post.state, "published")
        self.assertFalse(post.instagram_activity_id)
        self.assertNotIn("POST", self.fake.methods_called())
        rows = self.env["social.account.statistics"].search(
            [("account_id", "=", self.account.id)]
        )
        self.assertTrue(rows, "The check also rewrites the last days")

    def test_unknown_media_is_imported(self):
        self.fake.medias = [self._published_media(caption="Something else")]
        self.env["social.account"]._run_check_media_updates()
        line = self.env["social.post.account"].search([("remote_ref", "=", "9001")])
        self.assertEqual(line.account_id, self.account)
        self.assertEqual(line.message, "Something else")
        self.assertTrue(line.image_ids)
        self.assertEqual(line.like_count, 40)
        self.assertEqual(self.account.instagram_followers_count, 1200)
        self.assertFalse(self.account.need_update)

    def test_missing_media_is_checked_before_deletion(self):
        self.fake.medias = [
            self._published_media("1", caption="First"),
            self._published_media("2", caption="Second"),
        ]
        self.account._instagram_import_media()
        second = self.env["social.post.account"].search([("remote_ref", "=", "2")])
        self.fake.medias = [self._published_media("1", caption="First")]
        self.account._instagram_import_media()
        self.assertEqual(second.state, "posted", "Missing is not proof")
        self.fake.gone.add("2")
        self.account._instagram_import_media()
        self.assertEqual(second.state, "deleted")

    def test_rules_of_instagram(self):
        post = self._post(image_ids=[(5, 0, 0)], message="x" * 2300)
        self.assertIn("2300", post.message_error)
        self.assertIn("image or a video", post.message_error)
        with mute_logger("odoo.addons.social_media_base.models.social_post_account"):
            self._make_due(post)
        self.assertEqual(post.post_account_ids.state, "failed")
        self.assertEqual(post.state, "draft")
        reel = self._post(instagram_post_format="reel")
        self.assertTrue(reel._get_post_errors("instagram"))
        self.assertFalse(reel._get_post_errors("linkedin"))
        gif = self.env["ir.attachment"].create(
            {"name": "anim.gif", "datas": PIXEL, "mimetype": "image/gif"}
        )
        warned = self._post(image_ids=[(6, 0, gif.ids)])
        self.assertFalse(warned.message_error)
        self.assertIn("anim.gif", warned.message_info)

    def test_back_to_draft_resets_manual_lines(self):
        post = self._post()
        self._make_due(post)
        post.action_draft()
        self.assertEqual(post.post_account_ids.state, "ready")
        self.assertFalse(post.instagram_activity_id)

    def test_snapshot_writes_daily_rows(self):
        today = fields.Date.today()
        self.account._snapshot_statistics(today - timedelta(days=2), today)
        rows = self.env["social.account.statistics"].search(
            [("account_id", "=", self.account.id)]
        )
        self.assertEqual(len(rows), 3)
        row = rows[0]
        self.assertEqual(row.impression_count, 100)
        self.assertEqual(row.click_count, 3)
        self.assertEqual(row.like_count, 8)
        self.assertEqual(row.instagram_reach, 50)
        self.assertEqual(row.instagram_saves, 4)
        self.assertEqual(row.engagement, 0.3)
        # The card of the account is derived from the series by the base.
        self.account.compute_dashboard_statistics()
        self.assertEqual(self.account.impression_count, 300)
        # The backfill is skipped once the series reaches past the window.
        self.account._backfill_statistics()
        self.assertTrue(self.account._instagram_statistics_backfilled())

    def test_refresh_post_statistics(self):
        line = self.env["social.post.account"].create(
            {
                "account_id": self.account.id,
                "message": "Imported",
                "remote_ref": "77",
                "state": "posted",
                "published_date": fields.Datetime.now(),
            }
        )
        answered = self.env["social.account"]._run_refresh_post_statistics()
        self.assertIn(line, answered)
        self.assertEqual(line.share_count, 3)
        self.assertEqual(line.impression_count, 500)

    def test_token_refresh_and_expiry(self):
        self.account.write(
            {"expire_access_token_date": fields.Date.today() + timedelta(days=3)}
        )
        self.account.with_context(not_notify=True).validate_access_token()
        self.assertEqual(self.account.sudo().access_token, "refreshed")
        # A token Instagram refuses flags the account, the way the base does.
        self.fake.token_refused = True
        self.env["social.account"]._run_check_media_updates()
        self.assertTrue(self.account.need_update)
        with self.assertRaises(UserError):
            self.account.action_instagram_check_connection()
        # An expired token is flagged from the stored dates, without a call.
        self.account._clear_credentials_flag()
        self.fake.token_refused = False
        self.account.write(
            {"expire_access_token_date": fields.Date.today() - timedelta(days=1)}
        )
        calls = len(self.fake.calls)
        self.env["social.account"]._run_check_media_updates()
        self.assertTrue(self.account.need_update)
        self.assertEqual(len(self.fake.calls), calls)

    def test_associate_account(self):
        wizard = self.env["wizard.social.account"].create(
            {
                "media_id": self.media.id,
                "instagram_app_id": "123",
                "instagram_app_secret": "s3cr3t",
            }
        )
        action = wizard.action_associate_social_account()
        self.assertIn("instagram.com/oauth/authorize", action["url"])
        self.assertNotIn("content_publish", action["url"])
        token, scopes = self.env["social.account"]._instagram_exchange_code(
            "code", wizard._get_url_redirect(), "123", "s3cr3t"
        )
        self.assertEqual(token["access_token"], "long")
        account = self.env["social.account"]._create_account_instagram(
            wizard, token, scopes
        )
        # Same Instagram user: the existing account is reused.
        self.assertEqual(account, self.account)
        self.assertEqual(account.instagram_followers_count, 1200)
        self.assertIn("instagram_business_basic", account.instagram_granted_scopes)

    def test_update_requires_same_instagram_account(self):
        other = self.account.copy({"remote_ref": "999", "username": "other"})
        wizard = self.env["wizard.social.account"].create(
            {"media_id": self.media.id, "account_id": other.id}
        )
        with self.assertRaises(UserError):
            self.env["social.account"]._create_account_instagram(
                wizard, {"access_token": "long"}
            )

    def test_delete_post_account_only_in_odoo(self):
        line = self.env["social.post.account"].create(
            {
                "account_id": self.account.id,
                "message": "Imported",
                "remote_ref": "77",
                "state": "posted",
            }
        )
        res = line.action_delete_post_account()
        self.assertEqual(res["params"]["type"], "warning")
        self.assertIn("Odoo only", res["params"]["message"])
        self.assertFalse(line.exists())
        self.assertNotIn("DELETE", self.fake.methods_called())

    def test_uninstall_hook(self):
        linkedin_account = self._linkedin_account()
        uninstall_hook(self.env)
        self.assertFalse(self.account.active)
        self.assertFalse(self.account.sudo().access_token)
        self.assertTrue(linkedin_account.active)
        self.assertEqual(linkedin_account.sudo().access_token, "li-token")
