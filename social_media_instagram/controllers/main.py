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
        SocialAccount = request.env["social.account"]
        # The wizard is looked up by its state token and its creator, so a
        # state leaking out of the authorization URL is useless elsewhere.
        wizard = (
            request.env["wizard.social.account"]
            .sudo()
            .search(
                [
                    ("csrf_state_token", "=", state or "-"),
                    ("create_uid", "=", request.env.user.id),
                ],
                limit=1,
            )
            if state
            else request.env["wizard.social.account"]
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
            SocialAccount._notify_user_session(
                SocialAccount._format_user_notification(str(e), media="instagram")
            )
            _logger.warning("Error in the Instagram OAuth callback: %s", e)
        except Exception:  # noqa: BLE001 - the provider may fail in any way
            SocialAccount._notify_user_session(
                SocialAccount._format_user_notification(
                    request.env._(
                        "The account could not be associated. Check the server "
                        "log for details."
                    ),
                    media="instagram",
                )
            )
            _logger.exception("Error in the Instagram OAuth callback")
        finally:
            wizard.sudo().unlink()
        return request.redirect(SocialAccount._get_social_dashboard_url())
