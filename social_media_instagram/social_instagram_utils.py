# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re
from datetime import datetime, timezone

# Instagram API with Instagram Login: no Facebook page is needed, only an
# Instagram professional (business or creator) account.
_URL_AUTHORIZE_INSTAGRAM = "https://www.instagram.com/oauth/authorize"
_URL_API_INSTAGRAM = "https://api.instagram.com"
_URL_GRAPH_INSTAGRAM = "https://graph.instagram.com"
_URL_WEB_INSTAGRAM = "https://www.instagram.com"

# Graph API version used for every call. It can be moved without touching the
# code through the system parameter ``social_media_instagram.graph_version``.
_GRAPH_VERSION_INSTAGRAM = "v25.0"
_PARAM_GRAPH_VERSION_INSTAGRAM = "social_media_instagram.graph_version"

# The permissions requested on authorization. ``instagram_business_content_
# publish`` is deliberately NOT requested: this connector never uploads a
# publication, it only plans them and reads them back once the user published
# them by hand.
_SCOPE_INSTAGRAM = [
    "instagram_business_basic",
    "instagram_business_manage_insights",
]

_MEDIA_FIELDS_INSTAGRAM = (
    "id,caption,media_type,media_product_type,permalink,shortcode,timestamp,"
    "like_count,comments_count,media_url,thumbnail_url"
)
_PROFILE_FIELDS_INSTAGRAM = (
    "user_id,username,name,account_type,profile_picture_url,"
    "followers_count,follows_count,media_count"
)

# Insights of a publication, by kind of publication. Stories do not report
# likes, comments nor saves.
_MEDIA_METRICS_INSTAGRAM = {
    "STORY": "views,reach,replies,shares,total_interactions",
    "DEFAULT": "views,reach,likes,comments,shares,saved,total_interactions",
}
_MEDIA_METRICS_FALLBACK_INSTAGRAM = "reach,likes,comments,saved"

# Account insights asked as the totals of one day, which is what the daily
# series of ``social.account.statistics`` is made of.
_ACCOUNT_METRICS_INSTAGRAM = (
    "views,reach,likes,comments,shares,saves,total_interactions,profile_links_taps"
)
_ACCOUNT_METRICS_FALLBACK_INSTAGRAM = "views,reach,likes,comments,shares,saves"
# Instagram serves no account insights older than about two years.
_INSIGHTS_MAX_AGE_DAYS_INSTAGRAM = 729
# Parallel requests used to read the daily series, one request per day.
_STATISTICS_WORKERS_INSTAGRAM = 6
# Days of the series asked again on every refresh: Instagram revises the
# figures of the last days for up to 48 hours.
_STATISTICS_REFRESH_DAYS_INSTAGRAM = 7
# Days of the series read when an account is linked. One call per day, so the
# depth is what bounds the cost of the backfill.
_STATISTICS_HISTORY_DAYS_INSTAGRAM = 90

# Publications read by the check for updates: what the user published by hand
# is matched to its plan, the rest is imported. One call, whatever the
# history of the account.
_MEDIA_CHECK_LIMIT_INSTAGRAM = 25
_MEDIA_PAGE_SIZE_INSTAGRAM = 50

# Long-lived tokens last 60 days and can be refreshed once they are 24 hours
# old. They are refreshed when less than this margin is left.
_TOKEN_MARGIN_DAYS_INSTAGRAM = 10

# What Instagram accepts in a publication.
_MAX_CAPTION_LENGTH_INSTAGRAM = 2200
_MAX_HASHTAGS_INSTAGRAM = 30
_MAX_MENTIONS_INSTAGRAM = 20
_MAX_CAROUSEL_ITEMS_INSTAGRAM = 20
_IMAGE_MIMETYPES_INSTAGRAM = (
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/heic",
    "image/webp",
)
_VIDEO_MIMETYPES_INSTAGRAM = ("video/mp4", "video/quicktime")

_RE_HASHTAG = re.compile(r"(?<![\w&])#\w+", re.UNICODE)
_RE_MENTION = re.compile(r"(?<![\w])@[\w.]+", re.UNICODE)
_RE_SHORTCODE = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:p|reel|reels|tv)/([\w-]+)")


def count_hashtags(text):
    return len(_RE_HASHTAG.findall(text or ""))


def count_mentions(text):
    return len(_RE_MENTION.findall(text or ""))


def shortcode_from_url(url):
    """Return the shortcode of an Instagram publication link, or ``False``."""
    match = _RE_SHORTCODE.search(url or "")
    return match.group(1) if match else False


def normalize_caption(text):
    """Return a caption reduced to what survives a copy and paste.

    The comparison has to resist the changes a user makes, or the app does,
    while pasting a planned caption: case, spacing and line breaks.
    """
    return " ".join((text or "").lower().split())


def captions_match(planned, published, length=60):
    """Whether a published caption is the one that was planned.

    Only the beginning is compared: a hashtag added at the end in the app, or
    an emoji the keyboard replaced, must not prevent the match.
    """
    planned = normalize_caption(planned)
    published = normalize_caption(published)
    if not planned or not published:
        return False
    head = planned[:length]
    return published.startswith(head) or planned.startswith(published[:length])


def parse_instagram_datetime(value):
    """Return an Instagram timestamp as a naive UTC datetime, as Odoo stores.

    Instagram answers ``2026-10-15T08:00:00+0000``.
    """
    if not value:
        return False
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return False
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def day_timestamp(day):
    """Unix timestamp of the start of a day in UTC."""
    return int(
        datetime.combine(day, datetime.min.time())
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


def engagement_rate(interactions, reach):
    """Interactions over the accounts reached, as a ratio.

    A ratio like the ``engagement`` of the base, which LinkedIn fills the
    same way, so the figures of both social media read alike.
    """
    return round(interactions / reach, 4) if reach else 0.0
