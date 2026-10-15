# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import itertools
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import requests
from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.image import image_process

from ..social_instagram_utils import (
    _ACCOUNT_METRICS_FALLBACK_INSTAGRAM,
    _ACCOUNT_METRICS_INSTAGRAM,
    _CHART_WORKERS_INSTAGRAM,
    _GRAPH_VERSION_INSTAGRAM,
    _INSIGHTS_MAX_AGE_DAYS_INSTAGRAM,
    _INSIGHTS_WINDOW_DAYS_INSTAGRAM,
    _MEDIA_CHECK_LIMIT_INSTAGRAM,
    _MEDIA_FIELDS_INSTAGRAM,
    _MEDIA_METRICS_FALLBACK_INSTAGRAM,
    _MEDIA_METRICS_INSTAGRAM,
    _MEDIA_PAGE_SIZE_INSTAGRAM,
    _MEDIA_SYNC_LIMIT_INSTAGRAM,
    _PARAM_GRAPH_VERSION_INSTAGRAM,
    _PROFILE_FIELDS_INSTAGRAM,
    _TOKEN_MARGIN_DAYS_INSTAGRAM,
    _URL_API_INSTAGRAM,
    _URL_GRAPH_INSTAGRAM,
    _URL_WEB_INSTAGRAM,
    captions_match,
    chart_buckets,
    day_timestamp,
    engagement_rate,
    parse_instagram_datetime,
    split_range,
)

_logger = logging.getLogger(__name__)


class InstagramApiError(UserError):
    """What Instagram answered when it refused a call.

    ``is_token_error`` tells apart the refusals a new authorization fixes
    (an expired or revoked token) from everything else.
    """

    def __init__(self, message, code=None, subcode=None, error_type=None):
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        self.error_type = error_type

    @property
    def is_token_error(self):
        # ``OAuthException`` alone means nothing: Instagram also answers it,
        # with code 100, for a metric a publication does not report.
        return self.code in (102, 190) or self.subcode in (458, 459, 460, 463, 464, 467)


