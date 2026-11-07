# Root-owned Bash completion. No sudo, PHP, network or site code runs on Tab.
_site_complete() {
    local cur=${COMP_WORDS[COMP_CWORD]} cmd=${COMP_WORDS[0]} action=${COMP_WORDS[1]:-}
    local words='' root='' word kind=plugin component='' i token skip='' prev=${COMP_WORDS[COMP_CWORD-1]}
    case $cmd in
        jump-in-help)
            words='git wp php check policy files history exit site-shell jump-in-help --all --help' ;;
        policy|site-policy)
            for ((i=2; i<COMP_CWORD; i++)); do
                token=${COMP_WORDS[i]}
                if [[ -n $skip ]]; then
                    [[ $skip == --kind ]] && kind=$token
                    skip=''
                elif [[ $token == --kind || $token == --reason || $token == --mode ]]; then
                    skip=$token
                elif [[ $token != --* && -z $component ]]; then
                    component=$token
                fi
            done
            if (( COMP_CWORD == 1 )); then
                words='menu show hold unhold checksums allow-file remove-file --help'
            elif [[ $prev == --kind ]]; then words='plugin theme'
            elif [[ $prev == --mode ]]; then words='wordpress_org git'
            elif [[ $prev == --reason ]]; then COMPREPLY=(); return
            elif [[ $cur == --* ]]; then words='--kind --reason --mode --yes --yaml'
            elif [[ $action == show ]]; then words='--yaml'
            elif [[ -z $component ]]; then
                root="$SITE_BASE/htdocs/wp-content/plugins"
                [[ $kind == theme ]] && root="$SITE_BASE/htdocs/wp-content/themes"
            elif [[ $action == allow-file || $action == remove-file ]]; then
                root="$SITE_BASE/htdocs/wp-content/plugins/$component"
                mapfile -t COMPREPLY < <(cd -- "$root" 2>/dev/null && compgen -f -- "$cur")
                return
            fi ;;
        site-check)
            if (( COMP_CWORD == 1 )); then words='all git core plugins files --help'
            elif [[ $action == plugins ]]; then root="$SITE_BASE/htdocs/wp-content/plugins"; fi ;;
        wp|site-wp)
            if (( COMP_CWORD == 1 )); then
                words='core plugin theme option user db cache transient media post term rewrite cli --info --help'
            elif (( COMP_CWORD == 2 )); then
                case $action in
                    plugin) words='list status get update install activate deactivate delete verify-checksums' ;;
                    theme) words='list status get update install activate delete' ;;
                    core) words='version check-update update update-db verify-checksums' ;;
                    option) words='get list update add delete' ;;
                    user) words='list get create update delete' ;;
                    db) words='check tables size export import query' ;;
                    cache) words='flush get set delete' ;;
                    cli) words='info version help completions' ;;
                esac
            elif [[ $cur == --* ]]; then
                words='--all --minor --dry-run --format=json --format=table --include-root --skip-plugins --skip-themes'
            elif [[ $action == plugin ]]; then root="$SITE_BASE/htdocs/wp-content/plugins"
            elif [[ $action == theme ]]; then root="$SITE_BASE/htdocs/wp-content/themes"; fi ;;
    esac
    if [[ -n $root && -d $root && ! -L $root ]]; then
        COMPREPLY=()
        for word in "$root"/*; do
            [[ -d $word && ! -L $word ]] || continue
            word=${word##*/}
            [[ $word == "$cur"* ]] && COMPREPLY+=("$word")
        done
    else
        mapfile -t COMPREPLY < <(compgen -W "$words" -- "$cur")
    fi
}
complete -o default -o bashdefault -F _site_complete wp site-wp policy site-policy site-check jump-in-help
# git's packaged completion is loaded by bash-completion on first use.
