# Root-owned profile. site-shell has already verified and dropped privileges.
[[ $- == *i* && $EUID -ne 0 && $UID == "$EUID" ]] || { printf 'Use site-shell as the site owner.\n' >&2; exit 2; }
umask 027
readonly SITE_DOMAIN SITE_USER SITE_PHP SITE_BASE
export HOME="$SITE_BASE/owner-tmp"
export WP_CLI_CACHE_DIR="$HOME/wp-cli-cache"
export HISTFILE="$HOME/.bash_history"
HISTCONTROL=ignoreboth
HISTSIZE=1000
HISTFILESIZE=2000
shopt -s histappend checkwinsize
# Interactive Bash can report an idle signal as a normal shell exit.
trap 'exit' TERM HUP
cd "$SITE_BASE/htdocs" || exit 1
if [[ $TERM != dumb ]]; then
    PS1='[\[\e[36m\]\u@${SITE_DOMAIN}\[\e[0m\] \w] $ '
else
    PS1='[\u@${SITE_DOMAIN} \w] $ '
fi
# Fixed wrappers determine identity from UID/facts on each call, not SITE_*.
php() { /usr/local/sbin/site-php "$@"; }
wp() { /usr/local/sbin/site-wp "$@"; }
policy() { /usr/local/sbin/site-policy "$@"; }
# Completion and user customizations run only after the privilege drop.
if [[ -r /usr/share/bash-completion/bash_completion ]]; then
    source /usr/share/bash-completion/bash_completion
fi
source /usr/local/lib/wp-hosting/lib/site-completion.bash
printf 'Site: %s | user: %s | PHP: %s\n' "$SITE_DOMAIN" "$SITE_USER" "$SITE_PHP"
printf 'Use jump-in-help for commands. Use exit to return.\n'
if [[ -f $HOME/.site-shellrc && ! -L $HOME/.site-shellrc ]]; then
    source "$HOME/.site-shellrc"
fi