class SocialAccount(models.Model):
    """Instagram implementation of the social account API calls."""

    _inherit = "social.account"

    instagram_app_id = fields.Char(string="Instagram App ID", groups="base.group_system")
    instagram_app_secret = fields.Char(
        string="Instagram App Secret", groups="base.group_system"
    )
    instagram_token_date = fields.Datetime(
        string="Token Issued On",
        readonly=True,
        help="When the long-lived token was obtained or refreshed. Instagram "
        "only refreshes a token that is at least 24 hours old.",
    )
    instagram_token_expired = fields.Boolean(
        string="Authorization Expired",
        readonly=True,
        help="Instagram refused the token of this account, or it expired "
        "without being refreshed. Update the account to authorize it again.",
    )
    instagram_granted_scopes = fields.Char(string="Granted Permissions", readonly=True)
    instagram_account_type = fields.Char(string="Account Type", readonly=True)
    instagram_followers_count = fields.Integer(string="Instagram Followers", readonly=True)
    instagram_follows_count = fields.Integer(string="Following", readonly=True)
    instagram_media_count = fields.Integer(string="Publications", readonly=True)
    instagram_pending_count = fields.Integer(
        string="To Publish Manually",
        compute="_compute_instagram_pending_count",
    )

    def _compute_instagram_pending_count(self):
        counts = dict(
            self.env["social.post.account"]._read_group(
                [("account_id", "in", self.ids), ("state", "=", "manual")],
                ["account_id"],
                ["__count"],
            )
        )
        for account in self:
            account.instagram_pending_count = counts.get(account, 0)

    def _fields_account_url(self):
        res = super()._fields_account_url()
        if self.media_type == "instagram" and self.username:
            res.append(("instagram", f"{_URL_WEB_INSTAGRAM}/{self.username}/"))
        return res

    def _get_removal_values(self):
        values = super()._get_removal_values()
        values.update({"instagram_app_id": False, "instagram_app_secret": False})
        return values

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    @api.model
    def _instagram_graph_version(self):
        return (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(_PARAM_GRAPH_VERSION_INSTAGRAM, _GRAPH_VERSION_INSTAGRAM)
        )

    @api.model
    def _instagram_url(self, path, versioned=True, host=_URL_GRAPH_INSTAGRAM):
        if versioned:
            return f"{host}/{self._instagram_graph_version()}{path}"
        return f"{host}{path}"

    @api.model
    def _instagram_http(self, method, url, params=None, data=None, timeout=20):
        """Send one request to Instagram and return its status and JSON body.

        The single place that talks to the network, which is also what the
        tests replace. It never touches the database, so it can run in the
        threads that build a chart.

        :rtype: tuple
        """
        try:
            response = requests.request(
                method, url, params=params, data=data, timeout=timeout
            )
        except requests.exceptions.RequestException as ex:
            raise InstagramApiError(
                self.env._("Error connecting to Instagram: %(error)s", error=ex)
            ) from ex
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return response.status_code, payload

    @api.model
    def _instagram_raise_for_payload(self, status, payload):
        error = payload.get("error") if isinstance(payload, dict) else None
        if status < 400 and not error:
            return
        if isinstance(error, dict):
            raise InstagramApiError(
                error.get("error_user_msg") or error.get("message") or str(error),
                code=error.get("code"),
                subcode=error.get("error_subcode"),
                error_type=error.get("type"),
            )
        raise InstagramApiError(
            payload.get("error_message")
            or (error if isinstance(error, str) else "")
            or self.env._("Instagram answered with the HTTP status %(status)s.", status=status),
            code=payload.get("code"),
            error_type=payload.get("error_type"),
        )

    def _request_instagram(
        self,
        path,
        method="GET",
        params=None,
        data=None,
        token=None,
        versioned=True,
        host=_URL_GRAPH_INSTAGRAM,
    ):
        """Call the Instagram API and return the JSON answer.

        :param token: the token to send, the one of the account when ``None``
            and none at all when ``False``.
        :raise InstagramApiError: when Instagram refused the call.
        """
        params = dict(params or {})
        if token is None:
            self.ensure_one()
            token = self.sudo().access_token
        if token:
            params["access_token"] = token
        status, payload = self._instagram_http(
            method,
            self._instagram_url(path, versioned=versioned, host=host),
            params=params,
            data=data,
        )
        self._instagram_raise_for_payload(status, payload)
        return payload

    def _request_instagram_next(self, url):
        """Follow a ``paging.next`` link, which already carries the token."""
        status, payload = self._instagram_http("GET", url)
        self._instagram_raise_for_payload(status, payload)
        return payload

    @api.model
    def _instagram_download(self, url):
        """Download a picture of the Instagram CDN as base64, or ``False``.

        The addresses Instagram answers expire after a while, so what is
        shown in Odoo has to be a copy.
        """
        if not url:
            return False
        try:
            response = requests.get(url, timeout=15)
        except requests.exceptions.RequestException:
            _logger.warning("Could not download the Instagram picture %s", url)
            return False
        if response.status_code != 200 or not response.content:
            return False
        return base64.b64encode(response.content)

    @api.model
    def _instagram_valid_image(self, datas):
        """Whether Odoo can decode a downloaded picture, so it can be stored."""
        try:
            image_process(base64.b64decode(datas))
        except (UserError, ValueError, TypeError):
            return False
        return True

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    @api.model
    def _instagram_exchange_code(self, code, redirect_uri, app_id, app_secret):
        """Exchange the authorization code for a long-lived token.

        The code first gives a token valid for one hour, which is exchanged
        right away for one valid for 60 days.

        :return: the long-lived token answer and the granted permissions.
        :rtype: tuple
        """
        payload = self._request_instagram(
            "/oauth/access_token",
            method="POST",
            token=False,
            versioned=False,
            host=_URL_API_INSTAGRAM,
            data={
                "client_id": app_id,
                "client_secret": app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        # Instagram has answered both a flat object and a ``data`` list.
        if isinstance(payload.get("data"), list) and payload["data"]:
            payload = payload["data"][0]
        short_token = payload.get("access_token")
        if not short_token:
            raise UserError(self.env._("Instagram answered without an access token."))
        permissions = payload.get("permissions") or []
        if isinstance(permissions, str):
            permissions = permissions.split(",")
        long_lived = self._request_instagram(
            "/access_token",
            token=short_token,
            versioned=False,
            params={"grant_type": "ig_exchange_token", "client_secret": app_secret},
        )
        return long_lived, ", ".join(sorted(p.strip() for p in permissions if p))

    @api.model
    def _instagram_token_values(self, token):
        """Return the credential fields of a token answer of Instagram."""
        now = fields.Datetime.now()
        expires_in = token.get("expires_in") or 60 * 86400
        return {
            "access_token": token.get("access_token"),
            "expire_access_token_date": (now + timedelta(seconds=expires_in)).date(),
            "instagram_token_date": now,
            "instagram_token_expired": False,
        }

    def _instagram_get_profile(self, token=None):
        return self._request_instagram(
            "/me", token=token, params={"fields": _PROFILE_FIELDS_INSTAGRAM}
        )

    @api.model
    def _instagram_profile_values(self, profile, with_picture=True):
        values = {
            "name": profile.get("name") or profile.get("username"),
            "username": profile.get("username"),
            "instagram_account_type": profile.get("account_type"),
            "instagram_followers_count": profile.get("followers_count", 0),
            "instagram_follows_count": profile.get("follows_count", 0),
            "instagram_media_count": profile.get("media_count", 0),
        }
        if with_picture:
            picture = self._instagram_download(profile.get("profile_picture_url"))
            if picture and self._instagram_valid_image(picture):
                values["image_1920"] = picture
        return values

    @api.model
    def _create_account_instagram(self, wizard, token, granted_scopes=""):
        """Create or update the account the user just authorized.

        An account already linked to the same Instagram user is reused, and
        reactivated if it was archived; whether the current user may take it
        over is decided by ``_check_can_associate``.

        :param wizard: the association wizard that started the flow.
        :param token: the long-lived token answer.
        :rtype: recordset of ``social.account``
        """
        access_token = token.get("access_token")
        profile = self._instagram_get_profile(token=access_token)
        remote_ref = str(profile.get("user_id") or profile.get("id") or "")
        if not remote_ref:
            raise UserError(self.env._("Instagram did not answer the account identifier."))
        target = wizard.account_id.sudo()
        if target and target.remote_ref and target.remote_ref != remote_ref:
            raise UserError(
                self.env._(
                    "You logged in on Instagram as @%(username)s, but this "
                    "account is @%(account)s. Log out of Instagram in the "
                    "browser and update the account again.",
                    username=profile.get("username"),
                    account=target.username,
                )
            )
        account = target or self._find_account_to_associate(
            "instagram", remote_ref, username=profile.get("username")
        )
        values = {
            **self._instagram_profile_values(profile),
            **self._instagram_token_values(token),
            "remote_ref": remote_ref,
            "instagram_app_id": wizard.instagram_app_id,
            "instagram_app_secret": wizard.instagram_app_secret,
            "instagram_granted_scopes": granted_scopes,
            "last_update_account": fields.Datetime.now(),
        }
        if account:
            account._check_can_associate()
            if not account.active:
                values["active"] = True
            account.sudo().write(values)
        else:
            values.update(
                {
                    "media_id": wizard.media_id.id,
                    "user_id": self.env.user.id,
                }
            )
            account = self.sudo().create(values)
        account._trigger_initial_sync()
        return account

    def _instagram_refresh_token(self):
        """Renew the long-lived token for 60 more days."""
        self.ensure_one()
        payload = self._request_instagram(
            "/refresh_access_token",
            versioned=False,
            params={"grant_type": "ig_refresh_token"},
        )
        self.sudo().write(self._instagram_token_values(payload))
        return payload

    def _instagram_flag_token_expired(self, reason):
        """Tell the responsible user the account has to be authorized again."""
        self.ensure_one()
        if self.instagram_token_expired:
            return
        self.sudo().write({"instagram_token_expired": True})
        self.message_post(
            body=self.env._(
                "The Instagram authorization of this account is no longer "
                "valid: %(reason)s. Use 'Update account' to authorize it "
                "again; until then no statistics are read.",
                reason=reason,
            ),
            partner_ids=self.user_id.partner_id.ids,
        )

    def _instagram_ensure_token(self):
        """Refresh the token when it is about to expire.

        Cheap when the token is still good: the stored dates answer, no call
        is made. Instagram only refreshes a token at least one day old and not
        yet expired, so an expired one asks the user to authorize again.

        :return: whether the account can be used.
        :rtype: bool
        """
        self.ensure_one()
        if not self.sudo().access_token:
            self._instagram_flag_token_expired(self.env._("there is no access token"))
            return False
        today = fields.Date.today()
        expire = self.expire_access_token_date
        if expire and expire < today:
            self._instagram_flag_token_expired(self.env._("the token expired"))
            return False
        issued = self.instagram_token_date
        old_enough = not issued or issued <= fields.Datetime.now() - timedelta(days=1)
        if (
            expire
            and expire <= today + timedelta(days=_TOKEN_MARGIN_DAYS_INSTAGRAM)
            and old_enough
        ):
            try:
                self._instagram_refresh_token()
            except InstagramApiError as error:
                _logger.warning(
                    "Could not refresh the Instagram token of account %s: %s",
                    self.id,
                    error,
                )
                if error.is_token_error or expire <= today:
                    self._instagram_flag_token_expired(str(error))
                    return False
        return True

    def validate_access_token(self):
        res = super().validate_access_token()
        for account in self.filtered(lambda acc: acc.media_type == "instagram"):
            valid = account._instagram_ensure_token()
            if valid and not self.env.context.get("not_notify"):
                account._notify_user_client(
                    notif_type="social_form_success",
                    notif_message=self.env._("The token is valid."),
                    media="instagram",
                    account_name=account.name,
                )
        return res

    def action_instagram_check_connection(self):
        """Ask Instagram for the profile, from the account form."""
        self.ensure_one()
        if not self._instagram_ensure_token():
            raise UserError(
                self.env._(
                    "The Instagram authorization of this account expired. Use "
                    "'Update account' to authorize it again."
                )
            )
        try:
            profile = self._instagram_get_profile()
        except InstagramApiError as error:
            if error.is_token_error:
                self._instagram_flag_token_expired(str(error))
            raise
        self.sudo().write(
            {
                **self._instagram_profile_values(profile),
                "last_update_account": fields.Datetime.now(),
            }
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": self.env._(
                    "Connected to Instagram as @%(username)s.",
                    username=self.username,
                ),
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    # ------------------------------------------------------------------
    # Publications and their statistics
    # ------------------------------------------------------------------

    def _instagram_fetch_media(self, limit=_MEDIA_SYNC_LIMIT_INSTAGRAM):
        """Return the latest publications of the account, newest first."""
        self.ensure_one()
        payload = self._request_instagram(
            "/me/media",
            params={
                "fields": _MEDIA_FIELDS_INSTAGRAM,
                "limit": min(_MEDIA_PAGE_SIZE_INSTAGRAM, limit),
            },
        )
        medias = []
        while True:
            medias += payload.get("data", [])
            next_url = payload.get("paging", {}).get("next")
            if len(medias) >= limit or not next_url:
                break
            payload = self._request_instagram_next(next_url)
        return medias[:limit]

    @api.model
    def _instagram_metric_value(self, item):
        values = item.get("values") or []
        if values:
            return values[0].get("value") or 0
        return (item.get("total_value") or {}).get("value") or 0

    def _instagram_media_insights(self, media):
        """Return the insights of one publication by metric name.

        A publication answers a different set of metrics depending on its
        kind, and Instagram refuses the whole call for a metric it does not
        answer, so a smaller set is asked for before giving up. An empty
        answer keeps the counters the media list already gave.

        :rtype: dict
        """
        self.ensure_one()
        product = media.get("media_product_type") or ""
        metrics = _MEDIA_METRICS_INSTAGRAM.get(
            product, _MEDIA_METRICS_INSTAGRAM["DEFAULT"]
        )
        for metric_set in (metrics, _MEDIA_METRICS_FALLBACK_INSTAGRAM):
            try:
                payload = self._request_instagram(
                    f"/{media['id']}/insights", params={"metric": metric_set}
                )
            except InstagramApiError as error:
                if error.is_token_error:
                    raise
                _logger.info(
                    "Instagram refused the insights %s of media %s: %s",
                    metric_set,
                    media.get("id"),
                    error,
                )
                continue
            return {
                item.get("name"): self._instagram_metric_value(item)
                for item in payload.get("data", [])
            }
        return {}

    @api.model
    def _instagram_media_values(self, media, insights=None):
        """Return the ``social.post.account`` values of one publication.

        The counters that need insights are only written when insights were
        read, so a publication out of the insights window keeps the figures
        it was last given instead of dropping to zero.
        """
        insights = insights or {}
        media_type = media.get("media_type") or False
        product = media.get("media_product_type") or False
        likes = insights.get("likes", media.get("like_count") or 0)
        comments = insights.get("comments", media.get("comments_count") or 0)
        values = {
            "remote_ref": media["id"],
            "post_account_url": media.get("permalink") or False,
            "instagram_media_type": media_type,
            "instagram_media_product_type": product,
            "instagram_shortcode": media.get("shortcode") or False,
            "has_video": media_type == "VIDEO" or product == "REELS",
            "like_count": likes,
            "comment_count": comments,
            "state": "posted",
            "published": True,
        }
        published = parse_instagram_datetime(media.get("timestamp"))
        if published:
            values["published_date"] = published
        if insights:
            shares = insights.get("shares", 0)
            saved = insights.get("saved", 0)
            reach = insights.get("reach", 0)
            interactions = insights.get("total_interactions") or (
                likes + comments + shares + saved
            )
            values.update(
                {
                    "share_count": shares,
                    "impression_count": insights.get("views", 0),
                    "instagram_reach": reach,
                    "instagram_saved": saved,
                    "engagement": engagement_rate(interactions, reach),
                    "instagram_statistics_date": fields.Datetime.now(),
                }
            )
        return values

    def _instagram_media_images(self, media):
        """Return the commands storing a copy of the picture of a publication."""
        url = (
            media.get("thumbnail_url")
            if media.get("media_type") == "VIDEO"
            else media.get("media_url")
        )
        name = f"instagram_{media['id']}.jpg"
        PostAccount = self.env["social.post.account"]
        if not url or PostAccount._get_medias_account([name]):
            return []
        datas = self._instagram_download(url)
        if not datas or not self._instagram_valid_image(datas):
            return []
        return [
            (
                0,
                0,
                {
                    "name": name,
                    "type": "binary",
                    "mimetype": "image/jpeg",
                    "res_model": PostAccount._name,
                    "datas": datas,
                },
            )
        ]

    def _instagram_match_pending(self, pending, media):
        """Return the planned publication a published media corresponds to.

        The user published by hand, so nothing ties the media to the plan but
        its caption and its date: the caption has to start the same way, and
        the media cannot be older than the plan by more than two days.
        """
        published = parse_instagram_datetime(media.get("timestamp"))
        for line in pending:
            planned_on = (
                line.post_id.send_post_date or line.post_id.create_date or line.create_date
            )
            if published and planned_on and published < planned_on - timedelta(days=2):
                continue
            if captions_match(line.message, media.get("caption")):
                return line
        return pending.browse()

    def _instagram_store_media(self, medias, create_unknown=True):
        """Write what Instagram answered about these publications.

        A publication already known is updated. An unknown one is first
        matched against the publications waiting to be published by hand,
        which is how a post published in the Instagram app joins its plan;
        whatever is left is imported as a publication of its own, like the
        LinkedIn connector imports what was published outside of Odoo.

        :return: the medias that matched nothing and were not imported.
        :rtype: list
        """
        self.ensure_one()
        PostAccount = self.env["social.post.account"].with_context(active_test=False)
        refs = [media["id"] for media in medias if media.get("id")]
        known = {
            line.remote_ref: line
            for line in PostAccount.search(
                [("account_id", "=", self.id), ("remote_ref", "in", refs)]
            )
        }
        pending = PostAccount.search(
            [("account_id", "=", self.id), ("state", "=", "manual")], order="id"
        )
        insights_limit = fields.Datetime.now() - timedelta(
            days=_INSIGHTS_WINDOW_DAYS_INSTAGRAM
        )
        posts = self.env["social.post"]
        unknown = []
        for media in medias:
            if not media.get("id"):
                continue
            line = known.get(media["id"])
            matched = False
            if not line and pending:
                line = self._instagram_match_pending(pending, media)
                pending -= line
                matched = bool(line)
            if not line and not create_unknown:
                unknown.append(media)
                continue
            published = parse_instagram_datetime(media.get("timestamp"))
            insights = (
                self._instagram_media_insights(media)
                if published and published >= insights_limit
                else {}
            )
            values = self._instagram_media_values(media, insights)
            if line:
                line.write(values)
                posts |= line.post_id
                if matched:
                    line._instagram_log_published()
            else:
                values.update(
                    {
                        "account_id": self.id,
                        "message": media.get("caption") or "-",
                        "image_ids": self._instagram_media_images(media),
                    }
                )
                PostAccount.create(values)
        posts._instagram_update_state()
        return unknown

    def _instagram_mark_deleted(self, medias, complete):
        """Mark the publications that are gone from Instagram.

        Only what the listing could have answered is judged: with an
        incomplete listing, a publication older than the oldest one read is
        simply not known about.
        """
        self.ensure_one()
        refs = [media["id"] for media in medias if media.get("id")]
        domain = [
            ("account_id", "=", self.id),
            ("state", "=", "posted"),
            ("remote_ref", "!=", False),
            ("remote_ref", "not in", refs),
        ]
        if not complete:
            dates = [
                parse_instagram_datetime(media.get("timestamp")) for media in medias
            ]
            dates = [value for value in dates if value]
            if not dates:
                return
            domain.append(("published_date", ">=", min(dates)))
        self.env["social.post.account"].search(domain).write(
            {"state": "deleted", "post_account_url": False}
        )

    def _instagram_update_totals(self):
        """Add up the figures of the publications on the account card."""
        self.ensure_one()
        lines = self.env["social.post.account"].search(
            [("account_id", "=", self.id), ("state", "=", "posted")]
        )
        with_reach = lines.filtered("instagram_reach")
        self.sudo().write(
            {
                "like_count": sum(lines.mapped("like_count")),
                "comment_count": sum(lines.mapped("comment_count")),
                "share_count": sum(lines.mapped("share_count")),
                "impression_count": int(sum(lines.mapped("impression_count"))),
                "engagement": (
                    round(sum(with_reach.mapped("engagement")) / len(with_reach), 2)
                    if with_reach
                    else 0
                ),
            }
        )

    def _instagram_sync_media(self, remote_ref=None):
        """Read the publications of the account and their statistics."""
        self.ensure_one()
        if not self._instagram_ensure_token():
            return False
        try:
            self.sudo().write(
                self._instagram_profile_values(
                    self._instagram_get_profile(), with_picture=False
                )
            )
            if remote_ref:
                medias = [
                    self._request_instagram(
                        f"/{remote_ref}", params={"fields": _MEDIA_FIELDS_INSTAGRAM}
                    )
                ]
            else:
                medias = self._instagram_fetch_media()
        except InstagramApiError as error:
            if error.is_token_error:
                self._instagram_flag_token_expired(str(error))
            raise
        self._instagram_store_media(medias)
        if not remote_ref:
            self._instagram_mark_deleted(
                medias, complete=len(medias) < _MEDIA_SYNC_LIMIT_INSTAGRAM
            )
        self._instagram_update_totals()
        self.sudo().write(
            {"need_update": False, "last_update_account": fields.Datetime.now()}
        )
        return True

    def _instagram_accounts(self):
        """The Instagram accounts a dashboard call is about, or ``None``."""
        if not self:
            return self.search([("media_type", "=", "instagram")])
        accounts = self.filtered(lambda account: account.media_type == "instagram")
        return accounts or None

    def _update_posts_statistics(self, post_id, domain):
        statistics = super()._update_posts_statistics(post_id, domain)
        accounts = self._instagram_accounts()
        if accounts is None:
            return statistics
        for account in accounts:
            try:
                with self.env.cr.savepoint():
                    account._instagram_sync_media(remote_ref=post_id or None)
            except Exception as error:  # noqa: BLE001 - one account must not stop the rest
                _logger.exception(
                    "Error updating the Instagram account %s", account.id
                )
                account._notify_user_client(
                    target=account.user_id.partner_id,
                    notif_type="social_kanban_danger",
                    notif_message=str(error),
                    media="instagram",
                    account_name=account.name,
                )
        return self._instagram_account_statistics(statistics)

    def _instagram_account_statistics(self, statistics=None):
        data = self.search_read(
            [("media_type", "=", "instagram")],
            [
                "name",
                "company_id",
                "media_id",
                "account_url",
                "impression_count",
                "interactions_count",
                "engagement",
                "need_update",
            ],
        )
        return list(itertools.chain(statistics or [], data))

    def action_instagram_refresh(self):
        """Read the publications and statistics now, from the account form."""
        self.ensure_one()
        self._instagram_sync_media()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": self.env._("The Instagram statistics were updated."),
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_instagram_open_pending(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "social_media_instagram.social_post_account_instagram_pending_action"
        )
        action["domain"] = [("account_id", "=", self.id), ("state", "=", "manual")]
        return action

    # ------------------------------------------------------------------
    # Charts
    # ------------------------------------------------------------------

    def _instagram_fetch_account_totals(self, url, token, since, until):
        """Return the account totals Instagram reports between two instants.

        Runs in the threads of :meth:`_instagram_bucket_statistics`, so it
        only does HTTP and never touches the database.

        :rtype: dict
        """
        for metrics in (_ACCOUNT_METRICS_INSTAGRAM, _ACCOUNT_METRICS_FALLBACK_INSTAGRAM):
            status, payload = self._instagram_http(
                "GET",
                url,
                params={
                    "metric": metrics,
                    "metric_type": "total_value",
                    "period": "day",
                    "since": since,
                    "until": until,
                    "access_token": token,
                },
            )
            try:
                self._instagram_raise_for_payload(status, payload)
            except InstagramApiError as error:
                if error.is_token_error:
                    raise
                _logger.info("Instagram refused the account insights: %s", error)
                continue
            return {
                item.get("name"): self._instagram_metric_value(item)
                for item in payload.get("data", [])
            }
        return {}

    def _instagram_bucket_statistics(self, buckets):
        """Return the six figures the chart draws, one tuple per bucket.

        The figures are the ones ``_map_chart_statistics`` reads: clicks
        (taps on the profile links), likes, comments, shares, engagement rate
        and views. A bucket wider than what Instagram answers at once is asked
        in several ranges and added up, which over-counts the reach of the
        accounts seen in both a little; the views and interactions are exact.
        """
        self.ensure_one()
        token = self.sudo().access_token
        url = self._instagram_url("/me/insights")
        today = fields.Date.today()
        oldest = today - timedelta(days=_INSIGHTS_MAX_AGE_DAYS_INSTAGRAM)
        now_ts = int(fields.Datetime.now().timestamp())
        jobs = []
        for index, (first, last) in enumerate(buckets):
            first, last = max(first, oldest), min(last, today)
            if first > last:
                continue
            for chunk_from, chunk_to in split_range(first, last):
                jobs.append(
                    (
                        index,
                        day_timestamp(chunk_from),
                        min(day_timestamp(chunk_to + timedelta(days=1)), now_ts),
                    )
                )
        totals = [{} for __ in buckets]

        def fetch(job):
            return job[0], self._instagram_fetch_account_totals(
                url, token, job[1], job[2]
            )

        if jobs:
            with ThreadPoolExecutor(max_workers=_CHART_WORKERS_INSTAGRAM) as executor:
                for index, values in executor.map(fetch, jobs):
                    for name, value in values.items():
                        totals[index][name] = totals[index].get(name, 0) + (value or 0)
        statistics = []
        for values in totals:
            interactions = values.get("total_interactions") or sum(
                values.get(name, 0) for name in ("likes", "comments", "shares", "saves")
            )
            statistics.append(
                (
                    values.get("profile_links_taps", 0),
                    values.get("likes", 0),
                    values.get("comments", 0),
                    values.get("shares", 0),
                    engagement_rate(interactions, values.get("reach", 0)),
                    values.get("views", 0),
                )
            )
        return statistics

    def _get_chart_account_statistics(
        self, start_date=None, end_date=None, granularity="WEEK"
    ):
        data = super()._get_chart_account_statistics(start_date, end_date, granularity)
        accounts = self._instagram_accounts()
        if not accounts:
            return data
        freq = {"DAY": "D", "MONTH": "ME"}.get(granularity, "W-MON")
        data_instagram = []
        for account in accounts:
            start, end = account._get_default_filter_date(start_date, end_date)
            try:
                if not account._instagram_ensure_token():
                    continue
                statistics = account._instagram_bucket_statistics(
                    chart_buckets(start, end, freq)
                )
            except InstagramApiError as error:
                _logger.warning(
                    "Could not read the Instagram insights of account %s: %s",
                    account.id,
                    error,
                )
                account._notify_user_client(
                    notif_type="social_kanban_danger",
                    notif_message=str(error),
                    media="instagram",
                    account_name=account.name,
                )
                continue
            if statistics:
                data_instagram += account._map_chart_statistics(
                    statistics, freq=freq, start_date=start, end_date=end
                )
        return list(itertools.chain(data, data_instagram))

    # ------------------------------------------------------------------
    # Crons
    # ------------------------------------------------------------------

    def _instagram_check_updates(self):
        """Look at the latest publications for what was published by hand.

        A planned publication found there is linked to its plan right away,
        with its statistics. Anything else that is new only raises the
        notice of the dashboard, like the LinkedIn connector does.

        :return: whether something new is left to synchronize.
        :rtype: bool
        """
        self.ensure_one()
        medias = self._instagram_fetch_media(limit=_MEDIA_CHECK_LIMIT_INSTAGRAM)
        unknown = self._instagram_store_media(medias, create_unknown=False)
        if unknown and not self.need_update:
            self.sudo().write({"need_update": True})
            self.env["bus.bus"]._sendone(
                self.user_id.partner_id,
                "social_need_update",
                {"need_update": True},
            )
        return bool(unknown)

    def _run_check_media_updates(self):
        update = super()._run_check_media_updates()
        accounts = self.sudo().search(
            [("media_type", "=", "instagram"), ("instagram_token_expired", "=", False)]
        )
        for account in accounts:
            try:
                with self.env.cr.savepoint():
                    if account._instagram_ensure_token():
                        update = account._instagram_check_updates() or update
            except InstagramApiError as error:
                if error.is_token_error:
                    account._instagram_flag_token_expired(str(error))
                _logger.warning(
                    "Error checking the Instagram account %s: %s", account.id, error
                )
            except Exception:  # noqa: BLE001 - one account must not stop the rest
                _logger.exception("Error checking the Instagram account %s", account.id)
        return update

    @api.model
    def _cron_instagram_refresh_statistics(self):
        """Refresh the publications and statistics of every Instagram account."""
        accounts = self.sudo().search(
            [("media_type", "=", "instagram"), ("instagram_token_expired", "=", False)]
        )
        for account in accounts:
            try:
                with self.env.cr.savepoint():
                    account._instagram_sync_media()
            except Exception:  # noqa: BLE001 - one account must not stop the rest
                _logger.exception(
                    "Error refreshing the Instagram account %s", account.id
                )

    def _instagram_notify_due(self, lines):
        """Ping the responsible users of the publications that are now due."""
        for account in lines.account_id:
            account._notify_user_client(
                target=account.user_id.partner_id,
                notif_type="social_kanban_info",
                notif_message=Markup(
                    self.env._(
                        "%(count)s publication(s) are due on @%(username)s. "
                        "Publish them in the Instagram app."
                    )
                )
                % {
                    "count": len(lines.filtered(lambda line, a=account: line.account_id == a)),
                    "username": account.username or account.name,
                },
                media="instagram",
                account_name=account.name,
            )
