# Legacy system (not running)

Kept for reference only. Nothing here is executed.

- `ping_sites.py` - the original `requests`-based pinger.
- `main.yml.disabled` - its GitHub Actions workflow. Renamed out of
  `.github/workflows/` so it cannot trigger. Two workflows pushing to the same
  branch would fight over `state/`, and this one spoofed a Googlebot user agent
  from data centre IPs, which is what the new system deliberately stopped doing.
  To re-enable it (you should not need to), move it back and rename to `.yml`.
- `report.csv` - the original per-run summary, 2026-04-06 to 2026-08-25. Real
  history, worth keeping: it shows the collapse from 5,592 successes down to
  ~1,250, and the July window where 4,933 sites reported as suspended.
- `detailed_status.csv` - the last run's per-site rows. Read it knowing that
  roughly 4,000 of its 4,334 "failures" are the script checking a hardcoded
  `/about-us`, `/contact-us` or `/privacy-policy` that never existed, and that
  its "Success" count included thousands of already-deleted accounts.
