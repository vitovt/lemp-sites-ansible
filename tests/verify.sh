#!/bin/bash
# Server-side checks after site.yml on the TEST CT (inventories/test). Run as root:
#   scp tests/verify.sh <admin>@<test-host>: && ssh <admin>@<test-host> sudo bash verify.sh [site]
# [site] = main test site (default test1.example). Installs WordPress core in
# every site that has none (needs internet).
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
T=${1:-test1.example}
grep -qxF "DOMAIN=$T" "/srv/sites/$T/conf/site.env" 2>/dev/null \
    || { echo "$T not found: is this the test host?"; exit 2; }

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
    c=$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 20 --resolve "$d:80:127.0.0.1" "http://$d/x?y=1")
    [[ $c == "301 https://$d/x?y=1" ]] && ok "$d http -> https" || bad "$d http redirect: $c"
    up="/srv/sites/$d/htdocs/wp-content/uploads"
    install -d -o "$(env_of "$d" SITE_USER)" -g "$(env_of "$d" SITE_USER)" -m 2770 "$up"
    echo '<?php echo "EXECUTED";' > "$up/t.php"
    body=$(curl -sk --max-time 20 --resolve "$d:443:127.0.0.1" "https://$d/wp-content/uploads/t.php")
    c=$(code "$d" /wp-content/uploads/t.php)
    [[ $body != *EXECUTED* && $c != 200 ]] && ok "$d PHP in uploads blocked ($c)" || bad "$d PHP in uploads ran ($c)"
    rm -f "$up/t.php"
done

chk "wp-login rate limit in nginx config" bash -c "nginx -T 2>/dev/null | grep -A3 'wp-login' | grep -q limit_req"

