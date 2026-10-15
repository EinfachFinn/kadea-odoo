# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import requests
from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.image import image_process

from odoo.addons.social_media_base.exceptions import SocialCredentialsError

from ..social_instagram_utils import (
    _ACCOUNT_METRICS_FALLBACK_INSTAGRAM,
    _ACCOUNT_METRICS_INSTAGRAM,
    _GRAPH_VERSION_INSTAGRAM,
    _INSIGHTS_MAX_AGE_DAYS_INSTAGRAM,
    _MEDIA_CHECK_LIMIT_INSTAGRAM,
    _MEDIA_FIELDS_INSTAGRAM,
    _MEDIA_METRICS_FALLBACK_INSTAGRAM,
    _MEDIA_METRICS_INSTAGRAM,
    _MEDIA_PAGE_SIZE_INSTAGRAM,
    _PARAM_GRAPH_VERSION_INSTAGRAM,
    _PROFILE_FIELDS_INSTAGRAM,
    _STATISTICS_HISTORY_DAYS_INSTAGRAM,
    _STATISTICS_REFRESH_DAYS_INSTAGRAM,
    _STATISTICS_WORKERS_INSTAGRAM,
    _TOKEN_MARGIN_DAYS_INSTAGRAM,
    _URL_API_INSTAGRAM,
    _URL_GRAPH_INSTAGRAM,
    _URL_WEB_INSTAGRAM,
    captions_match,
    day_timestamp,
    engagement_rate,
    parse_instagram_datetime,
)

_logger = logging.getLogger(__name__)

# Error codes and subcodes Instagram answers for a token it no longer accepts.
# ``OAuthException`` alone means nothing: Instagram also answers it, with code
# 100, for a metric a publication does not report.
_TOKEN_ERROR_CODES = (102, 190)
_TOKEN_ERROR_SUBCODES = (458, 459, 460, 463, 464, 467)


class InstagramApiError(UserError):
    """What Instagram answered when it refused a call."""

    def __init__(self, message, code=None, subcode=None):
        super().__init__(message)
        self.code = code
        self.subcode = subcode


class InstagramCredentialsError(SocialCredentialsError):
    """Instagram refused the token: only a new authorization fixes it.

    Raised as the ``SocialCredentialsError`` of the base, which is what makes
    the check for updates flag the account instead of retrying it.
    """

    def __init__(self, message, code=None, subcode=None):
        super().__init__(message)
        self.code = code
        self.subcode = subcode


