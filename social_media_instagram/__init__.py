# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from . import controllers
from . import models
from . import wizards


def uninstall_hook(env):
    """Archive the Instagram accounts and drop their credentials."""
    env["social.account"]._remove_social_media("instagram")