d=$T
if [ "$(env_of $d REDIS)" = true ]; then
    out=$(runuser -u "$(env_of $d SITE_USER)-php" -- env HOME=/tmp "php$(env_of $d PHP_VERSION)" /usr/local/bin/wp \
        --path=/srv/sites/$d/htdocs --skip-plugins --skip-themes eval '
        $r = new Redis(); $r->connect(WP_REDIS_PATH);
        $r->auth(WP_REDIS_PASSWORD); $k = WP_REDIS_PREFIX . "verify";
        echo ($r->set($k, "1") && $r->get($k) === "1" && $r->del($k)) ? "OK" : "NO";' 2>&1)
    [[ $out == OK* ]] && ok "valkey reachable by $(env_of $d SITE_USER)-php" || bad "valkey from PHP: $out"
fi

# wp-monitor: first run makes baselines, second must be clean.
wp-monitor >/dev/null 2>&1
chk "wp-monitor clean" wp-monitor
out=$(wp-monitor --check)
n=$(grep -c '^PASS' <<<"$out")
grep -q '^FAIL' <<<"$out" && bad "wp-monitor --check: $(grep '^FAIL' <<<"$out" | head -3)" || ok "wp-monitor --check: $n PASS, 0 FAIL"
for c in host_services disk_space db_connect http_home fpm_pool valkey core_checksums plugin_checksums \
         stray_php php_in_writable git_status admin_users content_spike; do
    grep -q "^PASS [^ ]* $c\b" <<<"$out" || bad "wp-monitor --check has no PASS for $c"
done
# Read-only: --check must not touch baselines.
chk "wp-monitor --check writes no state" bash -c "s=\$(ls -l --time-style=+%s%N /var/lib/wp-monitor); wp-monitor --check $d; [ \"\$s\" = \"\$(ls -l --time-style=+%s%N /var/lib/wp-monitor)\" ]"
mon() { wp-monitor --check "$d" 2>&1; }
has_fail() { grep -q "^FAIL $d $1" <<<"$2" && ok "wp-monitor $1: $3" || bad "wp-monitor missed $3 ($1)"; }

# Integrity: planted files and a modified core file.
H=/srv/sites/$d/htdocs U=$(env_of $d SITE_USER)
cp -p "$H/wp-includes/version.php" /tmp/verify-version.php
since=$(date '+%Y-%m-%d %H:%M:%S'); sleep 1
echo '<?php // stray' > "$H/stray.php"
echo '<?php // stray' > "$H/wp-content/x.php"
echo '<?php // stray' > "$H/wp-includes/.x.php"
echo 'auto_prepend_file=/tmp/x' > "$H/wp-content/themes/.user.ini"
install -d -o "$U" -g "$U" -m 2770 "$H/wp-content/uploads"
echo '<?php // stray' > "$H/wp-content/uploads/evil.php"
echo '// changed' >> "$H/wp-includes/version.php"
out=$(mon)
has_fail core_checksums "$out" "stray.php in root"
grep -q "modified: wp-includes/version.php" <<<"$out" && ok "wp-monitor: modified core file" || bad "wp-monitor missed modified version.php"
has_fail stray_php "$out" "wp-content/x.php, .x.php, .user.ini"
grep -q "^FAIL $d stray_php.*\.user\.ini" <<<"$out" || bad "stray_php missed .user.ini"
has_fail php_in_writable "$out" "PHP in uploads"
has_fail git_status "$out" "uncommitted files"
chk "wp-monitor --files lists stray.php" bash -c "wp-monitor --files $d | grep -qx 'FILE stray.php'"
if wp-monitor >/dev/null 2>&1; then bad "wp-monitor nightly exit 0 with problems"; else ok "wp-monitor nightly exit 1"; fi
chk "tg-alert logged to syslog" bash -c "journalctl -t tg-alert --since '$since' --no-pager | grep -q stray.php"
rm -f "$H/stray.php" "$H/wp-content/x.php" "$H/wp-includes/.x.php" "$H/wp-content/themes/.user.ini" "$H/wp-content/uploads/evil.php"
cp -p /tmp/verify-version.php "$H/wp-includes/version.php"; rm -f /tmp/verify-version.php

# Administrators: added -> alert naming who, baseline follows, removed -> alert.
since=$(date '+%Y-%m-%d %H:%M:%S'); sleep 1
uid=$(wp_owner $d user create verifyevil verifyevil@example.invalid --role=administrator --porcelain 2>/dev/null)
has_fail admin_users "$(mon)" "new administrator"
grep -q "added verifyevil (id $uid)" <<<"$(mon)" && ok "admin_users names verifyevil (id $uid)" || bad "admin_users detail"
wp-monitor >/dev/null 2>&1
chk "admin alert names the user" bash -c "journalctl -t tg-alert --since '$since' --no-pager | grep -q 'added verifyevil'"
grep -q "^PASS $d admin_users" <<<"$(mon)" && ok "admin baseline updated after alert" || bad "admin baseline not updated"
wp_owner $d user delete "$uid" --yes >/dev/null 2>&1
grep -q "removed verifyevil (id $uid)" <<<"$(mon)" && ok "admin_users: removed administrator" || bad "admin_users missed removal"
wp-monitor >/dev/null 2>&1
grep -q "^PASS $d admin_users" <<<"$(mon)" && ok "admin baseline back" || bad "admin baseline after removal"

# Content spike: > CONTENT_MAX (default 10) new posts of any type in 24 h.
ids=$(for i in $(seq 1 11); do wp_owner $d post create --post_type=page --post_status=publish \
      --post_title="verify spike $i" --porcelain 2>/dev/null; done | tr '\n' ' ')
has_fail content_spike "$(mon)" "11 new pages"
env=/srv/sites/$d/conf/site.env; cp -p "$env" /tmp/verify-site.env
sed -i 's/^CONTENT_MAX=.*/CONTENT_MAX=100/' "$env"
grep -q "^PASS $d content_spike.*limit 100" <<<"$(mon)" && ok "content_spike per-site limit (100)" || bad "content_spike limit from site.env"
cp -p /tmp/verify-site.env "$env"; rm -f /tmp/verify-site.env
wp_owner $d post delete $ids --force >/dev/null 2>&1
grep -q "^PASS $d content_spike" <<<"$(mon)" && ok "content_spike clean after delete" || bad "content_spike after delete"

# Health: PHP-FPM and Valkey down are reported.
v=$(env_of $d PHP_VERSION)
systemctl stop "php$v-fpm"
has_fail fpm_pool "$(mon)" "php$v-fpm stopped"
systemctl start "php$v-fpm"
if [ "$(env_of $d REDIS)" = true ]; then
    systemctl stop valkey-server
    out=$(wp-monitor --check 2>&1)
    grep -q "^FAIL host host_services.*valkey-server" <<<"$out" && grep -q "^FAIL $d valkey" <<<"$out" \
        && ok "wp-monitor: valkey stopped" || bad "wp-monitor missed valkey stopped"
    c=$(code "$d" "/?nocache=$RANDOM"); [ "$c" = 200 ] && ok "$d works without Valkey (WP_REDIS_GRACEFUL)" || bad "$d $c with Valkey down"
    systemctl start valkey-server
fi
chk "wp-monitor clean again" wp-monitor

chk "wp-backup $d" wp-backup "$d"
chk "db dump valid" bash -c "gzip -t /srv/sites/$d/backup/db.sql.gz && zcat /srv/sites/$d/backup/db.sql.gz | tail -1 | grep -q 'Dump completed'"
chk "wp-backup full (forget/prune/check)" wp-backup
chk "restic snapshots" bash -c "set -a; . /etc/restic/env; restic snapshots --compact | grep -q nightly"
chk "wp-backup verify" wp-backup verify
# Restore round trip: marker post after the backup must be gone after restore-db.
mid=$(wp_owner $d post create --post_title="verify marker" --post_status=publish --porcelain 2>/dev/null)
dump=$(wp-backup restore "$d" 2>/dev/null | sed -n 's/^dump: //p')
[ -f "$dump" ] && ok "wp-backup restore to staging" || bad "wp-backup restore: no dump"
chk "restore-db refuses without --yes" bash -c "! wp-backup restore-db $d '$dump'"
chk "wp-backup restore-db --yes" wp-backup restore-db "$d" "$dump" --yes
wp_owner $d post get "$mid" >/dev/null 2>&1 && bad "restore-db: marker post still there" || ok "restore-db: DB back to the snapshot"
c=$(code "$d" /); [ "$c" = 200 ] && ok "$d homepage 200 after restore" || bad "$d homepage $c after restore"
rm -rf /srv/restore /srv/sites/$d/backup/db-before-restore-*

echo "== $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
