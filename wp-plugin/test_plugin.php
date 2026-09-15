<?php
/**
 * Logic tests for gh-keepalive, with WordPress stubbed out.
 *
 * A parse error or a logic slip in an active plugin can white-screen a whole
 * site, and this one is meant to go on every site at once, so the handlers get
 * exercised here before they go anywhere near a real install.
 *
 * PHP constants can only be defined once per process, and the plugin has two
 * meaningfully different configurations, so this file runs twice:
 *
 *   php wp-plugin/test_plugin.php unlimited   <- the shipped default, no limits
 *   php wp-plugin/test_plugin.php limited     <- guards switched on in wp-config
 */

$MODE = isset($argv[1]) ? $argv[1] : 'unlimited';
if (!in_array($MODE, array('unlimited', 'limited'), true)) {
    fwrite(STDERR, "usage: php test_plugin.php [unlimited|limited]\n");
    exit(2);
}
echo "MODE: $MODE\n\n";

define('ABSPATH', __DIR__ . '/');
define('DAY_IN_SECONDS', 86400);

define('GHKA_TOKEN', 'test-token-abc123');
define('GHKA_MIN_DAYS_BETWEEN_POSTS', $MODE === 'limited' ? 25 : 0);
define('GHKA_MAX_POSTS', $MODE === 'limited' ? 3 : 0);

/* ---------------------------------------------------------------- stubs -- */

$GLOBALS['opts']    = array();
$GLOBALS['posts']   = array();
$GLOBALS['actions'] = array();
$GLOBALS['routes']  = array();
$GLOBALS['next_id'] = 100;

function add_action($h, $c, $p = 10, $a = 1) { $GLOBALS['actions'][$h][] = $c; }
function register_activation_hook($f, $c) { }
function register_rest_route($ns, $route, $args) { $GLOBALS['routes'][$ns . $route] = $args; }

function get_option($k, $d = false) { return array_key_exists($k, $GLOBALS['opts']) ? $GLOBALS['opts'][$k] : $d; }
function update_option($k, $v, $a = null) { $GLOBALS['opts'][$k] = $v; return true; }
function add_option($k, $v, $x = '', $a = null) { if (!isset($GLOBALS['opts'][$k])) { $GLOBALS['opts'][$k] = $v; } return true; }

function get_bloginfo($w) { return '6.5.2'; }
function get_site_url() { return 'http://example.wuaze.com'; }
function wp_strip_all_tags($s) { return strip_tags($s); }
function wp_rand($lo, $hi) { return random_int($lo, $hi); }
function current_user_can($c) { return true; }
function get_permalink($id) { return 'http://example.wuaze.com/?p=' . $id; }

function wp_count_posts($t = 'post') {
    $o = new stdClass();
    $o->publish = count($GLOBALS['posts']);
    return $o;
}

function get_posts($a = array()) {
    $p = $GLOBALS['posts'];
    usort($p, function ($x, $y) { return strcmp($y->post_date_gmt, $x->post_date_gmt); });
    $n = isset($a['numberposts']) ? $a['numberposts'] : 5;
    return array_slice($p, 0, $n);
}

function get_users($a = array()) { return array(7); }

function wp_insert_post($data, $wp_error = false) {
    if (empty($data['post_title'])) {
        return new WP_Error('empty_title', 'Title is empty');
    }
    $p = new stdClass();
    $p->ID            = $GLOBALS['next_id']++;
    $p->post_title    = $data['post_title'];
    $p->post_content  = $data['post_content'];
    $p->post_date_gmt = gmdate('Y-m-d H:i:s');
    $GLOBALS['posts'][] = $p;
    return $p->ID;
}

class WP_Error {
    private $msg;
    public function __construct($c, $m = '') { $this->msg = $m; }
    public function get_error_message() { return $this->msg; }
}
function is_wp_error($t) { return $t instanceof WP_Error; }

class WP_REST_Request {
    private $params, $headers;
    public function __construct($params = array(), $headers = array()) {
        $this->params  = $params;
        $this->headers = $headers;
    }
    public function get_param($k) { return isset($this->params[$k]) ? $this->params[$k] : null; }
    public function get_header($k) { return isset($this->headers[$k]) ? $this->headers[$k] : null; }
}

class WP_REST_Response {
    private $data, $status;
    public function __construct($d, $s = 200) { $this->data = $d; $this->status = $s; }
    public function get_data() { return $this->data; }
    public function set_data($d) { $this->data = $d; }
    public function get_status() { return $this->status; }
}

require __DIR__ . '/gh-keepalive/gh-keepalive.php';

/* --------------------------------------------------------------- harness -- */

$FAILS = array();

function check($label, $got, $want) {
    global $FAILS;
    if ($got !== $want) {
        $FAILS[] = $label;
        printf("FAIL  %-56s got %s want %s\n", $label, var_export($got, true), var_export($want, true));
    } else {
        printf("ok    %-56s %s\n", $label, var_export($got, true));
    }
}

function req($params = array(), $headers = array()) {
    return new WP_REST_Request($params, $headers);
}

function good() { return req(array('token' => GHKA_TOKEN)); }

/* ----------------------------------------------------------------- tests -- */

echo "--- routes registered ----------------------------------------------\n";
foreach ($GLOBALS['actions']['rest_api_init'] as $cb) { call_user_func($cb); }
check('ping route registered', isset($GLOBALS['routes']['ghka/v1/ping']), true);
check('status route registered', isset($GLOBALS['routes']['ghka/v1/status']), true);
check('routes are public, token checked inside',
      $GLOBALS['routes']['ghka/v1/ping']['permission_callback'], '__return_true');

