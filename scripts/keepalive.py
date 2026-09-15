"""Keep free-hosting accounts alive with visits that actually get counted.

Why a real browser and not `requests`:
  InfinityFree's browser security system only lets through clients that execute
  JavaScript and accept cookies. A plain HTTP client either gets stopped at that
  wall, or (with a spoofed Googlebot user agent) slips past it without ever
  registering as a visitor. Neither buys the account any activity credit.
  A real Chromium solves the challenge the way a human's browser does, loads
  every asset, and lands in the account's traffic stats.

Each site gets the homepage plus one link discovered on that homepage: two real
page views, and no guessed URLs that 404.
"""

import argparse
import asyncio
import csv
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dnsfilter  # noqa: E402
from classify import classify, is_inconclusive  # noqa: E402
from sitelist import load_hosts  # noqa: E402

from playwright.async_api import async_playwright

# A current, ordinary desktop Chrome. No crawler spoofing: these are our own
# sites, and a real browser has no reason to lie about what it is.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

VIEWPORTS = [(1920, 1080), (1536, 864), (1440, 900), (1366, 768), (1280, 720)]
LOCALES = ["en-US", "en-GB", "en-CA", "en-AU"]

CONFIRM_ATTEMPTS = 3
NAV_TIMEOUT_MS = 45000
SETTLE_SECONDS = 2.5
MAX_ATTEMPTS = 2

CHALLENGE_HINTS = ("aes.js", "checking your browser", "this site requires javascript")
SKIP_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".pdf",
                 ".zip", ".css", ".js", ".xml", ".ico")


async def human_pause(lo=0.6, hi=2.4):
    await asyncio.sleep(random.uniform(lo, hi))


