<?php
/**
 * Plugin Name:       GH Keep-Alive
 * Description:       Publishes a post when the keep-alive workflow pings it, and reports the site's real state back. Keeps free hosting accounts from being deactivated for inactivity.
 * Version:           1.0.0
 * Requires at least: 5.6
 * Requires PHP:      7.0
 * License:           GPL-2.0-or-later
 *
 * WHY THIS EXISTS
 * ---------------
 * The host deactivates accounts that go 30 days without visitors, then deletes
 * them permanently about 18 days later. A visit from outside helps, but a post
 * written by the site itself is stronger evidence of a working site: it runs
 * PHP, writes to the database, and leaves a dated, public page behind.
 *
 * It also lets the workflow see inside. From outside you can only tell that a
 * page loaded. This reports the post count, the last post date, and the PHP and
 * WordPress versions, so the report can say what is actually going on.
 *
 * SAFETY
 * ------
 * This plugin only ever CREATES posts. It never edits or deletes existing
 * content, never touches settings, options belonging to other plugins, users,
 * or files. If the token is wrong it does nothing at all. Worst case for a
 * leaked token is an unwanted demo post, capped by the limits below.
 */

if (!defined('ABSPATH')) {
    exit; // Never reachable directly.
}

/*
 * ---------------------------------------------------------------------------
 * The shared secret. The SAME value must be set as the GitHub repository
 * secret named GHKA_TOKEN, or the workflow's pings will be refused.
 *
 * Because this value lives in the plugin file, KEEP THE GITHUB REPOSITORY
 * PRIVATE. A public repo would publish it.
 *
 * It also travels over plain HTTP, since these sites are not on HTTPS, so
 * treat it as a "please do the harmless thing" key rather than a password.
 * That is the reason this plugin can only ever create posts.
 *
 * To rotate it: change it here, re-zip, reinstall on every site, and update
 * the GitHub secret to match.
 * ---------------------------------------------------------------------------
 */
if (!defined('GHKA_TOKEN')) {
    define('GHKA_TOKEN', 'j_8mg4-DFSmCEqM6bz3GgAeaAIb_INzCS7bwMt8Ux0I');
}

/*
 * Both limits are OFF by default: every ping publishes, forever.
 *
 * They are still here because they are cheap insurance, not policy. A bad
 * schedule or a retry loop can ping far more often than intended, and a free
 * account has finite disk, database size and inodes. Filling those up gets an
 * account suspended for resource abuse, which is a worse outcome than the
 * inactivity this plugin exists to prevent.
 *
 * Set either one in wp-config.php to switch that guard back on. 0 means "no
 * limit" for both.
 */
if (!defined('GHKA_MIN_DAYS_BETWEEN_POSTS')) {
    define('GHKA_MIN_DAYS_BETWEEN_POSTS', 0);
}

if (!defined('GHKA_MAX_POSTS')) {
    define('GHKA_MAX_POSTS', 0);
}

const GHKA_OPT_LAST  = 'ghka_last_published';
const GHKA_OPT_COUNT = 'ghka_created_count';
const GHKA_META_FLAG = '_ghka_generated';

/* -------------------------------------------------------------------------
 * Routes
 * ---------------------------------------------------------------------- */

add_action('rest_api_init', 'ghka_register_routes');

function ghka_register_routes() {
    $args = array(
        'methods'             => 'GET, POST',
        'permission_callback' => '__return_true', // Token is checked in the handler.
        'args'                => array(
            'token' => array('type' => 'string', 'required' => false),
        ),
    );

    register_rest_route('ghka/v1', '/ping',   $args + array('callback' => 'ghka_ping'));
    register_rest_route('ghka/v1', '/status', $args + array('callback' => 'ghka_status'));
}

/**
 * Accept the token from a header or a query parameter.
 * hash_equals keeps the comparison constant-time.
 */