class SocialAccount(models.Model):
    """Instagram implementation of the social account API calls.

    Instagram is read, never written: nothing here publishes. What it costs
    is bounded the way the base asks for. The check for updates reads the
    latest publications in one call, the daily series costs one call per day
    of the window asked for, and the figures of a publication one call per
    publication inside the window of the base.
    """

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
    instagram_granted_scopes = fields.Char(string="Granted Permissions", readonly=True)
    instagram_account_type = fields.Char(string="Account Type", readonly=True)
    instagram_followers_count = fields.Integer(
        string="Instagram Followers", readonly=True
    )
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
        return {
            **super()._fields_account_url(),
            "instagram": f"{_URL_WEB_INSTAGRAM}/{self.username}/" if self.username else "",
        }

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
        threads that read the daily series.

        :rtype: tuple
        """
        try:
            response = requests.request(
                method, url, params=params, data=data, timeout=timeout
            )
        except requests.exceptions.RequestException as ex:
            raise InstagramApiError(f"Error connecting to Instagram: {ex}") from ex
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return response.status_code, payload

    @api.model
    def _instagram_raise_for_payload(self, status, payload):
        """Raise what Instagram answered, telling the token errors apart.

        Kept free of database access, translations included, because it runs
        in the threads of :meth:`_instagram_day_statistics` too.
        """
        error = payload.get("error") if isinstance(payload, dict) else None
        if status < 400 and not error:
            return
        if isinstance(error, dict):
            message = error.get("error_user_msg") or error.get("message") or str(error)
            code, subcode = error.get("code"), error.get("error_subcode")
        else:
            message = (
                payload.get("error_message")
                or (error if isinstance(error, str) else "")
                or f"Instagram answered with the HTTP status {status}."
            )
            code, subcode = payload.get("code"), None
        if code in _TOKEN_ERROR_CODES or subcode in _TOKEN_ERROR_SUBCODES:
            raise InstagramCredentialsError(message, code=code, subcode=subcode)
        raise InstagramApiError(message, code=code, subcode=subcode)

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
        :raise InstagramCredentialsError: when Instagram refused the token.
        :raise InstagramApiError: when Instagram refused anything else.
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

        A new account goes through ``_associate_account`` of the base, which
        finds, checks, reactivates or creates it. An account being updated is
        the one of the wizard, and the user has to have logged in on
        Instagram as that very account: otherwise the credentials of one
        account would end up on another.

        :param wizard: the association wizard that started the flow.
        :param token: the long-lived token answer.
        :rtype: recordset of ``social.account``
        """
        access_token = token.get("access_token")
        profile = self._instagram_get_profile(token=access_token)
        remote_ref = str(profile.get("user_id") or profile.get("id") or "")
        if not remote_ref:
            raise UserError(
                self.env._("Instagram did not answer the account identifier.")
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
        target = wizard.account_id.sudo()
        if target:
            if target.remote_ref and target.remote_ref != remote_ref:
                raise UserError(
                    self.env._(
                        "You logged in on Instagram as @%(username)s, but this "
                        "account is @%(account)s. Log out of Instagram in the "
                        "browser and update the account again.",
                        username=profile.get("username"),
                        account=target.username,
                    )
                )
            target._check_can_associate()
            if not target.active:
                values["active"] = True
            target.write(values)
            account = target
        else:
            account = self._associate_account(
                "instagram",
                remote_ref,
                values,
                username=profile.get("username"),
                create_values={"media_id": wizard.media_id.id},
            )
        account._clear_credentials_flag()
        account._on_account_associated()
        return account

    def _on_account_associated(self):
        """Import the latest publications before the base adds them up.

        The base refreshes and backfills the daily series and derives the
        card of the account. The publications are read first, so the
        fallback of the card (the counters of the publications) already has
        something to add up, and a post planned before the account was linked
        again finds the publication made by hand in the meantime.
        """
        for account in self.filtered(lambda acc: acc.media_type == "instagram"):
            with account._account_guard(on_error=account._instagram_notify_failure):
                account._instagram_import_media()
        return super()._on_account_associated()

    # ------------------------------------------------------------------
    # Token
    # ------------------------------------------------------------------

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

    def _instagram_token_due(self):
        """Whether the token should be renewed now, from the stored dates.

        Instagram only refreshes a token that is at least one day old.
        """
        self.ensure_one()
        expire = self.expire_access_token_date
        if not expire:
            return False
        issued = self.instagram_token_date
        old_enough = not issued or issued <= fields.Datetime.now() - timedelta(days=1)
        margin = fields.Date.today() + timedelta(days=_TOKEN_MARGIN_DAYS_INSTAGRAM)
        return old_enough and expire <= margin

    def _instagram_validate_access_token(self):
        """Renew the token when it is due, and check it when asked to.

        Cheap when the token is still good: the stored dates answer and no
        call is made, as the base expects from a check that runs before
        every operation. ``check_remote_token`` is the deliberate check of
        the account form, which asks Instagram for the profile.

        :raise SocialCredentialsError: when only a new authorization helps.
        """
        self.ensure_one()
        if not self.sudo().access_token:
            raise InstagramCredentialsError(
                self.env._("The account has no Instagram access token.")
            )
        expire = self.expire_access_token_date
        if expire and expire < fields.Date.today():
            raise InstagramCredentialsError(
                self.env._("The Instagram access token expired.")
            )
        if self._instagram_token_due():
            try:
                self._instagram_refresh_token()
            except InstagramApiError as error:
                # The token is still valid for a few days: a refusal that is
                # not about the token is retried on the next run.
                _logger.warning(
                    "Could not refresh the Instagram token of account %s: %s",
                    self.id,
                    error,
                )
        if self.env.context.get("check_remote_token"):
            self.sudo().write(
                {
                    **self._instagram_profile_values(self._instagram_get_profile()),
                    "last_update_account": fields.Datetime.now(),
                }
            )
            if not self.env.context.get("not_notify"):
                self._notify_user_client(
                    notif_type="social_form_success",
                    notif_message=self.env._(
                        "The token is valid, connected as @%(username)s.",
                        username=self.username,
                    ),
                    media="instagram",
                    account_name=self.name,
                )

    def validate_access_token(self):
        res = super().validate_access_token()
        for account in self.filtered(lambda acc: acc.media_type == "instagram"):
            account._instagram_validate_access_token()
        return res

    def _refresh_credentials(self):
        """Renew the token without the user, which Instagram allows."""
        res = super()._refresh_credentials()
        if self.media_type != "instagram":
            return res
        expire = self.expire_access_token_date
        if not self.sudo().access_token or (expire and expire < fields.Date.today()):
            return False
        try:
            self._instagram_refresh_token()
        except UserError:
            _logger.exception(
                "Could not renew the Instagram token of the account %s", self.id
            )
            return False
        return True

    def action_instagram_check_connection(self):
        """Ask Instagram for the profile, from the account form.

        A token Instagram refuses flags the account right away, the way the
        check for updates would on its next run.
        """
        self.ensure_one()
        try:
            self.with_context(check_remote_token=True).validate_access_token()
        except SocialCredentialsError as error:
            self._flag_credentials_expired(str(error))
            raise UserError(
                self.env._(
                    "Instagram refused the authorization of this account: "
                    "%(error)s. Use 'Update account' to authorize it again.",
                    error=error,
                )
            ) from error
        return True

    # ------------------------------------------------------------------
    # Publications
    # ------------------------------------------------------------------

    def _instagram_fetch_media(self, limit=_MEDIA_CHECK_LIMIT_INSTAGRAM):
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

    @api.model
    def _instagram_media_values(self, media):
        """Return what the media list says about one publication.

        Only what the list answers for free: the figures that need insights
        are written by :meth:`_refresh_post_statistics`.
        """
        media_type = media.get("media_type") or False
        product = media.get("media_product_type") or False
        values = {
            "remote_ref": media["id"],
            "post_account_url": media.get("permalink") or False,
            "instagram_media_type": media_type,
            "instagram_media_product_type": product,
            "instagram_shortcode": media.get("shortcode") or False,
            "has_video": media_type == "VIDEO" or product == "REELS",
            "like_count": media.get("like_count") or 0,
            "comment_count": media.get("comments_count") or 0,
            "state": "posted",
            "failed_description": False,
        }
        published = parse_instagram_datetime(media.get("timestamp"))
        if published:
            values["published_date"] = published
        return values

    def _instagram_media_images(self, media):
        """Return the commands storing a copy of the picture of a publication."""
        url = (
            media.get("thumbnail_url")
            if media.get("media_type") == "VIDEO"
            else media.get("media_url")
        )
        datas = self._instagram_download(url)
        if not datas or not self._instagram_valid_image(datas):
            return []
        return [
            (
                0,
                0,
                {
                    "name": f"instagram_{media['id']}.jpg",
                    "type": "binary",
                    "mimetype": "image/jpeg",
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
                line.post_id.send_post_date
                or line.post_id.create_date
                or line.create_date
            )
            if published and planned_on and published < planned_on - timedelta(days=2):
                continue
            if captions_match(line.message, media.get("caption")):
                return line
        return pending.browse()

    def _instagram_import_media(self, limit=_MEDIA_CHECK_LIMIT_INSTAGRAM):
        """Read the latest publications and bring them into Odoo.

        A publication already known is updated. An unknown one is first
        matched against the publications waiting to be published by hand,
        which is how a post published in the Instagram app joins its plan;
        whatever is left is imported as a publication of its own. The figures
        of what was touched inside the window of the base are read right
        after, and the publications missing from the listing are only marked
        as deleted once Instagram confirms it, as the base asks.

        :return: the publications written.
        :rtype: recordset of ``social.post.account``
        """
        self.ensure_one()
        self.sudo().write(
            self._instagram_profile_values(
                self._instagram_get_profile(), with_picture=False
            )
        )
        medias = [media for media in self._instagram_fetch_media(limit) if media.get("id")]
        PostAccount = self.env["social.post.account"].sudo().with_context(active_test=False)
        refs = [media["id"] for media in medias]
        known = {
            line.remote_ref: line
            for line in PostAccount.search(
                [("account_id", "=", self.id), ("remote_ref", "in", refs)]
            )
        }
        pending = PostAccount.search(
            [("account_id", "=", self.id), ("state", "=", "manual")], order="id"
        )
        lines = PostAccount
        matched = PostAccount
        for media in medias:
            values = self._instagram_media_values(media)
            line = known.get(media["id"])
            if not line and pending:
                line = self._instagram_match_pending(pending, media)
                pending -= line
                matched |= line
            if line:
                line.write(values)
            else:
                line = PostAccount.create(
                    {
                        **values,
                        "account_id": self.id,
                        "message": media.get("caption") or "-",
                        "image_ids": self._instagram_media_images(media),
                    }
                )
            lines |= line
        self._refresh_post_statistics(
            lines.filtered_domain(self._statistics_window_domain())
        )
        matched._instagram_log_published()
        matched.post_id._instagram_after_manual_publication()
        self._instagram_check_missing(medias, complete=len(medias) < limit)
        return lines

    def _instagram_check_missing(self, medias, complete):
        """Ask about the publications the listing did not answer.

        Missing from a listing is not proof of a deletion, so the suspects go
        through ``_register_remote_posts_gone`` of the base, which asks
        Instagram about each of them. With an incomplete listing, only the
        publications at least as recent as the oldest one read are suspects.
        """
        self.ensure_one()
        domain = [
            ("account_id", "=", self.id),
            ("state", "=", "posted"),
            ("remote_ref", "!=", False),
            ("remote_ref", "not in", [media["id"] for media in medias]),
        ]
        if not complete:
            dates = [parse_instagram_datetime(m.get("timestamp")) for m in medias]
            dates = [value for value in dates if value]
            if not dates:
                return
            domain.append(("published_date", ">=", min(dates)))
        suspects = self.env["social.post.account"].sudo().search(domain)
        if suspects:
            suspects._register_remote_posts_gone()

    def _instagram_media_insights(self, remote_ref, product=None):
        """Return the insights of one publication by metric name.

        A publication answers a different set of metrics depending on its
        kind, and Instagram refuses the whole call for a metric it does not
        answer, so a smaller set is asked for before giving up.

        :rtype: dict or None
        """
        self.ensure_one()
        metrics = _MEDIA_METRICS_INSTAGRAM.get(
            product or "", _MEDIA_METRICS_INSTAGRAM["DEFAULT"]
        )
        for metric_set in (metrics, _MEDIA_METRICS_FALLBACK_INSTAGRAM):
            try:
                payload = self._request_instagram(
                    f"/{remote_ref}/insights", params={"metric": metric_set}
                )
            except InstagramApiError as error:
                _logger.info(
                    "Instagram refused the insights %s of media %s: %s",
                    metric_set,
                    remote_ref,
                    error,
                )
                continue
            return {
                item.get("name"): self._instagram_metric_value(item)
                for item in payload.get("data", [])
            }
        return None

    @api.model
    def _instagram_insights_values(self, insights, line):
        """Return the figures of one publication as the fields that hold them."""
        likes = insights.get("likes", line.like_count)
        comments = insights.get("comments", line.comment_count)
        shares = insights.get("shares", 0)
        saved = insights.get("saved", 0)
        reach = insights.get("reach", 0)
        interactions = insights.get("total_interactions") or (
            likes + comments + shares + saved
        )
        return {
            "like_count": likes,
            "comment_count": comments,
            "share_count": shares,
            "impression_count": insights.get("views", 0),
            "instagram_reach": reach,
            "instagram_saved": saved,
            "engagement": engagement_rate(interactions, reach),
        }

    def _refresh_post_statistics(self, post_accounts):
        """Read the figures Instagram reports for these publications.

        One call per publication: Instagram has no batch endpoint for the
        insights of media. The lines arrive already chosen by the caller, so
        what is spent is bounded by the window of the base. A publication
        Instagram answered nothing for keeps the figures it had and is left
        out of the answer, since its silence is not a figure of zero.
        """
        instagram = post_accounts.filtered(
            lambda line: line.media_type == "instagram" and line.remote_ref
        )
        refreshed = super()._refresh_post_statistics(post_accounts - instagram)
        for account, lines in instagram.grouped("account_id").items():
            with account._account_guard(on_error=account._instagram_notify_failure):
                refreshed |= account._instagram_write_post_statistics(lines)
        return refreshed

    def _instagram_write_post_statistics(self, post_accounts):
        self.ensure_one()
        answered = post_accounts.browse()
        read_on = fields.Datetime.now()
        for line in post_accounts:
            insights = self._instagram_media_insights(
                line.remote_ref, line.instagram_media_product_type
            )
            if insights is None:
                continue
            line.sudo().write(
                {
                    **self._instagram_insights_values(insights, line),
                    "statistics_date": read_on,
                }
            )
            answered |= line
        return answered

    # ------------------------------------------------------------------
    # Daily series
    # ------------------------------------------------------------------

    def _instagram_fetch_day_totals(self, url, token, since, until):
        """Return the account totals Instagram reports between two instants.

        Runs in the threads of :meth:`_instagram_day_statistics`, so it only
        does HTTP and never touches the database.

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
                _logger.info("Instagram refused the account insights: %s", error)
                continue
            return {
                item.get("name"): self._instagram_metric_value(item)
                for item in payload.get("data", [])
            }
        return None

    @api.model
    def _instagram_statistics_values(self, totals):
        """Return the totals of one day as the fields of the daily series.

        The taps on the links of the profile are the clicks Instagram
        reports, the views its impressions, and the engagement is the ratio
        of the interactions over the accounts reached.
        """
        interactions = totals.get("total_interactions") or sum(
            totals.get(name, 0) for name in ("likes", "comments", "shares", "saves")
        )
        return {
            "click_count": totals.get("profile_links_taps", 0),
            "like_count": totals.get("likes", 0),
            "comment_count": totals.get("comments", 0),
            "share_count": totals.get("shares", 0),
            "impression_count": totals.get("views", 0),
            "engagement": engagement_rate(interactions, totals.get("reach", 0)),
            "instagram_reach": totals.get("reach", 0),
            "instagram_saves": totals.get("saves", 0),
        }

    def _instagram_day_statistics(self, date_from, date_to):
        """Ask Instagram for the totals of every day of the range.

        One call per day, in parallel: Instagram answers a total over the
        range it is asked for and no series of days for these metrics. The
        days it cannot answer (too old, still to come) are not asked.

        :return: the values of the series, keyed by day.
        :rtype: dict
        """
        self.ensure_one()
        today = fields.Date.today()
        first = max(fields.Date.to_date(date_from), today - timedelta(days=_INSIGHTS_MAX_AGE_DAYS_INSTAGRAM))
        last = min(fields.Date.to_date(date_to), today)
        if first > last:
            return {}
        url = self._instagram_url("/me/insights")
        token = self.sudo().access_token
        now_ts = int(fields.Datetime.now().timestamp())
        days = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]

        def fetch(day):
            until = min(day_timestamp(day + timedelta(days=1)), now_ts)
            return day, self._instagram_fetch_day_totals(
                url, token, day_timestamp(day), until
            )

        with ThreadPoolExecutor(max_workers=_STATISTICS_WORKERS_INSTAGRAM) as executor:
            answers = list(executor.map(fetch, days))
        return {
            day: self._instagram_statistics_values(totals)
            for day, totals in answers
            if totals is not None
        }

    def _instagram_snapshot_accounts(self, date_from, date_to):
        """Write the daily series of these accounts, each one on its own.

        The token is checked first (cheap: dates only), and each account goes
        in the guard of the base, so one Instagram refuses neither rolls back
        nor stops the others; its responsible user is told why.
        """
        for account in self:
            with account._account_guard(on_error=account._instagram_notify_failure):
                account.with_context(not_notify=True)._instagram_validate_access_token()
                account._write_statistics_rows(
                    account._instagram_day_statistics(date_from, date_to)
                )

    def _snapshot_statistics(self, date_from, date_to):
        instagram = self.filtered(lambda account: account.media_type == "instagram")
        instagram._instagram_snapshot_accounts(date_from, date_to)
        return super(SocialAccount, self - instagram)._snapshot_statistics(
            date_from, date_to
        )

    @api.model
    def _instagram_refresh_window(self):
        date_to = fields.Date.today()
        return date_to - timedelta(days=_STATISTICS_REFRESH_DAYS_INSTAGRAM), date_to

    @api.model
    def _instagram_backfill_window(self):
        date_to = fields.Date.today()
        return date_to - timedelta(days=_STATISTICS_HISTORY_DAYS_INSTAGRAM), date_to

    def _refresh_statistics(self):
        instagram = self.filtered(lambda account: account.media_type == "instagram")
        if instagram:
            instagram._instagram_snapshot_accounts(*self._instagram_refresh_window())
        return (
            bool(instagram)
            or super(SocialAccount, self - instagram)._refresh_statistics()
        )

    def _instagram_statistics_backfilled(self):
        """Whether the series already reaches past the refresh window.

        The refresh only writes the last days, so an older row can only come
        from a backfill that already ran: the series is its own answer.
        """
        self.ensure_one()
        return bool(
            self.env["social.account.statistics"]
            .sudo()
            .search_count(
                [
                    ("account_id", "=", self.id),
                    ("date", "<", self._instagram_refresh_window()[0]),
                ],
                limit=1,
            )
        )

    def _backfill_statistics(self, force=False):
        instagram = self.filtered(lambda account: account.media_type == "instagram")
        pending = (
            instagram
            if force
            else instagram.filtered(
                lambda account: not account._instagram_statistics_backfilled()
            )
        )
        if pending:
            pending._instagram_snapshot_accounts(*self._instagram_backfill_window())
        return super(SocialAccount, self - instagram)._backfill_statistics(force=force)

    # ------------------------------------------------------------------
    # Crons and actions
    # ------------------------------------------------------------------

    def _run_check_media_updates(self):
        """Import the latest publications and rewrite the last days.

        The base renews the credentials of every account first and flags the
        ones Instagram refused; those are left out here, since asking them
        would only spend a call to learn what the flag already says. Each
        account is read in its own guard.
        """
        update = super()._run_check_media_updates()
        accounts = self.sudo().search(
            self._get_check_media_updates_domain()
            + [("media_type", "=", "instagram"), ("need_update", "=", False)]
        )
        for account in accounts:
            with account._account_guard(on_error=account._instagram_notify_failure):
                account._instagram_import_media()
        accounts._refresh_statistics()
        return update

    def _instagram_notify_failure(self, error):
        """Tell the responsible user why the account could not be read."""
        self.ensure_one()
        _logger.warning(
            "Error reading the Instagram account %s: %s", self.id, error
        )
        if isinstance(error, SocialCredentialsError):
            self._flag_credentials_expired(str(error))
            return
        self._notify_user(
            target=self.user_id.partner_id,
            notif_type="social_kanban_danger",
            notif_message=str(error),
            media="instagram",
            account_name=self.name,
        )

    def action_instagram_import(self):
        """Import the latest publications now, from the account form."""
        self.ensure_one()
        self.with_context(not_notify=True)._instagram_validate_access_token()
        self._instagram_import_media()
        self._refresh_account_statistics()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": self.env._("The Instagram publications were imported."),
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

    def _instagram_notify_due(self, lines):
        """Ping the responsible users of the publications that are now due."""
        for account in lines.account_id:
            count = len(lines.filtered(lambda line, acc=account: line.account_id == acc))
            account._notify_user_client(
                target=account.user_id.partner_id,
                notif_type="social_kanban_info",
                notif_message=Markup("%s")
                % self.env._(
                    "%(count)s publication(s) are due on @%(username)s. Publish "
                    "them in the Instagram app.",
                    count=count,
                    username=account.username or account.name,
                ),
                media="instagram",
                account_name=account.name,
            )
