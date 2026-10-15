# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import secrets
from urllib.parse import urlencode, urljoin

from odoo import api, fields, models

from ..social_instagram_utils import _SCOPE_INSTAGRAM, _URL_AUTHORIZE_INSTAGRAM

_CALLBACK_INSTAGRAM = "/social_instagram/callback"


class WizardSocialAccount(models.TransientModel):
    """Instagram steps of the account association wizard."""

    _inherit = "wizard.social.account"

    instagram_app_id = fields.Char(string="Instagram App ID")
    instagram_app_secret = fields.Char(string="Instagram App Secret")
    instagram_redirect_uri = fields.Char(
        string="Redirect URI",
        compute="_compute_instagram_redirect_uri",
        help="Add this address to the valid OAuth redirect URIs of the "
        "'Instagram business login' settings of your Meta app.",
    )

    @api.depends("media_id")
    def _compute_instagram_redirect_uri(self):
        for wizard in self:
            wizard.instagram_redirect_uri = urljoin(
                wizard.get_base_url(), _CALLBACK_INSTAGRAM
            )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        account = self.env["social.account"].browse(res.get("account_id"))
        if (
            account.exists()
            and account.media_type == "instagram"
            and self.env.user.has_group("base.group_system")
        ):
            res.setdefault("instagram_app_id", account.sudo().instagram_app_id)
        return res

    def _get_url_redirect(self):
        if self.media_type == "instagram":
            return urljoin(self.get_base_url(), _CALLBACK_INSTAGRAM)
        return super()._get_url_redirect()

    def _get_csrf_state_token(self):
        if self.media_type == "instagram":
            return secrets.token_urlsafe(32)
        return super()._get_csrf_state_token()

    def _instagram_authorize_url(self):
        self.ensure_one()
        params = {
            "enable_fb_login": 0,
            "force_authentication": 1,
            "client_id": self.instagram_app_id,
            "redirect_uri": self._get_url_redirect(),
            "response_type": "code",
            "scope": ",".join(_SCOPE_INSTAGRAM),
            "state": self.csrf_state_token,
        }
        return f"{_URL_AUTHORIZE_INSTAGRAM}?{urlencode(params)}"

    def _action_add_account(self):
        res = super()._action_add_account()
        if self.media_type != "instagram":
            return res
        return {
            "type": "ir.actions.act_url",
            "url": self._instagram_authorize_url(),
            "target": "self",
        }

    def _instagram_fill_keys_from_account(self):
        """Reuse the keys of the account when the user did not type new ones."""
        account = self.account_id.sudo()
        if not self.instagram_app_id:
            self.instagram_app_id = account.instagram_app_id
        if not self.instagram_app_secret:
            self.instagram_app_secret = account.instagram_app_secret

    def _update_account(self):
        if self.media_type != "instagram":
            return super()._update_account()
        account = self.account_id
        if self.update_keys or account.instagram_token_expired:
            # A new authorization in the browser, which the callback closes
            # on this very wizard.
            self._instagram_fill_keys_from_account()
            return self.action_associate_social_account()
        if self.update_token:
            account._instagram_refresh_token()
            account._notify_user_client(
                notif_type="social_form_success",
                notif_message=self.env._("The token was refreshed for 60 days."),
                media="instagram",
                account_name=account.name,
            )
            return True
        return account.action_instagram_check_connection()
