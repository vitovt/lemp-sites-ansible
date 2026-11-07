# Managed operator shell for one site. Loaded only by jump-in.
umask 027
cd "$SITE_BASE/htdocs" || exit 1
export HOME="$SITE_BASE/owner-tmp"
export WP_CLI_CACHE_DIR="$HOME/wp-cli-cache"
PS1='[\[\e[36m\]${SITE_DOMAIN}:${SITE_USER}\[\e[0m\]] \w $ '
wp() {
    command "php${SITE_PHP}" /usr/local/bin/wp --path="$SITE_BASE/htdocs" \
        --skip-plugins --skip-themes "$@"
}
policy() {
    if (( $# == 0 )); then
        sudo -n /usr/local/sbin/wp-site policy menu "$SITE_DOMAIN"
    else
        local action="$1"
        shift
        sudo -n /usr/local/sbin/wp-site policy "$action" "$SITE_DOMAIN" "$@"
    fi
}
printf 'Site: %s | owner: %s | PHP: %s | policy: policy [show|hold|...]\n' \
    "$SITE_DOMAIN" "$SITE_USER" "$SITE_PHP"
