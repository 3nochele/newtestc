"""Cheap DNS pre-filter, so dead domains never cost a browser visit.

iFastNet points every subdomain with no active hosting account at one parking
address, which serves the "DNS Resolution Error" page. Live accounts sit on
ordinary hosting IPs, scattered across the range.

Measured on this project's own data (2026-09-15):

    450 of 450 known-dead hosts  ->  185.27.134.24   (100%)
      0 of 152 known-alive hosts ->  185.27.134.24   (0%)

Resolution runs at roughly 65 hosts/second, versus about 15 seconds per host
for a full browser visit.

The point is NOT speed. It is that a list full of dead domains would otherwise
send thousands of pointless requests at iFastNet in one run, and that is how an
IP gets blocked - which would make the *live* sites unreachable and let them die
of the very inactivity this repo exists to prevent.

Safety rule, enforced by the caller: DNS may only ever let us SKIP work. It must
never be the sole reason a site is recorded as dead. If iFastNet renumbers, or a
resolver returns something stale, the worst case must be a wasted visit, never a
live site quietly dropped.
"""

import socket
from concurrent.futures import ThreadPoolExecutor

# Verify this if verdicts ever start looking wrong in bulk: resolve a handful of
# known-live and known-dead hosts and compare. A changed parking IP shows up as
# every dead host suddenly landing somewhere else.
PARKING_IPS = {"185.27.134.24"}

RESOLVE_TIMEOUT_SECONDS = 8
DEFAULT_WORKERS = 24


def resolve(host):
    """Return the host's A record, or None if it cannot be resolved.

    None means "we do not know", never "dead" - an unreachable resolver must not
    look like a parked domain.
    """
    try:
        return socket.gethostbyname(host.split(":")[0])
    except Exception:
        return None


def resolve_many(hosts, workers=DEFAULT_WORKERS):
    """Map every host to its IP (or None). Safe to call on thousands."""
    hosts = list(hosts)
    if not hosts:
        return {}
    socket.setdefaulttimeout(RESOLVE_TIMEOUT_SECONDS)
    with ThreadPoolExecutor(max_workers=min(workers, max(len(hosts), 1))) as pool:
        return dict(zip(hosts, pool.map(resolve, hosts)))


def is_parked(ip):
    """True only for a positively identified parking address."""
    return ip is not None and ip in PARKING_IPS


def partition(hosts, workers=DEFAULT_WORKERS):
    """Split hosts into (likely_parked, needs_full_visit, ip_by_host).

    Anything we could not resolve lands in needs_full_visit, because not knowing
    is not the same as knowing it is dead.
    """
    ips = resolve_many(hosts, workers)
    parked, full = [], []
    for host in hosts:
        (parked if is_parked(ips.get(host)) else full).append(host)
    return parked, full, ips