function ghka_token_ok(WP_REST_Request $request) {
    $given = $request->get_header('x-ghka-token');
    if (!$given) {
        $given = $request->get_param('token');
    }
    if (!is_string($given) || $given === '') {
        return false;
    }
    return hash_equals(GHKA_TOKEN, $given);
}

function ghka_denied() {
    return new WP_REST_Response(array('ok' => false, 'error' => 'bad token'), 403);
}

/* -------------------------------------------------------------------------
 * What the site can tell us about itself
 * ---------------------------------------------------------------------- */

function ghka_site_facts() {
    $counts = wp_count_posts('post');
    $last   = get_posts(array(
        'numberposts'      => 1,
        'post_status'      => 'publish',
        'orderby'          => 'date',
        'order'            => 'DESC',
        'suppress_filters' => false,
    ));

    return array(
        'published_posts'  => isset($counts->publish) ? (int) $counts->publish : 0,
        'last_post_date'   => $last ? $last[0]->post_date_gmt : null,
        'last_post_title'  => $last ? wp_strip_all_tags($last[0]->post_title) : null,
        'generated_count'  => (int) get_option(GHKA_OPT_COUNT, 0),
        'last_generated'   => get_option(GHKA_OPT_LAST, null),
        'wp_version'       => get_bloginfo('version'),
        'php_version'      => PHP_VERSION,
        'site_url'         => get_site_url(),
        'plugin_version'   => '1.0.0',
        'server_time_gmt'  => gmdate('Y-m-d H:i:s'),
    );
}

/** Read-only. Never publishes. Useful for checking a site without changing it. */
function ghka_status(WP_REST_Request $request) {
    if (!ghka_token_ok($request)) {
        return ghka_denied();
    }
    return new WP_REST_Response(array('ok' => true, 'site' => ghka_site_facts()), 200);
}

/* -------------------------------------------------------------------------
 * Publishing
 * ---------------------------------------------------------------------- */

function ghka_days_since_last_generated() {
    $last = get_option(GHKA_OPT_LAST, '');
    if (!$last) {
        return null; // Never published one.
    }
    $then = strtotime($last . ' UTC');
    if (!$then) {
        return null;
    }
    return (time() - $then) / DAY_IN_SECONDS;
}

function ghka_ping(WP_REST_Request $request) {
    if (!ghka_token_ok($request)) {
        return ghka_denied();
    }

    $facts = ghka_site_facts();
    $force = (bool) $request->get_param('force');

    // 0 disables each guard, which is the default: publish on every ping.
    if (GHKA_MAX_POSTS > 0 && (int) get_option(GHKA_OPT_COUNT, 0) >= GHKA_MAX_POSTS) {
        return ghka_result('skipped', 'post cap reached', $facts);
    }

    $days = ghka_days_since_last_generated();
    if (!$force && GHKA_MIN_DAYS_BETWEEN_POSTS > 0
        && $days !== null && $days < GHKA_MIN_DAYS_BETWEEN_POSTS) {
        return ghka_result('skipped', sprintf('last post was %.1f days ago', $days), $facts);
    }

    $author = ghka_pick_author();
    if (!$author) {
        return ghka_result('error', 'no author available', $facts);
    }

    $draft   = ghka_compose_post();
    $post_id = wp_insert_post(array(
        'post_title'   => $draft['title'],
        'post_content' => $draft['content'],
        'post_status'  => 'publish',
        'post_type'    => 'post',
        'post_author'  => $author,
        'meta_input'   => array(GHKA_META_FLAG => 1),
    ), true);

    if (is_wp_error($post_id)) {
        return ghka_result('error', $post_id->get_error_message(), $facts);
    }

    update_option(GHKA_OPT_LAST, gmdate('Y-m-d H:i:s'), false);
    update_option(GHKA_OPT_COUNT, (int) get_option(GHKA_OPT_COUNT, 0) + 1, false);

    $facts = ghka_site_facts(); // Refresh so the caller sees the new state.
    $out = ghka_result('published', 'ok', $facts);
    $data = $out->get_data();
    $data['post_id']  = (int) $post_id;
    $data['post_url'] = get_permalink($post_id);
    $data['title']    = $draft['title'];
    $out->set_data($data);
    return $out;
}

