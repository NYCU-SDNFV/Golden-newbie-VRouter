#!/bin/sh
. "$(dirname "$0")/lib.sh"
i=0
while [ "$i" -lt 30 ]; do
    if container_running && dexec ovs-appctl -t ovs-vswitchd version >/dev/null 2>&1; then
        require_container
        pass "container '$CONTAINER' and ovs-vswitchd are ready"
        exit 0
    fi
    i=$((i + 1))
    sleep 1
done
die "container '$CONTAINER' did not become ready in 30 seconds" "run make logs; do not hide the startup error"
