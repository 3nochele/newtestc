# Site keep-alive

Keeps free InfinityFree / iFastNet hosting accounts from being deactivated for
inactivity, and tells you loudly and early when one goes down.

## The one thing to understand

InfinityFree does not delete an account the moment it goes idle. The sequence is:

```
no visitors for 30 days  ->  DEACTIVATED  ->  (18+ days)  ->  DELETED FOREVER
                                  ^                              ^
                          still recoverable              nothing can be done,
                          from the client area           subdomain is released
```

So the thing that actually saves a site is **noticing it went down while it is
still only deactivated**. Everything in this repo is built around that window.

The previous version of this repo could not do that. It reported one summary
line per run with no per-site memory, and it counted a dead account as a
success, because iFastNet serves its "DNS Resolution Error - Domain Not Found,
Or Domain not added to hosting account" page with **HTTP 200**. An audit on
2026-08-27 found 5,424 of 5,595 sites already permanently gone, while the report
had been claiming roughly 1,250 successes every run.

## How the visit is made to count

InfinityFree's browser security system only lets through clients that execute
JavaScript and accept cookies. That leaves a plain HTTP client with two options,
both useless:

| approach | gets the HTML | counts as a visitor |
|---|---|---|
| `requests` with a normal user agent | no, stopped at the JS wall | no |
| `requests` faking a Googlebot user agent | yes | almost certainly not |
| **real Chromium via Playwright** | **yes** | **yes** |

So `keepalive.py` drives a real headless Chromium: it solves the challenge the
way a person's browser does, loads every asset, scrolls, and then follows one
link that the site actually has — two genuine page views per site. It sends an
ordinary Chrome user agent, because these are your own sites and there is no
reason to impersonate a crawler.

The bar is low: InfinityFree staff have said **five visitors in any 30-day
window** is enough, and visiting your own site counts. The schedule below gives
six runs a month at two page views each, so a failed run or two costs nothing.

## Files

| path | what it is |
|---|---|
| `sites.txt` | your list, one host per line. `http://` prefixes, paths and trailing spaces are fine |
| `scripts/keepalive.py` | the real-browser visitor. This is what keeps accounts alive |
| `scripts/audit.py` | fast browser-free triage. Tells account-exists from account-gone, **cannot** see suspension. Does not keep anything alive |
| `scripts/dnsfilter.py` | the DNS pre-filter that keeps dead domains from costing a browser visit |
| `wp-plugin/gh-keepalive/` | optional WordPress plugin: publishes on ping, reports the site's real state. See [its README](wp-plugin/README.md) |
| `scripts/report.py` | merges results, tracks per-site state, writes the alert |
| `scripts/classify.py` | one place that decides what a response means |
| `scripts/sitelist.py` | site-list parsing |
| `scripts/test_classify.py` | regression tests. Run before changing the classifier |
| `state/site_state.csv` | per-site memory: first seen, last alive, days down, fail streak |
| `state/history.csv` | one row per run, appended forever |
| `reports/ATTENTION.md` | **read this one.** What to act on, sorted by urgency |
| `reports/alive.txt` | hosts confirmed alive in the last run |
| `legacy/` | the old script and its reports, kept for reference. Not run |

## Adding your new list

1. Put one host per line in `sites.txt` and commit it. Junk lines (blanks,
   comments, entries with no dot) are skipped automatically, so you do not need
   to clean the file by hand.
2. Go to **Actions -> Keep sites alive -> Run workflow**. Set `limit` to `20`
   for the first run to confirm everything works, then run again with `0`.
3. Check `reports/ATTENTION.md` on the branch afterwards.

To scale, raise `shards`. Budget roughly **15 seconds per site per shard**:

| sites | shards | rough wall clock |
|---|---|---|
| 200 | 2 | 15 min |
| 1,000 | 4 | 20 min |
| 5,000 | 8 | 50 min |

Do not raise `concurrency` much past 4. During development, scanning at 24
parallel requests got this project's IP throttled by iFastNet, which returned
tiny 555-byte 404s for thousands of healthy hosts and then blocked the IP
outright. Being slow is the point.

## The WordPress plugin (optional, and the strongest signal available)

A visit proves a page loaded. A post the site writes itself runs PHP, writes to
the database, and leaves a dated public page behind — much harder to read as an
idle account.

`wp-plugin/gh-keepalive/` installs on each site and exposes one token-protected
endpoint. After the browser has solved the security wall to load the homepage,
the workflow calls that endpoint *from inside the page* — a plain HTTP client
gets the challenge page instead of the API, which is why this only works from
the real browser we are already running.

It also reports what nothing outside the site can see: real post count, last
post date, WordPress and PHP versions. Before this, "alive" only ever meant "the
homepage rendered".

Set-up is in [wp-plugin/README.md](wp-plugin/README.md). Short version: put a
random token in the plugin file, zip and install it on each site, and add the
same string as the `GHKA_TOKEN` repository secret. Skip all of it and nothing
breaks — visits carry on exactly as before, the plugin column just reads
`absent`.

The plugin only ever creates posts. It never edits or deletes anything, and a
wrong token makes it do nothing at all.

### Checking it actually worked