echo "\n--- the token gate -------------------------------------------------\n";
check('no token is refused', ghka_ping(req())->get_status(), 403);
check('wrong token is refused', ghka_ping(req(array('token' => 'nope')))->get_status(), 403);
check('empty token is refused', ghka_ping(req(array('token' => '')))->get_status(), 403);
check('nothing was published while refused', count($GLOBALS['posts']), 0);
check('token via header works',
      ghka_status(req(array(), array('x-ghka-token' => GHKA_TOKEN)))->get_status(), 200);
check('token via query works', ghka_status(good())->get_status(), 200);

echo "\n--- status never writes --------------------------------------------\n";
$before = count($GLOBALS['posts']);
ghka_status(good());
check('status published nothing', count($GLOBALS['posts']), $before);

echo "\n--- publishing ------------------------------------------------------\n";
$r = ghka_ping(good())->get_data();
check('first ping publishes', $r['action'], 'published');
check('post really created', count($GLOBALS['posts']), 1);
check('counter incremented', (int) get_option('ghka_created_count'), 1);
check('post has a title', strlen($GLOBALS['posts'][0]->post_title) > 5, true);
check('post has a body', strlen($GLOBALS['posts'][0]->post_content) > 100, true);
check('response carries the post id', isset($r['post_id']), true);
check('response carries site facts', $r['site']['published_posts'], 1);

if ($MODE === 'limited') {
    echo "\n--- the waiting window (guard switched on) --------------------------\n";
    $r2 = ghka_ping(good())->get_data();
    check('second ping is skipped', $r2['action'], 'skipped');
    check('still only one post', count($GLOBALS['posts']), 1);

    update_option('ghka_last_published', gmdate('Y-m-d H:i:s', time() - 30 * DAY_IN_SECONDS));
    $r3 = ghka_ping(good())->get_data();
    check('publishes again after the window', $r3['action'], 'published');
    check('two posts now', count($GLOBALS['posts']), 2);

    $r4 = ghka_ping(req(array('token' => GHKA_TOKEN, 'force' => 1)))->get_data();
    check('force overrides the window', $r4['action'], 'published');
    check('three posts now', count($GLOBALS['posts']), 3);

    echo "\n--- the hard cap (guard switched on) --------------------------------\n";
    update_option('ghka_last_published', gmdate('Y-m-d H:i:s', time() - 99 * DAY_IN_SECONDS));
    $r5 = ghka_ping(good())->get_data();
    check('cap stops publishing', $r5['action'], 'skipped');
    check('cap reason reported', $r5['detail'], 'post cap reached');
    $r6 = ghka_ping(req(array('token' => GHKA_TOKEN, 'force' => 1)))->get_data();
    check('force cannot break the cap', $r6['action'], 'skipped');
    check('never more than the cap', count($GLOBALS['posts']), (int) GHKA_MAX_POSTS);
} else {
    echo "\n--- no limits (the shipped default) ---------------------------------\n";
    check('waiting window is off', (int) GHKA_MIN_DAYS_BETWEEN_POSTS, 0);
    check('post cap is off', (int) GHKA_MAX_POSTS, 0);

    // Back to back pings must every one of them publish: no waiting window.
    for ($i = 2; $i <= 6; $i++) {
        $res = ghka_ping(good())->get_data();
        check("ping $i publishes immediately", $res['action'], 'published');
    }
    check('six posts, none skipped', count($GLOBALS['posts']), 6);
    check('counter kept up', (int) get_option('ghka_created_count'), 6);

    // Well past any ceiling the plugin used to ship with.
    for ($i = 0; $i < 70; $i++) { ghka_ping(good()); }
    check('no ceiling at all', count($GLOBALS['posts']), 76);

    // Removing the limits must not have loosened anything else.
    check('token gate still applies',
          ghka_ping(req(array('token' => 'nope')))->get_status(), 403);
    check('refused ping published nothing', count($GLOBALS['posts']), 76);
    check('status still never writes',
          (ghka_status(good())->get_status() === 200) && count($GLOBALS['posts']) === 76, true);
}

echo "\n--- generated content -----------------------------------------------\n";
$titles = array();
$short  = 0;
for ($i = 0; $i < 60; $i++) {
    $d = ghka_compose_post();
    $titles[] = $d['title'];
    if (strlen($d['content']) < 150) { $short++; }
}
check('every body is substantial', $short, 0);
check('titles vary', count(array_unique($titles)) > 20, true);
check('title is capitalised', ctype_upper(substr($titles[0], 0, 1)), true);

echo "\n--- site facts ------------------------------------------------------\n";
$f = ghka_site_facts();
check('reports post count', $f['published_posts'], count($GLOBALS['posts']));
check('reports wp version', $f['wp_version'], '6.5.2');
check('reports php version', $f['php_version'], PHP_VERSION);
check('reports last post title', is_string($f['last_post_title']), true);

echo "\n" . str_repeat('=', 70) . "\n";
if ($FAILS) {
    echo count($FAILS) . " FAILURE(S) in mode $MODE:\n";
    foreach ($FAILS as $f) { echo "  $f\n"; }
    exit(1);
}
echo "all plugin tests passed ($MODE)\n";
