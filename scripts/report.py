"""Merge shard results, track every site over time, and shout early.

The point of this file is the recovery window. InfinityFree deactivates an
account for inactivity, then deletes it permanently 18 or more days later, and a
deleted account can never be recovered. So the only thing that actually saves a
site is noticing it went down while it is still only deactivated.

The old report.csv could not do that: it kept one line per run with no per-site
memory, and it counted a dead account as a success. This keeps per-site state so
"went down 4 days ago, still recoverable" is a thing the report can say.
"""

import argparse
import csv
import glob
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classify import is_inconclusive  # noqa: E402
from sitelist import norm_host  # noqa: E402

STATE_COLS = ["host", "first_seen", "last_alive", "last_verdict", "last_checked",
              "consecutive_fail", "down_since", "total_checks", "total_alive",
              "plugin_state", "plugin_posts", "plugin_rendered"]


def parse_plugin_cell(cell):
    """Unpack what keepalive.py wrote, e.g.

        "published posts=5 gen=2 rendered=ok"  ->  state/posts/gen/rendered
        "absent"                               ->  state only
    """
    out = {"state": "", "posts": None, "gen": None, "rendered": ""}
    cell = (cell or "").strip()
    if not cell:
        return out
    bits = cell.split()
    out["state"] = bits[0]
    for bit in bits[1:]:
        if "=" not in bit:
            continue
        key, _, value = bit.partition("=")
        if key in ("posts", "gen"):
            try:
                out[key] = int(value)
            except ValueError:
                pass
        elif key == "rendered":
            out["rendered"] = value
    return out

# Conservative reading of the published deletion timeline.
RECOVERY_DAYS = 18      # inside this, reactivating from the client area works
LIKELY_LOST_DAYS = 60   # past this, assume the account is gone for good

TS = "%Y-%m-%d %H:%M:%S"


def now_utc():
    return datetime.now(timezone.utc).strftime(TS)


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, TS).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def days_since(value, ref):
    stamp = parse_ts(value)
    if stamp is None:
        return None
    return (ref - stamp).days


def load_state(path):
    state = {}
    if not os.path.exists(path):
        return state
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            host = (row.get("host") or "").strip()
            if host:
                state[host] = row
    return state


def load_results(pattern):
    """Last write wins, so a retried shard supersedes the earlier attempt."""
    results = {}
    for path in sorted(glob.glob(pattern)):
        if not os.path.getsize(path):
            continue
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                host = (row.get("host") or "").strip()
                if host:
                    results[host] = row
    return results


def plugin_problem(entry, parsed):
    """Return a complaint about this run's plugin result, or None.

    The point is to catch a plugin that reports success while nothing actually
    changes on the site. A REST reply saying "published" only proves the write
    returned; the post count going up across runs proves it stuck.
    """
    state = parsed["state"]
    if not state:
        return None
    if state == "absent":
        return "plugin not installed or not activated"
    if state == "bad_token":
        return "site token does not match GHKA_TOKEN"
    if state in ("not_json", "error", "unknown"):
        return "plugin replied oddly (%s)" % state
    if state == "published":
        if parsed["rendered"] and parsed["rendered"] != "ok":
            return "post created but the page does not load (%s)" % parsed["rendered"]
        was = entry.get("plugin_posts")
        try:
            was = int(was)
        except (TypeError, ValueError):
            was = None
        if was is not None and parsed["posts"] is not None and parsed["posts"] <= was:
            return ("reported published but the post count did not rise (%s then, %s now)"
                    % (was, parsed["posts"]))
    return None


def apply_result(entry, row, stamp):
    verdict = row.get("verdict") or "UNKNOWN"
    entry["last_verdict"] = verdict
    entry["last_checked"] = stamp
    entry["total_checks"] = str(int(entry.get("total_checks") or 0) + 1)

    if verdict == "ALIVE":
        entry["last_alive"] = stamp
        entry["consecutive_fail"] = "0"
        entry["down_since"] = ""
        entry["total_alive"] = str(int(entry.get("total_alive") or 0) + 1)
        return

    if is_inconclusive(verdict):
        # We learned nothing. Never let our own throttling or a timeout push a
        # healthy site toward being written off.
        return

    entry["consecutive_fail"] = str(int(entry.get("consecutive_fail") or 0) + 1)
    if not entry.get("down_since"):
        entry["down_since"] = stamp


