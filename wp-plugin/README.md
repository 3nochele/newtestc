# GH Keep-Alive (WordPress plugin)

Publishes a post when the keep-alive workflow pings it, and reports what the
site looks like from the inside.

## Why a plugin at all

A visit from outside proves a page loaded. A post written by the site itself
runs PHP, writes to the database, and leaves a dated public page behind. That is
much harder for a host to read as an idle account.

It also solves a problem nothing outside can: the workflow can now see the
site's real post count, its last post date, and its WordPress and PHP versions.
Before this, "alive" only ever meant "the homepage rendered".

And it needs no credentials. No WordPress password in GitHub Secrets, no
Application Passwords to generate on 171 sites. Just one shared token.

## Setup

**1. Pick a token.** One long random string, the same on every site:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**2. Put it in the plugin.** Open `gh-keepalive/gh-keepalive.php` and replace the
placeholder on the `GHKA_TOKEN` line:

```php
define('GHKA_TOKEN', 'paste-your-token-here');
```

**3. Zip the folder.** The zip must contain the `gh-keepalive` folder itself, not
just the php file inside it:

```bash
cd wp-plugin && zip -r gh-keepalive.zip gh-keepalive
```

**4. Install on each site.** WordPress admin → Plugins → Add New → Upload Plugin
→ choose the zip → Install → **Activate**.

**5. Add the token to GitHub.** Repository → Settings → Secrets and variables →
Actions → New repository secret. Name it `GHKA_TOKEN`, paste the same string.

That is all. The next workflow run will start pinging it.

## Checking it worked

The run's summary and `reports/ATTENTION.md` show a `plugin` line:

| what you see | meaning |
|---|---|
| `published posts=5 gen=2 rendered=ok` | a post was created, and opening it worked |
| `rendered=http_404` (or similar) | it published, but the post does not load |
| `skipped` | a guard is switched on and stopped it. Off by default |
| `absent` | the plugin is not installed or not activated on that site |
| `bad_token` | the site's token and the GitHub secret do not match |
| `not_json` | something answered, but not the plugin. Check the site |

`posts` is the site's total published posts and `gen` is how many this plugin
made. The workflow opens each new post in the browser to confirm it really
serves, and compares `posts` against the previous run — so a plugin that reports
success while nothing actually changes gets caught. Anything wrong is listed
under **Plugin problems** in `reports/ATTENTION.md`.

To check one site by hand without publishing anything, open this in a browser
tab that has already loaded the site once:

```
http://yoursite.example/wp-json/ghka/v1/status?token=YOUR_TOKEN
```

It must be a browser. A plain `curl` gets the host's JavaScript security page
instead of the API, which is exactly why the workflow pings from inside a real
browser rather than from Python.

## Settings

All optional. Add to `wp-config.php` to override per site, or edit the defaults
in the plugin file:

| constant | default | what it does |
|---|---|---|
| `GHKA_TOKEN` | placeholder | shared secret. Must match the GitHub secret |
| `GHKA_MIN_DAYS_BETWEEN_POSTS` | `0` (off) | refuse to publish again before this many days |
| `GHKA_MAX_POSTS` | `0` (off) | lifetime ceiling on generated posts |

**By default there are no limits: every ping publishes, forever.**

The two guards are still in the code because they are cheap insurance against a
runaway, not policy. A bad schedule or a retry loop can ping far more often than
intended, and a free account has finite disk, database size and inodes. Filling
those up gets an account suspended for resource abuse — a worse outcome than the
inactivity this plugin is meant to prevent. If you ever want that seatbelt back:

```php
define('GHKA_MIN_DAYS_BETWEEN_POSTS', 25);
define('GHKA_MAX_POSTS', 500);
```

## What it will and will not do

It only ever **creates** posts. It never edits or deletes existing content,
never touches settings, other plugins' options, users, or files. With a wrong
token it does nothing at all.

The token travels over plain HTTP, since these sites are not on HTTPS, so treat
it as a "please do the harmless thing" key rather than a password. The worst a
leaked token can do is cause an unwanted demo post, bounded by the two limits
above.

## Changing what it publishes

`ghka_compose_post()` is the only place content is decided. Replace that one
function to publish something real; nothing else in the plugin needs to change.

Worth knowing: the built-in demo text creates *activity*, not *audience*. Google
will not send visitors to placeholder posts, so this keeps the account looking
used without making the site worth visiting. If the goal is real traffic, real
content has to go in that function.

## Tests

```bash
php -l wp-plugin/gh-keepalive/gh-keepalive.php
php wp-plugin/test_plugin.php unlimited
php wp-plugin/test_plugin.php limited
```

`test_plugin.php` stubs WordPress out and exercises the real handlers: the token
gate, that `status` never writes, that a refused request publishes nothing, that
the default really does publish on every ping with no ceiling, and that the
optional guards still hold when switched on. PHP constants can only be set once
per process, which is why the two configurations run as separate invocations.
All of it runs in CI on every push.
