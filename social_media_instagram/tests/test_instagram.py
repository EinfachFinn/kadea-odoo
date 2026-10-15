# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

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
        return 404, {"error": {"message": "Unsupported request", "code": 100}}

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
                SocialAccount,
                "_instagram_http",
                autospec=True,
                side_effect=self.fake,
            )
        )
        self.startPatcher(
            patch.object(
                SocialAccount, "_instagram_download", autospec=True, return_value=PIXEL
            )
        )

    def _post(self, **values):
        return self.env["social.post"].create(
            {
                "account_ids": [(6, 0, self.account.ids)],
                "message": "Autumn collection is here! #fashion",
                "image_ids": [(6, 0, self.image.ids)],
                "send_post": "schedule",
                **values,
            }
        )

    def _make_due(self, post):
        post.send_post_date = fields.Datetime.now() - timedelta(minutes=1)
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
        # Nothing at all was sent to Instagram, let alone published.
        self.assertFalse(self.fake.calls)
        # The cron does not pick the post up again.
        self.env["social.post"]._run_send_post()
        self.assertEqual(line.state, "manual")
        # The medias still belong to the post.
        self.assertNotEqual(self.image.res_model, "social.post.account")

    def test_published_by_hand_is_detected(self):
        post = self._post()
        self._make_due(post)
        activity = post.instagram_activity_id
        self.fake.medias = [self._published_media()]
        self.env["social.account"]._run_check_media_updates()
        line = post.post_account_ids
        self.assertEqual(line.state, "posted")
        self.assertEqual(line.remote_ref, "9001")
        self.assertEqual(line.post_account_url, "https://www.instagram.com/p/SC9001/")
        self.assertEqual(line.impression_count, 500)
        self.assertEqual(line.instagram_reach, 300)
        self.assertEqual(line.instagram_saved, 12)
        self.assertEqual(line.engagement, 20.0)
        self.assertEqual(post.state, "published")
        self.assertFalse(post.instagram_activity_id)
        self.assertFalse(activity.exists() and activity.active)
        self.assertNotIn("POST", self.fake.methods_called())

    def test_unknown_media_raises_notice_only(self):
        self.fake.medias = [self._published_media(caption="Something else")]
        self.env["social.account"]._run_check_media_updates()
        self.assertTrue(self.account.need_update)
        self.assertFalse(
            self.env["social.post.account"].search([("remote_ref", "=", "9001")])
        )

    def test_mark_published_wizard(self):
        post = self._post()
        self._make_due(post)
        line = post.post_account_ids
        self.fake.medias = [
            self._published_media("1", caption="Other"),
            self._published_media("2", caption="Changed caption in the app"),
        ]
        wizard = self.env["wizard.instagram.mark.published"].create(
            {"post_account_id": line.id, "permalink": "https://instagram.com/p/XX/"}
        )
        with self.assertRaises(UserError):
            wizard.action_confirm()
        wizard.permalink = "https://www.instagram.com/p/SC2/?igsh=abc"
        wizard.action_confirm()
        self.assertEqual(line.remote_ref, "2")
        self.assertEqual(post.state, "published")

    def test_rules_of_instagram(self):
        post = self._post(image_ids=[(5, 0, 0)], message="x" * 2300)
        self.assertIn("2300", post.message_info)
        self._make_due(post)
        self.assertEqual(post.post_account_ids.state, "failed")
        self.assertEqual(post.state, "draft")
        reel = self._post(instagram_post_format="reel")
        errors, __ = reel._instagram_check()
        self.assertTrue(errors)

    def test_back_to_draft_resets_manual_lines(self):
        post = self._post()
        self._make_due(post)
        post.action_draft()
        self.assertEqual(post.post_account_ids.state, "ready")
        self.assertFalse(post.instagram_activity_id)

    def test_sync_imports_and_totals(self):
        self.fake.medias = [
            self._published_media("1", caption="First"),
            self._published_media("2", caption="Second"),
        ]
        self.account._instagram_sync_media()
        lines = self.env["social.post.account"].search(
            [("account_id", "=", self.account.id)]
        )
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(lines.mapped("image_ids")))
        self.assertEqual(self.account.like_count, 80)
        self.assertEqual(self.account.impression_count, 1000)
        self.assertEqual(self.account.instagram_followers_count, 1200)
        # A publication gone from the complete listing is marked deleted.
        self.fake.medias = [self._published_media("1", caption="First")]
        self.account._instagram_sync_media()
        self.assertEqual(
            lines.filtered(lambda line: line.remote_ref == "2").state, "deleted"
        )

    def test_chart(self):
        end = fields.Datetime.now()
        start = end - timedelta(days=13)
        data = self.account._get_chart_account_statistics(start, end, "DAY")
        self.assertEqual(len(data), 1)
        chart = data[0]
        self.assertEqual(len(chart["labels"]), 14)
        self.assertEqual(len(chart["datasets"][0]["data"]), 14)
        self.assertEqual(chart["impressionCount"], 1400)
        self.assertEqual(chart["datasets"][5]["data"][0], 30.0)

    def test_token_refresh(self):
        self.account.write(
            {"expire_access_token_date": fields.Date.today() + timedelta(days=3)}
        )
        self.assertTrue(self.account._instagram_ensure_token())
        self.assertEqual(self.account.sudo().access_token, "refreshed")
        self.account.write(
            {"expire_access_token_date": fields.Date.today() - timedelta(days=1)}
        )
        self.assertFalse(self.account._instagram_ensure_token())
        self.assertTrue(self.account.instagram_token_expired)

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