def bucket(entry, ref):
    """Where this site sits relative to the recovery window.

    The two failure verdicts mean very different things:

      SUSPENDED        the account exists and is switched off. Log in, reactivate,
                       done. Always worth acting on, whatever the history.
      DEAD_NO_ACCOUNT  no account is attached to the domain at all. There is
                       nothing to reactivate.

    So a DEAD_NO_ACCOUNT host we have never once seen alive was already gone
    before this repo ever looked at it - its down_since is just the day we first
    checked, not the day it died. Calling those "recover now" would have put
    thousands of long-dead domains at the top of the action list on the first
    run and buried the handful that could still be saved.
    """
    verdict = entry.get("last_verdict") or "UNKNOWN"
    if verdict == "ALIVE":
        return "OK"
    if is_inconclusive(verdict):
        return "UNKNOWN"

    if verdict == "DEAD_NO_ACCOUNT" and not entry.get("last_alive"):
        return "LIKELY_LOST"

    age = days_since(entry.get("down_since") or entry.get("last_checked"), ref)
    if age is None:
        return "UNKNOWN"
    if age <= RECOVERY_DAYS:
        return "RECOVER_NOW"
    if age <= LIKELY_LOST_DAYS:
        return "AT_RISK"
    return "LIKELY_LOST"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results/shard-*.csv")
    ap.add_argument("--sites", default="sites.txt")
    ap.add_argument("--state", default="state/site_state.csv")
    ap.add_argument("--history", default="state/history.csv")
    ap.add_argument("--attention", default="reports/ATTENTION.md")
    ap.add_argument("--alive-list", default="reports/alive.txt")
    ap.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""))
    args = ap.parse_args()

    ref = datetime.now(timezone.utc)
    stamp = now_utc()

    results = load_results(args.results)
    if not results:
        print("ERROR: no shard results found at %s" % args.results, file=sys.stderr)
        return 2

    state = load_state(args.state)

    listed = set()
    if os.path.exists(args.sites):
        with open(args.sites, encoding="utf-8", errors="replace") as f:
            listed = {h for h in (norm_host(line) for line in f) if h}

    for host, row in results.items():
        entry = state.get(host)
        if entry is None:
            entry = {c: "" for c in STATE_COLS}
            entry["host"] = host
            entry["first_seen"] = stamp
            entry["consecutive_fail"] = "0"
            entry["total_checks"] = "0"
            entry["total_alive"] = "0"
            state[host] = entry
        apply_result(entry, row, stamp)

    # ---- buckets -----------------------------------------------------------
    buckets = {"OK": [], "RECOVER_NOW": [], "AT_RISK": [], "LIKELY_LOST": [],
               "UNKNOWN": []}
    for host in results:
        buckets[bucket(state[host], ref)].append(host)

    checked = len(results)
    ok = len(buckets["OK"])
    verdict_counts = {}
    for host in results:
        v = state[host]["last_verdict"]
        verdict_counts[v] = verdict_counts.get(v, 0) + 1

    # What the WordPress plugin reported from inside each site, if installed.
    # This is the only view we have of the site's own state - post count, whether
    # it actually published - rather than what a page load looks like outside.
    #
    # Compare against last run BEFORE the new numbers are stored, so a site that
    # claims to publish while its post count stays flat gets noticed.
    plugin_states, plugin_issues = {}, []
    for host, result in results.items():
        parsed = parse_plugin_cell(result.get("plugin"))
        if not parsed["state"]:
            continue
        plugin_states[parsed["state"]] = plugin_states.get(parsed["state"], 0) + 1

        complaint = plugin_problem(state.get(host, {}), parsed)
        if complaint:
            plugin_issues.append((host, complaint))

        entry = state[host]
        entry["plugin_state"] = parsed["state"]
        entry["plugin_rendered"] = parsed["rendered"]
        if parsed["posts"] is not None:
            entry["plugin_posts"] = str(parsed["posts"])

    # New failures this run: went down within the last day.
    newly_down = [h for h in buckets["RECOVER_NOW"]
                  if (days_since(state[h].get("down_since"), ref) or 0) <= 1]

    # ---- write state -------------------------------------------------------
    os.makedirs(os.path.dirname(os.path.abspath(args.state)) or ".", exist_ok=True)
    with open(args.state, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=STATE_COLS, extrasaction="ignore")
        writer.writeheader()
        for host in sorted(state):
            writer.writerow({c: state[host].get(c, "") for c in STATE_COLS})

    history_exists = os.path.exists(args.history)
    with open(args.history, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not history_exists:
            writer.writerow(["run_at", "checked", "alive", "recover_now", "at_risk",
                             "likely_lost", "unknown", "newly_down"])
        writer.writerow([stamp, checked, ok, len(buckets["RECOVER_NOW"]),
                         len(buckets["AT_RISK"]), len(buckets["LIKELY_LOST"]),
                         len(buckets["UNKNOWN"]), len(newly_down)])

    # ---- write the actionable report --------------------------------------
    os.makedirs(os.path.dirname(os.path.abspath(args.attention)) or ".", exist_ok=True)
    lines = ["# Sites needing attention", "",
             "Generated %s UTC" % stamp, "",
             "| bucket | count | meaning |", "|---|---|---|",
             "| ALIVE | %d | nothing to do |" % ok,
             "| RECOVER NOW | %d | down <= %d days: log in and reactivate, still saveable |"
             % (len(buckets["RECOVER_NOW"]), RECOVERY_DAYS),
             "| AT RISK | %d | down %d-%d days: may already be deleted, try anyway |"
             % (len(buckets["AT_RISK"]), RECOVERY_DAYS, LIKELY_LOST_DAYS),
             "| LIKELY LOST | %d | down > %d days: account almost certainly deleted |"
             % (len(buckets["LIKELY_LOST"]), LIKELY_LOST_DAYS),
             "| UNKNOWN | %d | check was inconclusive, not counted against the site |"
             % len(buckets["UNKNOWN"]),
             ""]

    if buckets["RECOVER_NOW"]:
        lines += ["## Act on these now", "",
                  "Log in to the InfinityFree client area and reactivate. After "
                  "reactivating, run this workflow manually so the site gets a visit "
                  "immediately.", "",
                  "| host | verdict | down since | days down |", "|---|---|---|---|"]
        for host in sorted(buckets["RECOVER_NOW"],
                           key=lambda h: state[h].get("down_since") or ""):
            entry = state[host]
            lines.append("| %s | %s | %s | %s |" % (
                host, entry["last_verdict"], entry.get("down_since") or "?",
                days_since(entry.get("down_since"), ref)))
        lines.append("")

    for name, title in (("AT_RISK", "At risk (try reactivating, may be too late)"),
                        ("LIKELY_LOST", "Likely lost (remove from sites.txt)"),
                        ("UNKNOWN", "Inconclusive this run (no action)")):
        if buckets[name]:
            lines += ["## %s" % title, ""]
            for host in sorted(buckets[name]):
                lines.append("- %s  (%s)" % (host, state[host]["last_verdict"]))
            lines.append("")

    if plugin_issues:
        lines += ["## Plugin problems", "",
                  "These sites are alive, but the keep-alive plugin is not doing "
                  "its job. Nothing is at risk today; publishing just is not "
                  "working there.", "",
                  "| host | problem |", "|---|---|"]
        lines += ["| %s | %s |" % pair for pair in sorted(plugin_issues)]
        lines.append("")

    stale = sorted(h for h in state
                   if h not in listed and h not in results and listed)
    if stale:
        lines += ["## In state but not in sites.txt", "",
                  "These are no longer being visited.", ""]
        lines += ["- %s" % h for h in stale] + [""]

    with open(args.attention, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    os.makedirs(os.path.dirname(os.path.abspath(args.alive_list)) or ".", exist_ok=True)
    with open(args.alive_list, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(buckets["OK"])) + ("\n" if buckets["OK"] else ""))

    # ---- console + Actions summary ----------------------------------------
    report = ["checked            %d" % checked,
              "alive              %d" % ok,
              "RECOVER NOW        %d" % len(buckets["RECOVER_NOW"]),
              "at risk            %d" % len(buckets["AT_RISK"]),
              "likely lost        %d" % len(buckets["LIKELY_LOST"]),
              "inconclusive       %d" % len(buckets["UNKNOWN"]),
              "newly down         %d" % len(newly_down),
              "",
              "verdicts: " + ", ".join("%s=%d" % kv for kv in
                                       sorted(verdict_counts.items()))]
    if plugin_states:
        report += ["plugin:   " + ", ".join("%s=%d" % kv
                                            for kv in sorted(plugin_states.items()))]
    if plugin_issues:
        report += ["plugin problems: %d (see %s)" % (len(plugin_issues), args.attention)]
    print("\n".join(report))

    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write("## Keep-alive run %s UTC\n\n" % stamp)
            f.write("| metric | count |\n|---|---|\n")
            f.write("| checked | %d |\n| alive | %d |\n" % (checked, ok))
            f.write("| **RECOVER NOW** | **%d** |\n" % len(buckets["RECOVER_NOW"]))
            f.write("| at risk | %d |\n| likely lost | %d |\n| inconclusive | %d |\n"
                    % (len(buckets["AT_RISK"]), len(buckets["LIKELY_LOST"]),
                       len(buckets["UNKNOWN"])))
            if plugin_states:
                f.write("| plugin | %s |\n"
                        % ", ".join("%s %d" % kv
                                    for kv in sorted(plugin_states.items())))
            if plugin_issues:
                f.write("| **plugin problems** | **%d** |\n" % len(plugin_issues))
            if newly_down:
                f.write("\n### Went down since the last run\n\n")
                for host in sorted(newly_down):
                    f.write("- `%s` - %s\n" % (host, state[host]["last_verdict"]))
                f.write("\nReactivate these from the client area within %d days.\n"
                        % RECOVERY_DAYS)

    # Stay red for as long as anything is still saveable, not just on the run
    # where it first broke.
    #
    # Alerting only on newly_down was a real hole: a site that went down five
    # days ago is still inside the recovery window and still needs a hand, but
    # it is no longer "new". The run would go green, and someone who missed the
    # single first email would see nothing but green for the remaining
    # seventeen days while the account was quietly deleted. That is exactly the
    # silent failure this repo exists to prevent.
    if buckets["RECOVER_NOW"]:
        print("\n%d site(s) down and still recoverable (%d since the last run). See %s"
              % (len(buckets["RECOVER_NOW"]), len(newly_down), args.attention),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
