#!/bin/bash
# Server-side checks after site.yml on the TEST CT (inventories/test). Run as root:
#   scp tests/verify.sh root@<test-ct>:/root/ && ssh root@<test-ct> bash /root/verify.sh
# Installs WordPress core in test.local and legacy.local (needs internet).
# Never run on production: it installs sites, drops a stray file, runs backups.
set -uo pipefail
PASS=0 FAIL=0
ok()   { echo "PASS  $*"; PASS=$((PASS+1)); }
bad()  { echo "FAIL  $*"; FAIL=$((FAIL+1)); }
chk()  { local d=$1; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi; }
env_of() { (. "/srv/sites/$1/conf/site.env"; echo "${!2}"); }
wp_owner() {  # domain, wp args...
    local d=$1; shift
    runuser -u "$(env_of "$d" SITE_USER)" -- env HOME=/tmp \
        "php$(env_of "$d" PHP_VERSION)" /usr/local/bin/wp --path="/srv/sites/$d/htdocs" "$@"
}
code() { curl -sk -o /dev/null -w '%{http_code}' --max-time 20 --resolve "$1:443:127.0.0.1" "https://$1$2"; }

SITES=$(for e in /srv/sites/*/conf/site.env; do (. "$e"; echo "$DOMAIN"); done)
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 2; }
grep -qE '^(DOMAIN=test\.local)$' /srv/sites/test.local/conf/site.env 2>/dev/null \
    || { echo "test.local not found: is this the test CT?"; exit 2; }

chk "nginx -t" nginx -t -q
for v in $(for d in $SITES; do env_of "$d" PHP_VERSION; done | sort -u); do
    chk "php$v-fpm active" systemctl is-active --quiet "php$v-fpm"
done
chk "mariadb active" systemctl is-active --quiet mariadb
chk "valkey active" systemctl is-active --quiet valkey-server
for t in wp-backup.timer wp-monitor.timer certbot.timer; do
    systemctl list-unit-files "$t" >/dev/null 2>&1 && chk "$t enabled" systemctl is-enabled --quiet "$t"
done

for d in $SITES; do
    if [ ! -f "/srv/sites/$d/htdocs/wp-includes/version.php" ]; then
        wp_owner "$d" core download --skip-content --quiet && \
        wp_owner "$d" core install --url="https://$d" --title="$d" --admin_user=admin \
            --admin_password="$(openssl rand -hex 12)" --admin_email="admin@$d" --skip-email --quiet
    fi
    c=$(code "$d" /); [ "$c" = 200 ] && ok "$d homepage 200" || bad "$d homepage $c"
    up="/srv/sites/$d/htdocs/wp-content/uploads"
    install -d -o "$(env_of "$d" SITE_USER)" -g "$(env_of "$d" SITE_USER)" -m 2770 "$up"
    echo '<?php echo "EXECUTED";' > "$up/t.php"
    body=$(curl -sk --max-time 20 --resolve "$d:443:127.0.0.1" "https://$d/wp-content/uploads/t.php")
    c=$(code "$d" /wp-content/uploads/t.php)
    [[ $body != *EXECUTED* && $c != 200 ]] && ok "$d PHP in uploads blocked ($c)" || bad "$d PHP in uploads ran ($c)"
    rm -f "$up/t.php"
done

chk "wp-login rate limit in nginx config" bash -c "nginx -T 2>/dev/null | grep -A3 'wp-login' | grep -q limit_req"

d=test.local
if [ "$(env_of $d REDIS)" = true ]; then
    out=$(runuser -u "$(env_of $d SITE_USER)-php" -- env HOME=/tmp php8.4 /usr/local/bin/wp \
        --path=/srv/sites/$d/htdocs --skip-plugins --skip-themes eval '
        $r = new Redis(); $r->connect(WP_REDIS_PATH);
        $r->auth(WP_REDIS_PASSWORD); $k = WP_REDIS_PREFIX . "verify";
        echo ($r->set($k, "1") && $r->get($k) === "1" && $r->del($k)) ? "OK" : "NO";' 2>&1)
    [[ $out == OK* ]] && ok "valkey reachable by $(env_of $d SITE_USER)-php" || bad "valkey from PHP: $out"
fi

# wp-monitor: first run makes baselines, second must be clean, stray file must alert.
wp-monitor >/dev/null 2>&1
chk "wp-monitor clean" wp-monitor
since=$(date '+%Y-%m-%d %H:%M:%S'); sleep 1
echo '<?php // stray' > /srv/sites/$d/htdocs/stray.php
if wp-monitor >/dev/null 2>&1; then bad "wp-monitor missed stray.php"; else ok "wp-monitor flags stray.php"; fi
chk "tg-alert logged to syslog" bash -c "journalctl -t tg-alert --since '$since' --no-pager | grep -q stray.php"
rm -f /srv/sites/$d/htdocs/stray.php
chk "wp-monitor clean again" wp-monitor

chk "wp-backup $d" wp-backup "$d"
chk "db dump valid" bash -c "gzip -t /srv/sites/$d/backup/db.sql.gz && zcat /srv/sites/$d/backup/db.sql.gz | tail -1 | grep -q 'Dump completed'"
chk "wp-backup full (forget/prune/check)" wp-backup
chk "restic snapshots" bash -c "set -a; . /etc/restic/env; restic snapshots --compact | grep -q nightly"

echo "== $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
