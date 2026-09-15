"""Fast, browser-free status check for a whole site list.

This is diagnosis only. It does NOT keep an account alive - a plain HTTP fetch
does not execute JavaScript, so it never registers as a visitor. Use it to find
out where a list stands right now; use keepalive.py to actually keep it alive.

The trick that makes this work without spoofing a crawler:

    A dead account serves iFastNet's "DNS Resolution Error" page to everyone.
    An account that still exists serves the JavaScript security wall instead.

So from an ordinary browser user agent, hitting the wall means an account is
attached to that domain. On the 5,595-site legacy list that split was exact:
every host behind the wall had an account, every host on the DNS page did not.

IMPORTANT LIMIT: "an account exists" is not "the site is healthy". A SUSPENDED
account also serves the security wall, and its suspension notice only appears
once the wall has been solved - which this script cannot do. On 2026-09-15,
13 hosts this script called ACCOUNT_EXISTS turned out to include 3 suspended
ones when keepalive.py opened them in a real browser.

So use this to sort a large list quickly, and never as the final word. Only
keepalive.py, which runs a real browser, can tell alive from suspended - and
suspension is the state that still has a recovery window, so it is the one
worth catching.

Keep concurrency low. At 24 workers iFastNet throttled us and returned tiny
555-byte 404s for thousands of hosts that were perfectly fine - which is exactly
the kind of false reading this whole repo exists to avoid.
"""

import argparse
import csv
import os
import random
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classify import classify  # noqa: E402
from sitelist import load_hosts  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

TITLE_RE = re.compile(r"<title[^>]*>([^<]*)", re.I)
ATTEMPTS = 2

# For a plain HTTP client, reaching the security wall means an account is
# attached to the domain - no more than that. Deliberately NOT called "alive":
# a suspended account looks identical from here.
# (For keepalive.py's real browser the same verdict means the opposite: the
# challenge failed to solve, so nothing was learned. Same string, different
# client, different meaning - hence the remap here rather than in classify.py.)
VERDICT_REMAP = {"JS_WALL": "ACCOUNT_EXISTS"}

# "Worth a proper browser check", not "confirmed healthy".
ALIVE_VERDICTS = {"ALIVE", "ACCOUNT_EXISTS"}

_print_lock = threading.Lock()


def check(host):
    verdict, code, size, title = "UNKNOWN", 0, 0, ""
    for attempt in range(ATTEMPTS):
        try:
            time.sleep(random.uniform(0.2, 1.2))
            resp = requests.get("http://" + host, headers=HEADERS, timeout=15,
                                allow_redirects=True)
            code, size = resp.status_code, len(resp.content)
            match = TITLE_RE.search(resp.text)
            title = (match.group(1).strip()[:60] if match else "")
            raw = classify(code, resp.text, size)
            verdict = VERDICT_REMAP.get(raw, raw)
            if verdict == "THROTTLED" and attempt + 1 < ATTEMPTS:
                time.sleep(random.uniform(4, 9))
                continue
            break
        except Exception as exc:
            verdict, title = "UNREACHABLE", type(exc).__name__
            if attempt + 1 < ATTEMPTS:
                time.sleep(random.uniform(2, 6))
    row = {"host": host, "verdict": verdict, "http_code": code, "bytes": size,
           "title": title.replace(",", " ")}
    with _print_lock:
        mark = " " if verdict in ALIVE_VERDICTS else "!"
        print("%s %-36s %-22s %s" % (mark, host, verdict, title[:40]), flush=True)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default="sites.txt")
    ap.add_argument("--out", default="reports/audit.csv")
    ap.add_argument("--alive-out", default="reports/audit_alive.txt",
                    help="hosts worth a full browser check, not a health list")
    ap.add_argument("--concurrency", type=int, default=6,
                    help="keep this low; high values get you throttled")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    hosts = load_hosts(args.sites, args.limit)

    print("auditing %d hosts at concurrency %d\n" % (len(hosts), args.concurrency),
          flush=True)
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        rows = list(pool.map(check, hosts))

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["host", "verdict", "http_code",
                                               "bytes", "title"])
        writer.writeheader()
        writer.writerows(rows)

    alive = sorted(r["host"] for r in rows if r["verdict"] in ALIVE_VERDICTS)
    with open(args.alive_out, "w", encoding="utf-8") as f:
        f.write("\n".join(alive) + ("\n" if alive else ""))

    print("\n===== totals =====")
    for verdict, count in Counter(r["verdict"] for r in rows).most_common():
        print("%-26s %5d  %5.1f%%" % (verdict, count, 100.0 * count / len(rows)))
    print("-" * 42)
    print("%d of %d have an account -> %s" % (len(alive), len(rows), args.alive_out))
    print("NOTE: ACCOUNT_EXISTS cannot tell alive from suspended. "
          "Run keepalive.py for that.")


if __name__ == "__main__":
    main()
