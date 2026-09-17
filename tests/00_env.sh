#!/bin/sh
. "$(dirname "$0")/lib.sh"
banner "Lab 3 environment"
require_container
for tool in python3 ip ovs-vsctl ovs-ofctl ovs-appctl vtysh tcpdump ethtool ping iperf3; do
    dexec sh -c "command -v $tool >/dev/null" || die "'$tool' is missing" "pull the formal course base image and rebuild"
done
dexec test -x /usr/lib/frr/zebra || die "FRR zebra is missing"
dexec test -x /usr/lib/frr/bgpd || die "FRR bgpd is missing"
dexec test -f /workspace/harness/lab3.py || die "the repository is not mounted at /workspace"
dexec python3 -c 'import lab_resources' || die "the cached course image predates resource normalization" "docker compose pull does not refresh a FROM image; run docker pull ghcr.io/nycu-sdnfv/lab-base:115-1, then make build"
dexec ovs-appctl -t ovs-vswitchd version
dexec /usr/lib/frr/bgpd --version
pass "FRR, OVS, namespace tools, packet capture, and the course runtime are available"