A REST reply saying `published` only proves the database write returned without
an error. Two things check that it stuck:

- **The new post is opened in the browser.** When the plugin returns a post URL,
  that URL becomes the run's second page view instead of a random existing link:
  a real view on a fresh page, and proof the post serves rather than 404s. The
  result lands in the `plugin` column as `rendered=ok` or, say,
  `rendered=http_404`.
- **The post count is compared against last run.** `state/site_state.csv` keeps
  each site's `plugin_posts`. If a site reports `published` but its count has not
  risen since the previous run, that is reported as a problem — the exact shape
  of failure where an API answers cheerfully while nothing changes on the site.

Anything wrong shows up under **Plugin problems** in `reports/ATTENTION.md`:

| what you see | meaning |
|---|---|
| `plugin not installed or not activated` | the plugin is missing on that site |
| `site token does not match GHKA_TOKEN` | the site's token and the secret differ |
| `post created but the page does not load (…)` | it published, but the post 404s |
| `reported published but the post count did not rise (…)` | the API claims success, the site disagrees |

These never mark a site as down. The site is alive; publishing just is not
working there.

## The DNS pre-filter

Every run first resolves its hosts, which takes about 15ms each, and any host
sitting on iFastNet's parking address has no hosting account behind it. Those
get one cheap HTTP fetch to confirm, and then skip the browser entirely.

Measured on this project's own data:

| | on the parking IP `185.27.134.24` | elsewhere |
|---|---|---|
| 450 known-dead hosts | 450 (100%) | 0 |
| 152 known-alive hosts | **0** | 152, scattered across many IPs |

The reason this matters is not speed, it is **not getting blocked**. A list with
thousands of dead domains would otherwise throw thousands of pointless requests
at iFastNet in a single run. That is exactly how this project's own IP got
blocked during development - and a blocked runner cannot reach the *live* sites
either, so they would quietly starve of visits and die. The filter removes the
pointless traffic.

Two safety rules are built in, because getting this wrong would cause the very
failure the repo exists to prevent:

- **DNS never condemns a site on its own.** A parked result still needs an HTTP
  fetch to agree before the browser is skipped. If iFastNet renumbers, or a
  resolver answers stale, the host falls through to a full browser visit. Worst
  case is a wasted visit, never a live site dropped.
- **Unresolvable means unknown, not dead.** A resolver outage sends every host
  down the full path rather than skipping them all.

Turn it off with `--no-dns-prefilter` if you ever want to force a browser visit
for everything.

One wrinkle worth knowing: the confirmation fetches are deliberately spaced and
retried. Fired back to back, iFastNet throttles them and returns undersized
bodies, and every throttled host then costs the browser visit the filter was
supposed to save.

## When you get an alert

The workflow **fails on purpose** when a site newly goes down, so GitHub emails
you a red X instead of the news sitting quietly in a CSV. On an alert:

1. Open `reports/ATTENTION.md`. The **Act on these now** table lists sites that
   are still inside the recovery window, oldest first.
2. Log in to the InfinityFree client area and reactivate those accounts.
3. Re-run the workflow manually so they get a visit immediately, which restarts
   their 30-day clock.

Buckets, in priority order:

| bucket | meaning | action |
|---|---|---|
| `RECOVER NOW` | down 18 days or less | reactivate today, it will work |
| `AT RISK` | down 19-60 days | try anyway, may already be gone |
| `LIKELY LOST` | down over 60 days | remove from `sites.txt` |
| `UNKNOWN` | the check was inconclusive | nothing. Deliberately not held against the site |

That last bucket matters. Timeouts, throttling and unsolved challenges never
increment a fail streak and never open a `down_since`, because a network problem
on our side is not evidence about the site. During development an IP block made
150 healthy sites unreachable at once; the state file correctly recorded nothing
against any of them.

## Running locally

```bash
pip install -r requirements.txt
python -m playwright install --with-deps chromium
python scripts/test_classify.py
python scripts/audit.py --sites sites.txt --limit 20 --concurrency 4
python scripts/keepalive.py --sites sites.txt --out results/shard-0.csv --limit 5
python scripts/report.py --results 'results/shard-*.csv'
```

## What this cannot promise

Being straight about the limits, because the last version of this repo failed
quietly for five months:

- **Nobody outside InfinityFree can confirm what their inactivity counter
  measures.** A real browser visit is the closest thing to a real visitor that
  can be automated, and it is far better than what was here before, but it is
  not a documented guarantee. The early-warning half of this repo exists exactly
  because the keep-alive half might not be enough.
- **All traffic still comes from GitHub Actions runners**, i.e. Azure data
  centre IPs. Real browser, real JS, real assets, staggered timing - but not
  residential addresses. If you want to go further you would need to route
  through a proxy you supply.
- **Free hosting reclaiming idle accounts is their business model, not a bug.**
  A script can keep an account above the activity line; it cannot make a free
  account permanent. If these sites carry real value, a smaller number on paid
  hosting will outlive a large number of free ones.
- **The classifier reads iFastNet's error pages by their wording.** If they
  reword those pages, verdicts drift. `scripts/test_classify.py` holds the
  current wording as fixtures; update it and the markers in `classify.py`
  together.