function ghka_result($action, $detail, $facts) {
    return new WP_REST_Response(array(
        'ok'     => $action !== 'error',
        'action' => $action,
        'detail' => $detail,
        'site'   => $facts,
    ), $action === 'error' ? 500 : 200);
}

/** Prefer a real administrator, so posts are not attributed to nobody. */
function ghka_pick_author() {
    $admins = get_users(array('role' => 'administrator', 'number' => 1, 'fields' => 'ID'));
    if (!empty($admins)) {
        return (int) $admins[0];
    }
    $any = get_users(array('number' => 1, 'fields' => 'ID'));
    return !empty($any) ? (int) $any[0] : 0;
}

/* -------------------------------------------------------------------------
 * The demo content
 *
 * Swap this function out to publish something real. Everything else in the
 * plugin stays the same - this is the only place content is decided.
 * ---------------------------------------------------------------------- */

function ghka_compose_post() {
    $openers = array(
        'Notes on', 'A short note about', 'Thinking about',
        'A few thoughts on', 'Looking at', 'Some notes on',
    );
    $subjects = array(
        'keeping things simple', 'small daily habits', 'planning the week',
        'working with less', 'staying organised', 'slow mornings',
        'writing things down', 'finishing what you start',
        'quiet afternoons', 'making room for rest', 'starting over',
        'one thing at a time',
    );
    $paragraphs = array(
        'There is something to be said for keeping a routine that does not ask too much. The days that go well are rarely the busiest ones. More often they are the ones with a little room left over at the end.',
        'Most of what matters gets done in small pieces. A page here, a phone call there. It never feels like progress while it is happening, and then one day the thing is simply finished.',
        'It helps to write things down. Not to remember them exactly, but to stop carrying them around. Once a thing is on paper it stops taking up space.',
        'Nothing here is complicated. Start with the part that is already clear, and let the rest wait until it is. The order usually sorts itself out.',
        'Some weeks go to plan and some do not. Both are fine. The plan was only ever a guess about how the week might go.',
        'It is worth stopping now and then to ask whether a thing still needs doing. Often it does not, and it can simply be crossed off.',
        'Rest is part of the work, not a break from it. The afternoons spent doing nothing in particular tend to be the ones that make the rest possible.',
        'Small changes hold better than big ones. A habit that fits into an ordinary day will still be there next month.',
    );

    shuffle($paragraphs);
    $body = array_slice($paragraphs, 0, wp_rand(3, 5));

    $title = $openers[array_rand($openers)] . ' ' . $subjects[array_rand($subjects)];

    $content = '';
    foreach ($body as $p) {
        $content .= "\n\n" . $p;
    }

    return array(
        'title'   => ucfirst($title),
        'content' => trim($content),
    );
}

/* -------------------------------------------------------------------------
 * Housekeeping
 * ---------------------------------------------------------------------- */

register_activation_hook(__FILE__, 'ghka_on_activate');

function ghka_on_activate() {
    add_option(GHKA_OPT_COUNT, 0, '', false);
}

/**
 * Warn in the admin if the token was never changed, so a site does not sit
 * there quietly accepting the placeholder value.
 */
add_action('admin_notices', 'ghka_admin_notice');

function ghka_admin_notice() {
    if (strpos(GHKA_TOKEN, 'CHANGE-ME') !== 0) {
        return;
    }
    if (!current_user_can('manage_options')) {
        return;
    }
    echo '<div class="notice notice-warning"><p><strong>GH Keep-Alive:</strong> '
       . 'the token is still the placeholder. Edit GHKA_TOKEN in the plugin file '
       . 'before relying on it.</p></div>';
}
