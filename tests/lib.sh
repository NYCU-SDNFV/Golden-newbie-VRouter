# Shared Lab 3 environment helpers.
set -eu
CONTAINER="${CONTAINER:-lab3}"

pass() { printf 'PASS  %s\n' "$1"; }
banner() { printf '\n=== %s ===\n' "$1"; }
die() {
    printf 'FAIL  %s\n' "$1" >&2
    if [ -n "${2:-}" ]; then printf '      hint: %s\n' "$2" >&2; fi
    exit 1
}
dexec() { docker exec "$CONTAINER" "$@"; }
container_running() {
    [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" = true ]
}
require_container() {
    container_running || die "container '$CONTAINER' is not running" "run make up; use make logs if startup fails"
    aa=$(dexec sh -c 'if [ -r /proc/self/attr/current ]; then cat /proc/self/attr/current; fi' | tr -d '\000\r\n')
    case "$aa" in
        ""|unconfined*) ;;
        *) die "AppArmor profile '$aa' blocks namespace creation" "restore the supplied privileged Compose configuration, then recreate the container" ;;
    esac
    limit=$(dexec sh -c 'ulimit -n')
    case "$limit" in
        ""|*[!0-9]*) die "unexpected nofile limit: $limit" "use the supplied Compose file (65536)" ;;
    esac
    [ "$limit" -le 65536 ] || die "nofile=$limit exceeds the course limit" "recreate the container using the supplied Compose file"
}