async def settle_challenge(page):
    """The security wall sets a cookie in JS and reloads itself. Let it finish."""
    for _ in range(3):
        body = (await page.content()).lower()
        if not any(h in body for h in CHALLENGE_HINTS):
            return
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await asyncio.sleep(2.0)
        try:
            await page.reload(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception:
            return


async def read_like_a_person(page):
    """Scroll and move the mouse, so the visit generates the asset and AJAX
    traffic that a one-shot HTML fetch never does."""
    try:
        height = await page.evaluate("document.body.scrollHeight") or 1000
        steps = random.randint(2, 4)
        for i in range(1, steps + 1):
            await page.evaluate("window.scrollTo(0, %d)" % int(height * i / (steps + 1)))
            await human_pause(0.4, 1.2)
        await page.mouse.move(random.randint(200, 900), random.randint(150, 600))
        await human_pause(0.5, 1.5)
    except Exception:
        pass


def filter_internal_links(hrefs, host):
    """Same-host page links only, from the hrefs a page actually contains.

    Pulled out of the async path so it can be unit tested: guessing /about-us
    is what made the old script report thousands of live sites as broken, and
    following /wp-admin or a logout link would be worse than useless.
    """
    base = "http://%s/" % host
    candidates = []
    for href in hrefs or []:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        parts = urlparse(urljoin(base, href))
        if parts.scheme not in ("http", "https"):
            continue
        if parts.netloc.split(":")[0] != host.split(":")[0]:
            continue
        if parts.path.rstrip("/") == "":
            continue
        low = parts.path.lower()
        if low.endswith(SKIP_SUFFIXES):
            continue
        if any(bad in low for bad in ("wp-login", "wp-admin", "logout", "feed")):
            continue
        full = urljoin(base, href)
        if full not in candidates:
            candidates.append(full)
    return candidates


async def ping_plugin(page, token):
    """Ask the GH Keep-Alive plugin to publish, from inside the browser.

    The REST API sits behind the host's JavaScript security wall, so a plain
    HTTP client gets the challenge page instead of JSON. The browser has already
    solved that wall to load the homepage, so the fetch has to happen in the
    page rather than from Python.

    A post written by the site itself runs PHP and writes to the database, which
    is much stronger evidence of a working site than a page view. The reply also
    tells us what the site looks like from the inside - post count, last post
    date, WordPress and PHP versions - which nothing outside the site can see.
    """
    script = """
    async function (token) {
        try {
            const r = await fetch('/wp-json/ghka/v1/ping?token=' + encodeURIComponent(token),
                                  {method: 'POST', credentials: 'same-origin'});
            if (r.status === 404) { return {state: 'absent'}; }
            if (r.status === 403) { return {state: 'bad_token'}; }
            const ct = r.headers.get('content-type') || '';
            if (ct.indexOf('json') === -1) { return {state: 'not_json', code: r.status}; }
            const j = await r.json();
            return {state: j.action || 'unknown', detail: j.detail,
                    url: j.post_url || null, title: j.title || null,
                    posts: j.site && j.site.published_posts,
                    generated: j.site && j.site.generated_count};
        } catch (e) {
            return {state: 'error', detail: String(e)};
        }
    }
    """
    try:
        return await page.evaluate(script, token)
    except Exception as exc:
        return {"state": "error", "detail": type(exc).__name__}


async def verify_published_post(page, res):
    """Open the post the plugin just created and check it really renders.

    A successful REST reply only proves the database write returned without an
    error. Loading the post proves the page actually exists and serves, which is
    the thing that matters - and it is one more genuine page view on a fresh URL
    of our own site.
    """
    url = res.get("url")
    if not url:
        return "no-url"
    try:
        resp = await page.goto(url, wait_until="domcontentloaded",
                               timeout=NAV_TIMEOUT_MS)
        await settle_challenge(page)
        status = resp.status if resp else 0
        # We asked for one specific URL, so the status code is the clearest
        # answer. Routing a 404 through classify() only relabels it as
        # "throttled", which would send someone looking in the wrong place.
        if status != 200:
            return "renders-http_%d" % status
        body = await page.content()
        verdict = classify(status, body)
        if verdict != "ALIVE":
            return "renders-%s" % verdict.lower()
        title = res.get("title") or ""
        if title and title.lower()[:30] not in body.lower():
            return "renders-wrong-page"
        return "ok"
    except Exception as exc:
        return "renders-error-%s" % type(exc).__name__


def summarise_plugin(res, rendered=None):
    """One short cell for the CSV, so the report stays readable."""
    if not res:
        return ""
    state = res.get("state", "unknown")
    if state not in ("published", "skipped"):
        return state
    cell = "%s posts=%s gen=%s" % (state, res.get("posts"), res.get("generated"))
    if rendered:
        cell += " rendered=%s" % rendered
    return cell


async def pick_internal_link(page, host):
    """Use a link the site actually has, instead of guessing /about-us."""
    try:
        hrefs = await page.eval_on_selector_all(
            "a[href]", "els => els.map(function (e) { return e.getAttribute('href'); })")
    except Exception:
        return None
    candidates = filter_internal_links(hrefs, host)
    return random.choice(candidates) if candidates else None


async def visit(browser, host, sem, results, plugin_token=None):
    async with sem:
        verdict, code, detail, pages = "UNKNOWN", 0, "", 0
        plugin = ""

        for attempt in range(MAX_ATTEMPTS):
            context = None
            try:
                width, height = random.choice(VIEWPORTS)
                context = await browser.new_context(
                    user_agent=UA,
                    viewport={"width": width, "height": height},
                    locale=random.choice(LOCALES),
                    ignore_https_errors=True,
                )
                page = await context.new_page()
                page.set_default_timeout(NAV_TIMEOUT_MS)

                resp = await page.goto("http://%s/" % host,
                                       wait_until="domcontentloaded",
                                       timeout=NAV_TIMEOUT_MS)
                code = resp.status if resp else 0
                await asyncio.sleep(SETTLE_SECONDS)
                await settle_challenge(page)

                body = await page.content()
                verdict = classify(code, body)
                detail = (await page.title() or "")[:80].replace(",", " ")
                pages = 1

                # A throttled or still-walled read tells us nothing about the
                # site. Back off and try once more rather than record a lie.
                if verdict in ("THROTTLED", "JS_WALL") and attempt + 1 < MAX_ATTEMPTS:
                    await context.close()
                    context = None
                    await asyncio.sleep(random.uniform(6, 14))
                    continue

                if verdict == "ALIVE":
                    await read_like_a_person(page)

                    plugin_res, rendered = None, None
                    if plugin_token:
                        plugin_res = await ping_plugin(page, plugin_token)

                    # If a post was just created, read that instead of a random
                    # existing page: it is a real page view AND it proves the
                    # post serves, not merely that the write returned ok.
                    if plugin_res and plugin_res.get("url"):
                        await human_pause(1.0, 3.0)
                        rendered = await verify_published_post(page, plugin_res)
                        if rendered == "ok":
                            await read_like_a_person(page)
                            pages = 2

                    plugin = summarise_plugin(plugin_res, rendered)

                    if pages < 2:
                        link = await pick_internal_link(page, host)
                        if link:
                            await human_pause(1.0, 3.0)
                            try:
                                await page.goto(link, wait_until="domcontentloaded",
                                                timeout=NAV_TIMEOUT_MS)
                                await settle_challenge(page)
                                await read_like_a_person(page)
                                pages = 2
                            except Exception:
                                pass  # Second page is a bonus, not a requirement.
                break

            except Exception as exc:
                verdict, detail = "UNREACHABLE", type(exc).__name__
                if attempt + 1 < MAX_ATTEMPTS:
                    await asyncio.sleep(random.uniform(4, 10))
            finally:
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        pass

        results.append(row(host, verdict, code, pages, detail, "browser", plugin))
        flag = " " if verdict == "ALIVE" else ("?" if is_inconclusive(verdict) else "!")
        print("%s %-34s %-16s pages=%d" % (flag, host, verdict, pages), flush=True)


def row(host, verdict, code, pages, detail, checked_by, plugin=""):
    return {
        "host": host,
        "verdict": verdict,
        "http_code": code,
        "pages_viewed": pages,
        "detail": detail,
        "checked_by": checked_by,
        "plugin": plugin,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


def confirm_parked(host):
    """Second opinion on a host whose DNS points at the parking address.

    DNS alone must never condemn a site, so one cheap HTTP fetch has to agree
    before we skip the browser. Anything other than a confirmed dead-account
    page sends the host back to the full browser path, so a renumbered parking
    IP or a stale resolver costs a wasted visit rather than a lost site.

    Spaced out and retried, because iFastNet throttles bursts hard: fired back to
    back, most of these come back as undersized THROTTLED bodies and every one of
    them then costs the browser visit this filter exists to avoid.
    """
    last = "UNKNOWN"
    for attempt in range(CONFIRM_ATTEMPTS):
        time.sleep(random.uniform(0.3, 1.5))
        try:
            resp = requests.get("http://%s/" % host, headers={"User-Agent": UA},
                                timeout=15, allow_redirects=True)
        except Exception as exc:
            last = type(exc).__name__
            continue
        verdict = classify(resp.status_code, resp.text, len(resp.content))
        if verdict == "DEAD_NO_ACCOUNT":
            return verdict, resp.status_code
        last = verdict
        if verdict != "THROTTLED":
            break  # A real, different answer. Hand it to the browser.
        time.sleep(random.uniform(3, 7))
    return None, last


def prefilter(hosts, workers):
    """Resolve, confirm, and return (rows_for_dead_hosts, hosts_still_to_visit)."""
    parked, full, _ = dnsfilter.partition(hosts, workers=max(workers * 4, 8))
    if not parked:
        return [], full

    print("dns pre-filter: %d of %d on the parking address, confirming"
          % (len(parked), len(hosts)), flush=True)

    rows, disagreed = [], []
    with ThreadPoolExecutor(max_workers=max(workers, 2)) as pool:
        for host, (verdict, info) in zip(parked, pool.map(confirm_parked, parked)):
            if verdict:
                rows.append(row(host, verdict, info, 0, "no hosting account", "dns+http"))
            else:
                disagreed.append(host)

    if disagreed:
        # Worth noticing: either iFastNet changed something, or DNS lied.
        print("dns pre-filter: %d host(s) did NOT confirm, visiting them properly"
              % len(disagreed), flush=True)
    print("dns pre-filter: skipped %d browser visit(s)" % len(rows), flush=True)
    return rows, full + disagreed


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default="sites.txt")
    ap.add_argument("--out", required=True)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="only check the first N sites")
    ap.add_argument("--no-dns-prefilter", action="store_true",
                    help="visit every host with the browser, even known-dead ones")
    ap.add_argument("--plugin-token", default=os.environ.get("GHKA_TOKEN", ""),
                    help="token for the GH Keep-Alive WordPress plugin; "
                         "without it the plugin is never pinged")
    args = ap.parse_args()

    hosts = load_hosts(args.sites, args.limit)

    mine = [h for i, h in enumerate(hosts) if i % args.shards == args.shard]
    random.shuffle(mine)

    print("shard %d/%d: %d of %d sites, concurrency %d, plugin ping %s"
          % (args.shard, args.shards, len(mine), len(hosts), args.concurrency,
             "on" if args.plugin_token else "off"), flush=True)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    cols = ["host", "verdict", "http_code", "pages_viewed", "detail", "checked_by",
            "plugin", "checked_at"]

    results = []
    if mine and not args.no_dns_prefilter:
        skipped, mine = prefilter(mine, args.concurrency)
        results.extend(skipped)

    if mine:
        sem = asyncio.Semaphore(args.concurrency)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
            try:
                await asyncio.gather(*(visit(browser, h, sem, results,
                                             args.plugin_token) for h in mine))
            finally:
                await browser.close()

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(results)
    print("wrote %d rows -> %s" % (len(results), args.out), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
