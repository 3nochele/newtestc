"""Regression tests for the status classifier and the recovery-window logic.

Run with:  python scripts/test_classify.py

Every case here comes from something that actually went wrong:

  * The dead-account page returns HTTP 200. The original script called that
    "Success" for months while thousands of accounts were deleted.
  * A live site 404s on /about-us. The original script called that a failure.
  * A bare "509" in the over-limit markers matched arbitrary digits in normal
    page markup and reported healthy WordPress sites as over their limit.
  * Our own throttling and IP blocks must never be recorded against a site.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classify import classify, is_inconclusive, is_ok  # noqa: E402
from sitelist import norm_host  # noqa: E402

FAILURES = []


def check(label, got, want):
    if got != want:
        FAILURES.append("%s: got %r, want %r" % (label, got, want))
        print("FAIL  %-58s %r != %r" % (label, got, want))
    else:
        print("ok    %-58s %r" % (label, got))


PAD = "<p>" + ("filler " * 400) + "</p>"

DEAD_PAGE = """<!DOCTYPE html><html><head>
<title>DNS Resolution Error - Domain Not Found, Or Domain not added to hosting account</title>
</head><body><h1>DNS Resolution Error</h1>
<p>The domain you're trying to reach cannot be found</p>
<strong>Or the requested domain is not yet added to an active hosting account.</strong>
""" + PAD + "</body></html>"

SUSPENDED_PAGE = ("<html><head><title>Account Suspended</title></head><body>"
                  "<h1>This domain has been suspended</h1>" + PAD + "</body></html>")

OVER_LIMIT_PAGE = ("<html><body><h1>This account is reaching server limits</h1>"
                   + PAD + "</body></html>")

JS_WALL_PAGE = ("<html><body><script src='/aes.js'></script>"
                "<script>toNumbers('a1b2c3')</script>"
                "<noscript>This site requires Javascript to work</noscript>"
                + PAD + "</body></html>")

# A perfectly ordinary live WordPress homepage, with digits scattered around -
# including a 509 - because that is what broke the classifier the first time.
LIVE_PAGE = ("<html><head><title>Tyler Farncomb &#8211; My WordPress Blog</title>"
             "</head><body><article id='post-509'>"
             "<p>Published 2026. Views: 1509. Item 509 of 900.</p>" + PAD
             + "</article></body></html>")

# A real 404 from a live site: the page a missing /about-us returns.
LIVE_404_PAGE = ("<html><head><title>Page not found &#8211; My WordPress Blog</title>"
                 "</head><body><h1>Nothing here</h1>"
                 "<p>Try a search?</p>" + PAD + "</body></html>")

THROTTLE_PAGE = "<html><head><title>404</title></head><body>404 Not Found</body></html>"

print("--- verdicts -------------------------------------------------------")
# Content beats status code: iFastNet serves the dead page with a 200.
check("dead account, HTTP 200", classify(200, DEAD_PAGE), "DEAD_NO_ACCOUNT")
check("dead account, HTTP 404", classify(404, DEAD_PAGE), "DEAD_NO_ACCOUNT")
check("suspended account", classify(200, SUSPENDED_PAGE), "SUSPENDED")
check("over server limits", classify(200, OVER_LIMIT_PAGE), "OVER_LIMIT")
check("javascript security wall", classify(200, JS_WALL_PAGE), "JS_WALL")
check("live wordpress homepage", classify(200, LIVE_PAGE), "ALIVE")
# The bug that made 4,000 healthy sites look broken.
check("live site 404 is not a dead site", classify(404, LIVE_404_PAGE), "HTTP_404")
# The bug that made healthy sites look rate-limited.
check("'509' in normal markup is not OVER_LIMIT", classify(200, LIVE_PAGE), "ALIVE")
check("tiny body is throttling, not a verdict",
      classify(404, THROTTLE_PAGE), "THROTTLED")
check("connection failure", classify(0, ""), "THROTTLED")

print("\n--- what may count against a site ---------------------------------")
check("ALIVE is ok", is_ok("ALIVE"), True)
check("DEAD counts against the site", is_inconclusive("DEAD_NO_ACCOUNT"), False)
check("SUSPENDED counts against the site", is_inconclusive("SUSPENDED"), False)
# The IP block we hit while testing must not condemn 150 healthy sites.
check("THROTTLED never counts", is_inconclusive("THROTTLED"), True)
check("UNREACHABLE never counts", is_inconclusive("UNREACHABLE"), True)
check("JS_WALL never counts", is_inconclusive("JS_WALL"), True)
check("OVER_LIMIT never counts", is_inconclusive("OVER_LIMIT"), True)

print("\n--- site list parsing ---------------------------------------------")
check("plain host", norm_host("example.wuaze.com"), "example.wuaze.com")
check("http prefix and trailing space",
      norm_host("http://tyler.kesug.com \n"), "tyler.kesug.com")
check("https with path", norm_host("https://a.is-best.net/about"), "a.is-best.net")
check("uppercase folds", norm_host("HTTP://Foo.Bar.NET"), "foo.bar.net")
check("blank line", norm_host("   "), None)
check("comment", norm_host("# a note"), None)
check("no dot", norm_host("vst"), None)
check("bare scheme", norm_host("http:"), None)
check("stray arrow", norm_host("site.com ↑"), None)

print("\n--- internal link selection ---------------------------------------")
from keepalive import filter_internal_links  # noqa: E402

HOST = "blog.wuaze.com"
HREFS = [
    "/hello-world/",                    # keep
    "post-two/",                        # keep, relative
    "http://blog.wuaze.com/deep/page/", # keep, absolute same host
    "/",                                # drop, that is the homepage
    "",                                 # drop
    None,                               # drop
    "#section",                         # drop, same-page anchor
    "mailto:a@b.com",                   # drop
    "tel:+880123",                      # drop
    "javascript:void(0)",               # drop
    "/wp-admin/",                       # drop, never touch admin
    "/wp-login.php",                    # drop
    "/?action=logout",                  # drop
    "/feed/",                           # drop
    "/images/photo.png",                # drop, not a page
    "/style.css",                       # drop
    "/paper.pdf",                       # drop
    "https://other-site.com/page/",     # drop, different host
    "/hello-world/",                    # duplicate of the first
]
got = filter_internal_links(HREFS, HOST)
check("keeps only real internal pages", len(got), 3)
check("relative link resolved", "http://blog.wuaze.com/post-two/" in got, True)
check("absolute same-host kept", "http://blog.wuaze.com/deep/page/" in got, True)
check("no admin links", any("wp-admin" in u or "wp-login" in u for u in got), False)
check("no logout link", any("logout" in u for u in got), False)
check("no assets", any(u.endswith((".png", ".css", ".pdf")) for u in got), False)
check("no external hosts", any("other-site.com" in u for u in got), False)
check("no duplicates", len(got), len(set(got)))
check("empty href list is safe", filter_internal_links([], HOST), [])
check("None href list is safe", filter_internal_links(None, HOST), [])

print("\n--- dns pre-filter -------------------------------------------------")
import dnsfilter  # noqa: E402

PARK = "185.27.134.24"
check("parking ip is parked", dnsfilter.is_parked(PARK), True)
check("a hosting ip is not parked", dnsfilter.is_parked("185.27.134.142"), False)
# Unresolvable must mean "unknown", never "dead" - otherwise a resolver outage
# would stop us visiting live sites and let them expire.
check("unresolved is not parked", dnsfilter.is_parked(None), False)

FAKE_DNS = {
    "dead-one.wuaze.com": PARK,
    "dead-two.kesug.com": PARK,
    "live-one.is-best.net": "185.27.134.142",
    "live-two.iblogger.org": "185.27.134.99",
    "no-dns.great-site.net": None,      # resolver failed
}
_real_resolve_many = dnsfilter.resolve_many
dnsfilter.resolve_many = lambda hosts, workers=0: {h: FAKE_DNS[h] for h in hosts}
try:
    parked, full, _ = dnsfilter.partition(list(FAKE_DNS))
    check("parked hosts detected", sorted(parked),
          ["dead-one.wuaze.com", "dead-two.kesug.com"])
    check("live hosts still get visited",
          "live-one.is-best.net" in full and "live-two.iblogger.org" in full, True)
    check("unresolvable host still gets visited",
          "no-dns.great-site.net" in full, True)
    check("every host accounted for", len(parked) + len(full), len(FAKE_DNS))
    empty_parked, empty_full, _ = dnsfilter.partition([])
    check("empty input is safe", (empty_parked, empty_full), ([], []))
finally:
    dnsfilter.resolve_many = _real_resolve_many


print("\n--- plugin result parsing -------------------------------------------")
import report  # noqa: E402

p = report.parse_plugin_cell("published posts=5 gen=2 rendered=ok")
check("state read", p["state"], "published")
check("post count read", p["posts"], 5)
check("generated count read", p["gen"], 2)
check("render result read", p["rendered"], "ok")
check("bare state", report.parse_plugin_cell("absent")["state"], "absent")
check("empty cell", report.parse_plugin_cell("")["state"], "")
check("missing cell", report.parse_plugin_cell(None)["state"], "")
check("junk numbers ignored", report.parse_plugin_cell("published posts=x")["posts"], None)

print("\n--- plugin problem detection ----------------------------------------")


def prob(previous_posts, cell):
    entry = {} if previous_posts is None else {"plugin_posts": str(previous_posts)}
    return report.plugin_problem(entry, report.parse_plugin_cell(cell))


check("healthy publish is fine", prob(4, "published posts=5 gen=2 rendered=ok"), None)
check("skipped is fine", prob(5, "skipped posts=5 gen=2"), None)
check("no plugin cell is fine", prob(None, ""), None)
check("first ever run is fine", prob(None, "published posts=1 gen=1 rendered=ok"), None)
check("missing plugin is flagged",
      prob(None, "absent"), "plugin not installed or not activated")
check("token mismatch is flagged",
      prob(None, "bad_token"), "site token does not match GHKA_TOKEN")
# The failure that matters: the API says it worked, the site disagrees.
check("count not rising is flagged",
      prob(5, "published posts=5 gen=3 rendered=ok") is not None, True)
check("count going backwards is flagged",
      prob(9, "published posts=5 gen=3 rendered=ok") is not None, True)
check("post that will not load is flagged",
      prob(4, "published posts=5 gen=2 rendered=renders-http_404") is not None, True)
check("odd reply is flagged", prob(4, "not_json") is not None, True)

print("\n--- recovery window ------------------------------------------------")
from datetime import datetime, timedelta, timezone  # noqa: E402
import report  # noqa: E402

REF = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


def entry(verdict, days_down=None):
    row = {"last_verdict": verdict, "last_checked": REF.strftime(report.TS)}
    if days_down is not None:
        row["down_since"] = (REF - timedelta(days=days_down)).strftime(report.TS)
    return row


def entry_seen_alive(verdict, days_down, last_alive_days=30):
    row = entry(verdict, days_down)
    row["last_alive"] = (REF - timedelta(days=last_alive_days)).strftime(report.TS)
    return row


check("alive -> OK", report.bucket(entry("ALIVE"), REF), "OK")

# A suspended account can be reactivated whether or not we ever saw it working.
check("suspended, never seen alive -> RECOVER_NOW",
      report.bucket(entry("SUSPENDED", 0), REF), "RECOVER_NOW")
# But a domain with no account behind it, that we never saw working, was gone
# before we started. Its down_since is just the day we first looked.
check("no-account, never seen alive -> LIKELY_LOST",
      report.bucket(entry("DEAD_NO_ACCOUNT", 0), REF), "LIKELY_LOST")
# Whereas one that WAS working and has just gone is real, urgent news.
check("no-account, was alive recently -> RECOVER_NOW",
      report.bucket(entry_seen_alive("DEAD_NO_ACCOUNT", 2, 6), REF), "RECOVER_NOW")

check("down today -> RECOVER_NOW",
      report.bucket(entry_seen_alive("DEAD_NO_ACCOUNT", 0), REF), "RECOVER_NOW")
check("down 17 days -> RECOVER_NOW",
      report.bucket(entry("SUSPENDED", 17), REF), "RECOVER_NOW")
check("down 18 days -> RECOVER_NOW (boundary)",
      report.bucket(entry("SUSPENDED", 18), REF), "RECOVER_NOW")
check("down 19 days -> AT_RISK",
      report.bucket(entry_seen_alive("DEAD_NO_ACCOUNT", 19, 25), REF), "AT_RISK")
check("down 61 days -> LIKELY_LOST",
      report.bucket(entry_seen_alive("DEAD_NO_ACCOUNT", 61, 70), REF), "LIKELY_LOST")
check("inconclusive -> UNKNOWN", report.bucket(entry("THROTTLED", 40), REF), "UNKNOWN")

print("\n--- state updates --------------------------------------------------")
STAMP = REF.strftime(report.TS)

st = {"last_verdict": "DEAD_NO_ACCOUNT", "consecutive_fail": "3",
      "down_since": "2026-08-01 00:00:00", "total_checks": "9", "total_alive": "6",
      "last_alive": "2026-08-01 00:00:00"}
report.apply_result(st, {"verdict": "ALIVE"}, STAMP)
check("recovery clears down_since", st["down_since"], "")
check("recovery clears fail count", st["consecutive_fail"], "0")
check("recovery stamps last_alive", st["last_alive"], STAMP)

st = {"last_verdict": "ALIVE", "consecutive_fail": "0", "down_since": "",
      "total_checks": "9", "total_alive": "9", "last_alive": STAMP}
report.apply_result(st, {"verdict": "THROTTLED"}, STAMP)
check("throttled leaves fail count alone", st["consecutive_fail"], "0")
check("throttled does not open down_since", st["down_since"], "")

st = {"last_verdict": "ALIVE", "consecutive_fail": "0", "down_since": "",
      "total_checks": "9", "total_alive": "9", "last_alive": "2026-08-20 00:00:00"}
report.apply_result(st, {"verdict": "DEAD_NO_ACCOUNT"}, STAMP)
check("first real failure opens down_since", st["down_since"], STAMP)
check("first real failure counts", st["consecutive_fail"], "1")
check("failure does not touch last_alive", st["last_alive"], "2026-08-20 00:00:00")

print("\n--- the alert actually fires ----------------------------------------")
# The most important behaviour in the repo: the run must stay red for as long
# as a site can still be saved. Alerting only on the day a site broke meant one
# missed email became a deleted account.
import subprocess  # noqa: E402
import tempfile  # noqa: E402

SCRIPTS = os.path.dirname(os.path.abspath(__file__))


def run_report(rows, prior_state=""):
    """Run report.py for real and return its exit code."""
    tmp = tempfile.mkdtemp(prefix="ka-test-")
    with open(os.path.join(tmp, "shard-0.csv"), "w", encoding="utf-8") as f:
        f.write("host,verdict,http_code,pages_viewed,detail,checked_by,plugin,checked_at\n")
        f.write(rows)
    state_path = os.path.join(tmp, "state.csv")
    if prior_state:
        with open(state_path, "w", encoding="utf-8") as f:
            f.write(",".join(report.STATE_COLS) + "\n" + prior_state)
    with open(os.path.join(tmp, "sites.txt"), "w", encoding="utf-8") as f:
        f.write("a.wuaze.com\nb.kesug.com\n")
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "report.py"),
         "--results", os.path.join(tmp, "shard-*.csv"),
         "--sites", os.path.join(tmp, "sites.txt"),
         "--state", state_path,
         "--history", os.path.join(tmp, "history.csv"),
         "--attention", os.path.join(tmp, "ATTENTION.md"),
         "--alive-list", os.path.join(tmp, "alive.txt"),
         "--summary", ""],
        capture_output=True, text=True)
    return proc.returncode


NOW = report.now_utc()
ALL_OK = ("a.wuaze.com,ALIVE,200,2,Blog A,browser,,%s\n"
          "b.kesug.com,ALIVE,200,2,Blog B,browser,,%s\n" % (NOW, NOW))
ONE_DOWN = ("a.wuaze.com,ALIVE,200,2,Blog A,browser,,%s\n"
            "b.kesug.com,SUSPENDED,200,1,Domain Suspended,browser,,%s\n" % (NOW, NOW))

check("everything healthy -> green", run_report(ALL_OK), 0)
check("a site goes down -> red", run_report(ONE_DOWN), 1)

# Down for six days: no longer "new", still inside the recovery window.
SIX_DAYS_AGO = (datetime.now(timezone.utc) - timedelta(days=6)).strftime(report.TS)
STILL_DOWN_STATE = (
    "b.kesug.com,2026-01-01 00:00:00,%s,SUSPENDED,%s,2,%s,10,8,,,\n"
    % (SIX_DAYS_AGO, SIX_DAYS_AGO, SIX_DAYS_AGO))
check("still down six days later -> STAYS red",
      run_report(ONE_DOWN, STILL_DOWN_STATE), 1)

# Long past saving: nothing to act on, so it must not cry wolf forever.
LONG_GONE = (datetime.now(timezone.utc) - timedelta(days=120)).strftime(report.TS)
LOST_STATE = ("b.kesug.com,2026-01-01 00:00:00,%s,DEAD_NO_ACCOUNT,%s,20,%s,30,8,,,\n"
              % (LONG_GONE, LONG_GONE, LONG_GONE))
DEAD_ROW = ("a.wuaze.com,ALIVE,200,2,Blog A,browser,,%s\n"
            "b.kesug.com,DEAD_NO_ACCOUNT,200,1,DNS Resolution Error,browser,,%s\n"
            % (NOW, NOW))
check("long-lost site does not keep the run red", run_report(DEAD_ROW, LOST_STATE), 0)

# Our own trouble must never raise an alarm about the site.
THROTTLED = ("a.wuaze.com,ALIVE,200,2,Blog A,browser,,%s\n"
             "b.kesug.com,THROTTLED,0,0,,browser,,%s\n" % (NOW, NOW))
check("our own throttling -> green", run_report(THROTTLED), 0)

print("\n" + "=" * 70)
if FAILURES:
    print("%d FAILURE(S):" % len(FAILURES))
    for f in FAILURES:
        print("  " + f)
    sys.exit(1)
print("all tests passed")
