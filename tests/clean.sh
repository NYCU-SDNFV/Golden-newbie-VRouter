#!/bin/sh
. "$(dirname "$0")/lib.sh"
failure=0
if container_running; then
    if ! dexec python3 harness/lab3.py clean; then
        printf 'FAIL  topology cleanup failed; removing the dedicated Compose container as well\n' >&2
        failure=1
    fi
fi
if ! docker compose down --remove-orphans; then
    printf 'FAIL  could not remove the Lab 3 Compose container\n' >&2
    failure=1
fi
if [ "$failure" -ne 0 ]; then exit 1; fi
pass "Lab 3 container/topology removed; results and captures retained"
