"""Shared status classification for InfinityFree / iFastNet hosted sites.

One place decides what a response means, so the keep-alive visitor and the
quick auditor can never disagree with each other.
"""

# The account is gone: the free subdomain no longer resolves to an active
# hosting account. iFastNet serves this as HTTP 200, which is why the old
# script counted these as successes for months.
# Kept deliberately specific. A loose marker like "domain not found" would also
# match any page that merely writes about domains.
DEAD_MARKERS = [
    "dns resolution error",
    "domain not added to hosting account",
    "domain not found, or domain not added",
]

# Account still exists but is switched off. This state is RECOVERABLE from the
# client area, and that is the whole reason this repo exists.
SUSPENDED_MARKERS = [
    "this domain has been suspended",
    "account has been suspended",
    "suspended-domain.net",
    "suspendedpage",
    "site is sleeping",
    "contact support in your hosting control panel",
]

# Temporary: hit/CPU/entry-process limits. Clears on its own.
# Never put a bare number like "509" here. It matched arbitrary digits in normal
# page markup and reported healthy WordPress sites as over their limit.
OVER_LIMIT_MARKERS = [
    "reaching server limits",
    "resource limit is reached",
    "509 bandwidth limit exceeded",
    "daily hit limit",
]

# The browser security wall. A real browser solves it; plain HTTP clients do not.
JS_WALL_MARKERS = [
    "aes.js",
    "this site requires javascript to work",
    "checking your browser",
    "slowaes",
    "tonumbers(",
]

# Nothing was really served. Almost always our own rate limiting, not the site.
THROTTLE_BYTE_CEILING = 1200

# Verdicts that mean "this site is fine".
OK_VERDICTS = {"ALIVE"}

# Verdicts that mean "act now, the clock is running".
URGENT_VERDICTS = {"DEAD_NO_ACCOUNT", "SUSPENDED"}


def classify(status_code, body, byte_len=None):
    """Return a verdict string for one fetched page.

    Order matters: content beats status code, because iFastNet returns 200 for
    dead accounts and sometimes 404 for perfectly live ones.
    """
    text = (body or "").lower()
    if byte_len is None:
        byte_len = len(body or "")

    if any(m in text for m in DEAD_MARKERS):
        return "DEAD_NO_ACCOUNT"
    if any(m in text for m in SUSPENDED_MARKERS):
        return "SUSPENDED"
    if any(m in text for m in OVER_LIMIT_MARKERS):
        return "OVER_LIMIT"
    if any(m in text for m in JS_WALL_MARKERS):
        return "JS_WALL"
    if byte_len < THROTTLE_BYTE_CEILING:
        # Too small to be a real page. Do not blame the site for this.
        return "THROTTLED"
    if status_code == 200:
        return "ALIVE"
    if status_code == 0:
        return "UNREACHABLE"
    return "HTTP_%s" % status_code


def is_ok(verdict):
    return verdict in OK_VERDICTS


def is_inconclusive(verdict):
    """Verdicts that say nothing about the site and must never mark it dead."""
    return verdict in ("THROTTLED", "UNREACHABLE", "JS_WALL", "OVER_LIMIT", "UNKNOWN")
