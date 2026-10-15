# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re
from datetime import date, datetime, timedelta, timezone

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
    "instagram_business_manage_comments",
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

# Account insights asked as totals over a range of days.
_ACCOUNT_METRICS_INSTAGRAM = (
    "views,reach,likes,comments,shares,saves,total_interactions,profile_links_taps"
)
_ACCOUNT_METRICS_FALLBACK_INSTAGRAM = "views,reach,likes,comments,shares,saves"
# Instagram refuses an insights range wider than 30 days, and serves no data
# older than about two years.
_INSIGHTS_MAX_RANGE_DAYS_INSTAGRAM = 30
_INSIGHTS_MAX_AGE_DAYS_INSTAGRAM = 729
# Parallel requests used to build a chart, one request per bucket.
_CHART_WORKERS_INSTAGRAM = 6

# How many of the latest publications the synchronization reads, and for how
# many days back the insights of a publication are asked again. Older
# publications keep the likes and comments counters of the media list, which
# cost nothing more.
_MEDIA_SYNC_LIMIT_INSTAGRAM = 100
_MEDIA_PAGE_SIZE_INSTAGRAM = 50
_INSIGHTS_WINDOW_DAYS_INSTAGRAM = 90
# Publications read by the 30 minutes check to detect what the user published
# by hand in the Instagram app.
_MEDIA_CHECK_LIMIT_INSTAGRAM = 15

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


def to_datetime(value):
    """Accept what the chart client sends: a datetime, a date or a string."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        return datetime.fromisoformat(value.strip()[:19].replace("T", " "))
    return value


def chart_buckets(start_date, end_date, freq="W-MON"):
    """Return the ``(first_day, last_day)`` of every bucket of a chart.

    It mirrors ``social_media_base.social_utils.get_weeks``, which builds the
    labels of the chart: one bucket per label, in the same order, so the data
    lines up with the axis.
    """
    start = to_datetime(start_date)
    end = to_datetime(end_date)
    buckets = []
    if not start or not end:
        return buckets
    if freq == "D":
        current = start
        while current <= end:
            buckets.append((current.date(), current.date()))
            current += timedelta(days=1)
    elif freq == "ME":
        current = start.replace(day=1)
        while current <= end:
            next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
            last_day = next_month - timedelta(days=1)
            if last_day <= end:
                buckets.append((current.date(), last_day.date()))
            current = next_month
    else:
        days_ahead = (0 - start.weekday()) % 7
        current = start + timedelta(days=days_ahead)
        while current <= end:
            buckets.append((current.date(), (current + timedelta(days=6)).date()))
            current += timedelta(weeks=1)
    return buckets


def split_range(first_day, last_day, max_days=_INSIGHTS_MAX_RANGE_DAYS_INSTAGRAM):
    """Split a range of days into ranges Instagram answers in one call."""
    chunks = []
    current = first_day
    while current <= last_day:
        chunk_end = min(current + timedelta(days=max_days - 1), last_day)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


def day_timestamp(day):
    """Unix timestamp of the start of a day in UTC."""
    return int(
        datetime.combine(day, datetime.min.time())
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


def engagement_rate(interactions, reach):
    """Interactions over the accounts reached, in percent."""
    return round(interactions / reach * 100, 2) if reach else 0.0
