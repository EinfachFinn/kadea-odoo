# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import request, route

_logger = logging.getLogger(__name__)


class SocialMediaInstagram(http.Controller):
    """Endpoint Instagram redirects to at the end of the authorization."""

    @route(["/social_instagram/callback"], type="http", auth="user")
    def social_instagram_callback(
        self, code=None, state=None, error=None, error_description=None, **kwargs
    ):
        # Everything reached from here answers through a redirect, so the
        # notifications go to the session instead of the bus. See
        # ``social.media.base.mixin._notify_user``.
        SocialAccount = request.env["social.account"].with_context(
            social_media_oauth_callback=True
        )
        # The wizard is looked up by its state token and its creator, so a
        # state leaking out of the authorization URL is useless elsewhere.
        wizard = request.env["wizard.social.account"]
        if state:
            wizard = wizard.sudo().search(
                [
                    ("csrf_state_token", "=", state),
                    ("create_uid", "=", request.env.user.id),
                ],
                limit=1,
            )
        try:
            if error:
                raise UserError(
                    request.env._(
                        "Instagram did not authorize the account: %(error)s",
                        error=error_description or error,
                    )
                )
            if not wizard or not code:
                raise UserError(
                    request.env._(
                        "Invalid OAuth state token. Please restart the account "
                        "association."
                    )
                )
            # Instagram appends ``#_`` to the code it redirects with.
            code = code.split("#")[0]
            with request.env.cr.savepoint():
                token, scopes = SocialAccount.sudo()._instagram_exchange_code(
                    code,
                    wizard._get_url_redirect(),
                    wizard.instagram_app_id,
                    wizard.instagram_app_secret,
                )
                account = SocialAccount._create_account_instagram(
                    wizard, token, scopes
                )
            SocialAccount._notify_user_session(
                SocialAccount._format_user_notification(
                    request.env._(
                        "The account @%(username)s was associated successfully.",
                        username=account.username,
                    ),
                    media="instagram",
                    message_type="success",
                ),
                message_type="success",
            )
        except (AccessError, UserError) as e:
            # Written for the end user and explaining what to do, so they are
            # the useful feedback of the association flow.
            SocialAccount._notify_user_session(
                SocialAccount._format_user_notification(str(e), media="instagram")
            )
            _logger.warning("Error in the Instagram OAuth callback: %s", e)
        except Exception:  # noqa: BLE001 - the provider may fail in any way
            SocialAccount._notify_association_failure("instagram")
            _logger.exception("Error in the Instagram OAuth callback")
        finally:
            wizard.sudo().unlink()
        return request.redirect(SocialAccount._get_social_dashboard_url())
