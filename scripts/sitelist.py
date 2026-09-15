"""Reading the site list.

Kept separate from keepalive.py so that the browser-free tools (audit.py,
report.py) do not have to import Playwright just to parse a text file.
"""

from urllib.parse import urlparse


def norm_host(line):
    """Turn one messy list line into a bare hostname, or None if it is junk.

    Handles the things that accumulate in a hand-edited list: trailing spaces,
    http:// prefixes, paths, stray arrows, blank lines and comments.
    """
    line = (line or "").strip()
    if not line or line.startswith("#"):
        return None
    if "://" in line:
        line = urlparse(line).netloc or line.split("://", 1)[1]
    line = line.split("/")[0].strip().lower()
    if "." not in line or " " in line or len(line) < 4:
        return None
    if any(ch in line for ch in "↑↓<>\"'"):
        return None
    return line


def load_hosts(path, limit=0):
    """Unique, sorted, junk-free hostnames from a site list file."""
    with open(path, encoding="utf-8", errors="replace") as f:
        hosts = sorted({h for h in (norm_host(line) for line in f) if h})
    return hosts[:limit] if limit else hosts
