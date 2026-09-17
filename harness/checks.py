"""Protected, authoritative real-probe checks for Lab 3."""
import json
import os
import re
import signal
import subprocess
import time
import ipaddress
from pathlib import Path

from harness import oracle, runtime
from topo import lab3_topo


class CheckFailure(RuntimeError):
    pass


CUSTOMER_BPF = (
    "(src host 10.1.0.10 and dst host 10.3.0.10) or "
    "(ip6 and src host 2001:db8:1::10 and dst host 2001:db8:3::10) or "
    "(src host 10.1.0.10 and dst host 10.4.0.10) or "
    "(ip6 and src host 2001:db8:1::10 and dst host 2001:db8:4::10)"
)


def _run(argv, timeout=12, check=False):
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    try:
        proc = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout,
                              check=False, env=env)
    except subprocess.TimeoutExpired as exc:
        raise CheckFailure("command timed out: %s" % " ".join(argv)) from exc
    if check and proc.returncode:
        raise CheckFailure("%s failed (%d): %s" %
                           (" ".join(argv), proc.returncode, proc.stderr.strip()))
    return proc


def _ns(namespace, *argv, **kwargs):
    return _run(["ip", "netns", "exec", namespace] + list(argv), **kwargs)


def _state():
    status = runtime.status()
    if not status.get("deployed"):
        raise CheckFailure("Lab 3 is not deployed")
    if not status.get("healthy"):
        raise CheckFailure("Lab 3 runtime has an unhealthy owned process")
    return status, runtime.load_json(runtime.TOPOLOGY_FILE)


def _loss(output):
    match = re.search(r"(\d+(?:\.\d+)?)% packet loss", output)
    if not match:
        raise CheckFailure("ping did not report packet loss: %s" % output[-300:])
    return float(match.group(1))


def _ping(namespace, destination, family=4, count=3, size=None, mtu=False):
    argv = ["ping", "-n", "-c", str(count), "-W", "1"]
    if family == 6:
        argv.append("-6")
    if size is not None:
        argv.extend(["-s", str(size)])
    if mtu:
        argv.extend(["-M", "do"])
    argv.append(destination)
    proc = _ns(namespace, *argv, timeout=count + 5)
    text = proc.stdout + proc.stderr
    return {"returncode": proc.returncode, "loss_percent": _loss(text),
            "output": text[-500:]}


def _wait_ping(namespace, destination, family, timeout=8):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = _ping(namespace, destination, family, count=1)
        if last["loss_percent"] == 0:
            return last
        time.sleep(0.1)
    raise CheckFailure("forwarding did not converge to %s: %r" % (destination, last))


def _route(asn, destination, family):
    flag = "-6" if family == 6 else "-4"
    proc = _ns("l3-r%d" % asn, "ip", "-j", flag, "route", "get", destination,
               check=True)
    rows = json.loads(proc.stdout)
    if not rows:
        raise CheckFailure("AS%d has no route to %s" % (asn, destination))
    row = rows[0]
    return {"destination": destination, "gateway": row.get("gateway", ""),
            "device": row.get("dev", ""), "source": row.get("prefsrc", "")}


def _bgp(family):
    observations = {}
    prefixes = {}
    expected = {
        4: {
            1: {"10.2.0.0/24", "10.3.0.0/24"},
            2: {"10.1.0.0/24", "10.3.0.0/24"},
            3: {"10.1.0.0/24", "10.2.0.0/24"},
        },
        6: {
            1: {"2001:db8:2::/64", "2001:db8:3::/64"},
            2: {"2001:db8:1::/64", "2001:db8:3::/64"},
            3: {"2001:db8:1::/64", "2001:db8:2::/64"},
        },
    }
    for asn in (1, 2, 3):
        summary = runtime.bgp_summary(asn, family)
        observations["as%d" % asn] = summary["established"]
        flag = "-4" if family == 4 else "-6"
        proc = _ns("l3-r%d" % asn, "ip", "-j", flag, "route", "show",
                   "proto", "bgp", check=True)
        installed = {route["prefix"] for route in
                     oracle.parse_kernel_routes(proc.stdout, family)}
        prefixes["as%d" % asn] = {
            "installed": sorted(installed),
            "expected": sorted(expected[family][asn]),
            "missing": sorted(expected[family][asn] - installed),
        }
    passed = (all(value >= 2 for value in observations.values()) and
              all(not value["missing"] for value in prefixes.values()))
    return passed, {"established_peers": observations, "prefixes": prefixes,
                    "family": family}


def _require_plane(status, plane):
    if status["plane"] != plane:
        raise CheckFailure("check requires --plane %s (deployed plane is %s)" %
                           (plane, status["plane"]))


def _dump_flows(bridge):
    proc = _run(["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", bridge],
                check=True)
    return oracle.parse_ovs_flows(proc.stdout)


def _kernel_bgp_routes(asn):
    routes = []
    for family, flag in ((4, "-4"), (6, "-6")):
        proc = _ns("l3-r%d" % asn, "ip", "-j", flag, "route", "show",
                   "proto", "bgp", check=True)
        routes.extend(oracle.parse_kernel_routes(proc.stdout, family))
    return routes


def _expected_route_flows(asn, topology):
    entry = topology["ases"][str(asn)]
    neighbors = oracle.parse_kernel_neighbors(
        _ns(entry["namespace"], "ip", "-j", "neigh", "show", check=True).stdout)
    interfaces = {}
    for name, speaker in entry["speaker_ports"].items():
        if name != "lan":
            interfaces[speaker["namespace_if"]] = (
                speaker, entry["external_ports"][name])
    expected = {}
    unresolved = []
    for route in _kernel_bgp_routes(asn):
        mapping = interfaces.get(route["device"])
        key = (route["family"], route["prefix"])
        neighbor_key = ((route["device"], str(ipaddress.ip_address(route["gateway"])))
                        if route["gateway"] else None)
        if mapping is None or neighbor_key not in neighbors:
            unresolved.append({
                "family": route["family"], "prefix": route["prefix"],
                "device": route["device"], "gateway": route["gateway"],
            })
            continue
        speaker, external = mapping
        expected[key] = {
            "family": route["family"], "prefix": route["prefix"],
            "priority": oracle.flow_priority(route["prefix"]),
            "eth_src": speaker["mac"].lower(),
            "eth_dst": neighbors[neighbor_key],
            "output": external["ofport"], "dec_ttl": True,
            "gateway": route["gateway"], "device": route["device"],
        }
    return expected, unresolved


def _compare_real_flows(asn, topology):
    expected, unresolved = _expected_route_flows(asn, topology)
    entry = topology["ases"][str(asn)]
    all_flows = _dump_flows(entry["bridge"])
    actual_list = [
        flow for flow in all_flows
        if flow["route_cookie"] and flow["priority"] < 2000 and flow["prefix"]
    ]
    actual = {(flow["family"], flow["prefix"]): flow for flow in actual_list}
    duplicate = []
    for key in set((flow["family"], flow["prefix"]) for flow in actual_list):
        matches = [flow for flow in actual_list
                   if (flow["family"], flow["prefix"]) == key]
        if len(matches) > 1:
            duplicate.append({"family": key[0], "prefix": key[1],
                              "count": len(matches)})
    unowned_route_like = [
        flow for flow in all_flows
        if flow["family"] in (4, 6) and flow["priority"] >= 1000 and
        not flow["route_cookie"]
    ]
    malformed_route_cookie = [
        flow for flow in all_flows
        if flow["route_cookie"] and flow["priority"] < 2000 and not flow["prefix"]
    ]
    missing = []
    wrong = []
    less_specific = []
    for key, wanted in expected.items():
        observed = actual.get(key)
        if observed is None:
            missing.append({"family": key[0], "prefix": key[1]})
            network = ipaddress.ip_network(key[1])
            for flow in actual_list:
                if flow["family"] == key[0]:
                    candidate = ipaddress.ip_network(flow["prefix"])
                    if network.subnet_of(candidate) and candidate != network:
                        less_specific.append({
                            "expected": key[1], "actual": flow["prefix"],
                            "actual_priority": flow["priority"],
                        })
            continue
        fields = ("priority", "eth_src", "eth_dst", "output", "dec_ttl")
        differences = {
            field: {"expected": wanted[field], "actual": observed[field]}
            for field in fields if observed[field] != wanted[field]
        }
        if differences:
            wrong.append({"family": key[0], "prefix": key[1],
                          "differences": differences, "raw": observed["raw"]})
    stale = [
        {"family": key[0], "prefix": key[1], "raw": flow["raw"]}
        for key, flow in actual.items() if key not in expected
    ]
    return {
        "expected": list(expected.values()), "actual": actual_list,
        "unresolved_neighbors": unresolved, "missing": missing,
        "stale": stale, "wrong": wrong, "less_specific": less_specific,
        "duplicate": duplicate, "unowned_route_like": unowned_route_like,
        "malformed_route_cookie": malformed_route_cookie,
        "passed": bool(expected) and not any(
            (unresolved, missing, stale, wrong, less_specific, duplicate,
             unowned_route_like, malformed_route_cookie)),
    }


def _controller_connected(bridge):
    raw = _run(["ovs-vsctl", "get", "Bridge", bridge, "controller"],
               check=True).stdout.strip()
    identifiers = re.findall(r"[0-9a-f]{8}-[0-9a-f-]{27,}", raw, re.I)
    if not identifiers:
        return False
    return all(
        _run(["ovs-vsctl", "get", "Controller", identifier, "is_connected"],
             check=True).stdout.strip().lower() == "true"
        for identifier in identifiers)


def _route_flow(bridge, family, prefix):
    matches = [
        flow for flow in _dump_flows(bridge)
        if flow["route_cookie"] and flow["family"] == family and
        flow["prefix"] == prefix and flow["priority"] < 2000
    ]
    if len(matches) != 1:
        raise CheckFailure("expected one route flow for %s on %s, found %d" %
                           (prefix, bridge, len(matches)))
    return matches[0]


def _capture_counts(text):
    lines = [line for line in text.splitlines() if line.strip()]
    return {
        4: sum(1 for line in lines if re.search(r"\bIP\b", line)),
        6: sum(1 for line in lines if re.search(r"\bIP6\b", line)),
    }


def _wait_capture_records(capture, families, process, timeout=2):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = _run(["tcpdump", "-nn", "-r", str(capture)], timeout=2)
        counts = _capture_counts(last.stdout)
        if last.returncode == 0 and all(counts[family] > 0 for family in families):
            return counts
        if process.poll() is not None:
            raise CheckFailure("capture exited before the required packet records were written")
        time.sleep(0.05)
    detail = (last.stderr.strip() if last is not None else "no capture read completed")
    raise CheckFailure("capture lacks required IPv%s packet records: %s" %
                       ("/".join(str(family) for family in families), detail))


def _capture_customer(interface, label, probe, families=(4, 6)):
    capture = runtime.CAPTURES / ("%s-%d.pcap" % (label, time.time_ns()))
    log_path = runtime.RESULTS / (label + "-tcpdump.log")
    log = log_path.open("ab", buffering=0)
    process = subprocess.Popen(
        ["tcpdump", "--immediate-mode", "-U", "-i", interface, "-nn",
         "-w", str(capture), CUSTOMER_BPF],
        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    log.close()
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise CheckFailure("%s capture exited before probe" % label)
            if capture.is_file() and capture.stat().st_size >= 24:
                break
            time.sleep(0.05)
        else:
            raise CheckFailure("%s capture did not become ready" % label)
        measurements = probe()
        _wait_capture_records(capture, families, process)
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=2)
    decoded = _run(["tcpdump", "-nn", "-r", str(capture)], check=True)
    lines = [line for line in decoded.stdout.splitlines() if line.strip()]
    counts = _capture_counts(decoded.stdout)
    return measurements, {
        "interface": interface,
        "capture": str(capture.relative_to(runtime.ROOT)),
        "customer_packets": len(lines),
        "ipv4_packets": counts[4],
        "ipv6_packets": counts[6],
        "decoded": decoded.stdout[-1200:],
    }


def _ping_case(source, destination, family, kind):
    status, _ = _state()
    result = _ping(source, destination, family)
    route = None
    if kind != "intra":
        asn = int(source.split("h", 1)[1][0])
        route = _route(asn, destination, family)
    return result["loss_percent"] == 0, {
        "probe": result, "route": route, "plane": status["plane"], "kind": kind,
    }


def _fib_sync():
    status, topology = _state()
    _require_plane(status, "ovs")
    controller = runtime.load_json(runtime.STATE_DIR / "controller.json")
    comparisons = {}
    age = time.time() - float(controller.get("updated", 0))
    passed = not controller.get("last_error") and 0 <= age <= 3
    for asn in (1, 2, 3):
        comparisons["as%d" % asn] = _compare_real_flows(asn, topology)
        passed = passed and comparisons["as%d" % asn]["passed"]
    return passed, {"comparisons": comparisons,
                    "controller_error": controller.get("last_error", ""),
                    "controller_state_age_seconds": age}


def _ttl(family):
    status, _ = _state()
    _require_plane(status, "ovs")
    destination = "10.3.0.10" if family == 4 else "2001:db8:3::10"
    probes = []
    pattern = "Time to live exceeded" if family == 4 else "Time exceeded"
    for lifetime in (1, 2, 3):
        argv = ["ping", "-n", "-c", "1", "-W", "2"]
        if family == 6:
            argv.append("-6")
        argv.extend(["-t", str(lifetime), destination])
        proc = _ns("l3-h1a", *argv, timeout=6)
        text = proc.stdout + proc.stderr
        probes.append({
            "ttl_or_hop_limit": lifetime, "returncode": proc.returncode,
            "time_exceeded_visible": pattern.lower() in text.lower(),
            "output": text[-500:],
        })
    delivered = _ping("l3-h1a", destination, family, count=1)
    passed = (all(item["time_exceeded_visible"] and item["returncode"] != 0
                  for item in probes) and delivered["loss_percent"] == 0)
    return passed, {
        "family": family, "probes": probes, "delivered_with_default_lifetime": delivered,
        "time_exceeded_visible": all(item["time_exceeded_visible"] for item in probes),
        "expected_l3_hops": 3,
        "note": "OVS decrements at every AS L3 hop; FRR need not be in the data path.",
    }


def _vxlan():
    _, topology = _state()
    capture = runtime.CAPTURES / ("vxlan-%d.pcap" % int(time.time()))
    interface = topology["links"]["12"]["interfaces"][0]
    command = ["tcpdump", "-U", "-i", interface, "-nn", "-c", "1",
               "-w", str(capture), "udp", "port", "4789"]
    log = (runtime.RESULTS / "vxlan-tcpdump.log").open("ab", buffering=0)
    process = subprocess.Popen(command, stdout=log, stderr=log,
                               stdin=subprocess.DEVNULL, start_new_session=True)
    log.close()
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise CheckFailure("VXLAN capture exited before probe")
            if capture.is_file() and capture.stat().st_size >= 24:
                break
            time.sleep(0.05)
        else:
            raise CheckFailure("VXLAN capture did not become ready")
        small = _ping("l3-h1a", "192.168.99.3", 4, count=2)
        try:
            process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=2)
        boundary = _ping("l3-h1a", "192.168.99.3", 4, count=2,
                         size=1422, mtu=True)
        too_large = _ping("l3-h1a", "192.168.99.3", 4, count=1,
                          size=1423, mtu=True)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2)
    decoded = _run(["tcpdump", "-nn", "-r", str(capture)], check=False)
    text = decoded.stdout + decoded.stderr
    vni_visible = bool(re.search(r"vni\s+100\b", text, re.I))
    passed = (small["loss_percent"] == 0 and boundary["loss_percent"] == 0 and
              too_large["returncode"] != 0 and vni_visible and capture.stat().st_size > 24)
    return passed, {
        "interface": interface, "capture": str(capture.relative_to(runtime.ROOT)),
        "udp_port": 4789, "vni": 100, "vni_visible": vni_visible,
        "outer_family": "IPv4", "underlay_mtu": 1500, "vxlan_mtu": 1450,
        "small": small, "boundary_payload_1422": boundary,
        "oversize_payload_1423": too_large,
        "decoded": text[-600:],
    }


def _wait_route(asn, destination, family, expected_device=None, absent=False, timeout=18):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            route = _route(asn, destination, family)
            last = route
            if not absent and (expected_device is None or route["device"] == expected_device):
                return route
        except (CheckFailure, json.JSONDecodeError):
            if absent:
                return {"absent": True}
        time.sleep(0.25)
    if absent:
        raise CheckFailure("route remained present: %r" % last)
    raise CheckFailure("route did not converge via %s: %r" % (expected_device, last))


def _wait_route_flow(bridge, family, prefix, present, timeout=8):
    deadline = time.monotonic() + timeout
    last = []
    while time.monotonic() < deadline:
        last = [
            flow for flow in _dump_flows(bridge)
            if flow["route_cookie"] and flow["priority"] < 2000 and
            flow["family"] == family and flow["prefix"] == prefix
        ]
        if bool(last) == present:
            return last[0] if last else {"absent": True}
        time.sleep(0.1)
    raise CheckFailure("flow %s presence=%s did not converge: %r" %
                       (prefix, present, last))


def _dual_probe(destination_as):
    return {
        "ipv4": _ping("l3-h1a", "10.%d.0.10" % destination_as, 4),
        "ipv6": _ping("l3-h1a", "2001:db8:%d::10" % destination_as, 6),
    }


def _wait_forwarding_convergence(topology, destination_as=3):
    def ready():
        observations = {
            str(asn): _compare_real_flows(asn, topology) for asn in (1, 2, 3)
        }
        return observations if all(value["passed"] for value in observations.values()) else False

    flows = runtime.wait_for("actual forwarding rules after route change", ready,
                             timeout=12, interval=0.1)
    probes = {
        "ipv4": _wait_ping("l3-h1a", "10.%d.0.10" % destination_as, 4),
        "ipv6": _wait_ping("l3-h1a", "2001:db8:%d::10" % destination_as, 6),
    }
    return {"flows": flows, "first_reachable": probes}


def _withdrawal():
    status, topology = _state()
    _require_plane(status, "ovs")
    before = {
        "ipv4_route": _route(1, "10.3.0.10", 4),
        "ipv6_route": _route(1, "2001:db8:3::10", 6),
        "ipv4_flow": _route_flow("br-l3-1", 4, "10.3.0.0/24"),
        "ipv6_flow": _route_flow("br-l3-1", 6, "2001:db8:3::/64"),
    }
    started = time.monotonic()
    try:
        runtime.vtysh(3, [
            "configure terminal", "router bgp 65003",
            "address-family ipv4 unicast", "no network 10.3.0.0/24",
            "exit-address-family", "address-family ipv6 unicast",
            "no network 2001:db8:3::/64", "end",
        ])
        gone = {
            "ipv4_route": _wait_route(1, "10.3.0.10", 4, absent=True),
            "ipv6_route": _wait_route(1, "2001:db8:3::10", 6, absent=True),
            "ipv4_flow": _wait_route_flow(
                "br-l3-1", 4, "10.3.0.0/24", False),
            "ipv6_flow": _wait_route_flow(
                "br-l3-1", 6, "2001:db8:3::/64", False),
        }
        elapsed = time.monotonic() - started
        probe = _dual_probe(3)
        links_reachable = {
            interface: json.loads(_run(
                ["ip", "-j", "link", "show", "dev", interface],
                check=True).stdout)[0]["operstate"]
            for link in ("13", "23")
            for interface in topology["links"][link]["interfaces"]
        }
        peer_sessions = {
            "as%d-ipv%d" % (asn, family):
                runtime.bgp_summary(asn, family)["established"]
            for asn in (1, 2, 3) for family in (4, 6)
        }
        passed = (
            all(value["loss_percent"] == 100 for value in probe.values()) and
            all(value == "UP" for value in links_reachable.values()) and
            all(value >= 2 for value in peer_sessions.values()))
    finally:
        runtime.vtysh(3, [
            "configure terminal", "router bgp 65003",
            "address-family ipv4 unicast", "network 10.3.0.0/24",
            "exit-address-family", "address-family ipv6 unicast",
            "network 2001:db8:3::/64", "end",
        ])
        restored = {
            "ipv4_route": _wait_route(1, "10.3.0.10", 4, "r1-12"),
            "ipv6_route": _wait_route(1, "2001:db8:3::10", 6, "r1-12"),
            "ipv4_flow": _wait_route_flow(
                "br-l3-1", 4, "10.3.0.0/24", True),
            "ipv6_flow": _wait_route_flow(
                "br-l3-1", 6, "2001:db8:3::/64", True),
        }
        restore_probe = {
            "ipv4": _wait_ping("l3-h1a", "10.3.0.10", 4),
            "ipv6": _wait_ping("l3-h1a", "2001:db8:3::10", 6),
        }
    return passed, {
        "before": before, "withdrawn": gone, "withdraw_seconds": elapsed,
        "probe_while_withdrawn": probe, "restored": restored,
        "restore_probe": restore_probe, "links_reachable": links_reachable,
        "peer_sessions_during_withdrawal": peer_sessions,
        "plane": status["plane"],
    }


def _failover():
    status, topology = _state()
    before = {
        "ipv4": _route(1, "10.3.0.10", 4),
        "ipv6": _route(1, "2001:db8:3::10", 6),
    }
    started = time.monotonic()
    try:
        lab3_topo.set_link(topology, "12", False)
        backup = {
            "ipv4": _wait_route(1, "10.3.0.10", 4, "r1-13"),
            "ipv6": _wait_route(1, "2001:db8:3::10", 6, "r1-13"),
        }
        control_convergence = time.monotonic() - started
        ready = _wait_forwarding_convergence(topology)
        convergence = time.monotonic() - started
        probe, capture = _capture_customer(
            topology["links"]["13"]["interfaces"][0], "failover",
            lambda: _dual_probe(3))
        passed = (
            all(value["loss_percent"] == 0 for value in probe.values()) and
            capture["ipv4_packets"] > 0 and capture["ipv6_packets"] > 0 and
            all(value["device"] == "r1-13" for value in backup.values()))
    finally:
        lab3_topo.set_link(topology, "12", True)
        restored = {
            "ipv4": _wait_route(1, "10.3.0.10", 4, "r1-12"),
            "ipv6": _wait_route(1, "2001:db8:3::10", 6, "r1-12"),
        }
        restored_forwarding = _wait_forwarding_convergence(topology)
        restore_probe = {
            "ipv4": _wait_ping("l3-h1a", "10.3.0.10", 4),
            "ipv6": _wait_ping("l3-h1a", "2001:db8:3::10", 6),
        }
    return passed, {"before": before, "backup": backup,
                    "convergence_seconds": convergence, "probe": probe,
                    "control_convergence_seconds": control_convergence,
                    "data_convergence_seconds": convergence,
                    "forwarding_ready": ready,
                    "data_path_capture": capture,
                    "restored_forwarding": restored_forwarding,
                    "restored": restored, "restore_probe": restore_probe,
                    "plane": status["plane"]}


def _policy():
    _, topology = _state()
    before = {
        "ipv4": _route(1, "10.3.0.10", 4),
        "ipv6": _route(1, "2001:db8:3::10", 6),
    }
    try:
        runtime._scenario_unlocked("policy-backup")
        direct = {
            "ipv4": _wait_route(1, "10.3.0.10", 4, "r1-13"),
            "ipv6": _wait_route(1, "2001:db8:3::10", 6, "r1-13"),
        }
        ready = _wait_forwarding_convergence(topology)
        probes, capture = _capture_customer(
            topology["links"]["13"]["interfaces"][0], "policy",
            lambda: _dual_probe(3))
        passed = (all(value["loss_percent"] == 0 for value in probes.values()) and
                  capture["ipv4_packets"] > 0 and capture["ipv6_packets"] > 0)
    finally:
        runtime._scenario_unlocked("policy-default")
        restored = {
            "ipv4": _wait_route(1, "10.3.0.10", 4, "r1-12"),
            "ipv6": _wait_route(1, "2001:db8:3::10", 6, "r1-12"),
        }
        restored_forwarding = _wait_forwarding_convergence(topology)
        restore_probe = {
            "ipv4": _wait_ping("l3-h1a", "10.3.0.10", 4),
            "ipv6": _wait_ping("l3-h1a", "2001:db8:3::10", 6),
        }
    return passed, {"default": before, "backup_policy": direct, "restored": restored,
                    "forwarding_ready": ready,
                    "restored_forwarding": restored_forwarding,
                    "probe": probes, "data_path_capture": capture,
                    "restore_probe": restore_probe}


def _new_as():
    status, topology = _state()
    try:
        runtime._scenario_unlocked("add-as")
        route4 = _wait_route(1, "10.4.0.10", 4)
        route6 = _wait_route(1, "2001:db8:4::10", 6)
        added_flows = {}
        if status["plane"] == "ovs":
            added_flows = {
                "ipv4": _wait_route_flow(
                    "br-l3-1", 4, "10.4.0.0/24", True),
                "ipv6": _wait_route_flow(
                    "br-l3-1", 6, "2001:db8:4::/64", True),
            }
        probes, capture = _capture_customer(
            topology["links"]["24"]["interfaces"][0], "new-as",
            lambda: {
                "ipv4": _wait_ping("l3-h1a", "10.4.0.10", 4),
                "ipv6": _wait_ping("l3-h1a", "2001:db8:4::10", 6),
            })
        probe4, probe6 = probes["ipv4"], probes["ipv6"]
        passed = probe4["loss_percent"] == 0 and probe6["loss_percent"] == 0
        passed = (passed and capture["ipv4_packets"] > 0 and
                  capture["ipv6_packets"] > 0)
    finally:
        runtime._scenario_unlocked("remove-as")
        removed4 = _wait_route(1, "10.4.0.10", 4, absent=True)
        removed6 = _wait_route(1, "2001:db8:4::10", 6, absent=True)
        removed_flows = {}
        if status["plane"] == "ovs":
            removed_flows = {
                "ipv4": _wait_route_flow(
                    "br-l3-1", 4, "10.4.0.0/24", False),
                "ipv6": _wait_route_flow(
                    "br-l3-1", 6, "2001:db8:4::/64", False),
            }
    return passed, {"route4": route4, "route6": route6,
                    "probe4": probe4, "probe6": probe6,
                    "data_path_capture": capture,
                    "added_flows": added_flows,
                    "removed4": removed4, "removed6": removed6,
                    "removed_flows": removed_flows}


def _control_down():
    status, topology = _state()
    _require_plane(status, "ovs")
    state = runtime.load_json(runtime.RUNTIME_FILE)
    pid = state["controller"]["pid"]
    if not runtime._require_owned(pid, ["osken-manager", "controller.py"]):
        raise CheckFailure("refusing to signal non-owned controller PID %d" % pid)
    warmups = {
        "v4": _ping("l3-h1a", "10.3.0.10", 4, count=1),
        "v6": _ping("l3-h1a", "2001:db8:3::10", 6, count=1),
    }
    if any(item["loss_percent"] for item in warmups.values()):
        raise CheckFailure("could not prime gateway neighbor state before disconnect")
    configured_before = {
        entry["bridge"]: _run(
            ["ovs-vsctl", "get-controller", entry["bridge"]], check=True
        ).stdout.strip()
        for entry in topology["ases"].values()
    }
    if not all(configured_before.values()):
        raise CheckFailure("the test must retain a configured controller on every bridge")
    forwards = {}
    before_flows = {
        "ipv4": _route_flow("br-l3-2", 4, "10.3.0.0/24"),
        "ipv6": _route_flow("br-l3-2", 6, "2001:db8:3::/64"),
    }
    capture = runtime.CAPTURES / ("control-down-%d.pcap" % int(time.time()))
    capture_log = (runtime.RESULTS / "control-down-tcpdump.log").open("ab", buffering=0)
    sniffer = subprocess.Popen([
        "ip", "netns", "exec", "l3-r2", "tcpdump", "-U", "-i", "any", "-nn",
        "-w", str(capture),
        CUSTOMER_BPF,
    ], stdin=subprocess.DEVNULL, stdout=capture_log, stderr=capture_log,
       start_new_session=True)
    capture_log.close()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if sniffer.poll() is not None:
            raise CheckFailure("speaker capture exited before probes")
        if capture.is_file() and capture.stat().st_size >= 24:
            break
        time.sleep(0.05)
    else:
        sniffer.terminate()
        sniffer.wait(timeout=2)
        raise CheckFailure("speaker capture did not become ready")
    try:
        os.kill(pid, signal.SIGSTOP)
        stopped = runtime.wait_for(
            "controller process to stop",
            lambda: bool(re.search(
                r"(?m)^State:\s+T\b",
                Path("/proc/%d/status" % pid).read_text(encoding="ascii"))),
            timeout=2, interval=0.01)
        controllers_during = {
            entry["bridge"]: _run(
                ["ovs-vsctl", "get-controller", entry["bridge"]], check=True
            ).stdout.strip()
            for entry in topology["ases"].values()
        }
        forwards["v4"] = _ping("l3-h1a", "10.3.0.10", 4)
        forwards["v6"] = _ping("l3-h1a", "2001:db8:3::10", 6)
        after_flows = {
            "ipv4": _route_flow("br-l3-2", 4, "10.3.0.0/24"),
            "ipv6": _route_flow("br-l3-2", 6, "2001:db8:3::/64"),
        }
    finally:
        if runtime._require_owned(pid, ["osken-manager", "controller.py"]):
            os.kill(pid, signal.SIGCONT)
        try:
            runtime.wait_for(
                "controller reconnection after control-down check",
                lambda: all(_controller_connected(entry["bridge"])
                            for entry in topology["ases"].values()),
                timeout=8, interval=0.1)
        finally:
            if sniffer.poll() is None:
                sniffer.terminate()
            sniffer.wait(timeout=2)
    decoded = _run(["tcpdump", "-nn", "-r", str(capture)], check=False)
    captured_data = [line for line in decoded.stdout.splitlines() if line.strip()]
    forwarding = all(item["loss_percent"] == 0 for item in forwards.values())
    sysctls = {}
    for asn in (1, 2, 3):
        v4 = _ns("l3-r%d" % asn, "sysctl", "-n", "net.ipv4.ip_forward",
                 check=True).stdout.strip()
        v6 = _ns("l3-r%d" % asn, "sysctl", "-n",
                 "net.ipv6.conf.all.forwarding", check=True).stdout.strip()
        sysctls["as%d" % asn] = {"ipv4": int(v4), "ipv6": int(v6)}
    speakers_disabled = all(value == {"ipv4": 0, "ipv6": 0}
                            for value in sysctls.values())
    deltas = {
        family: after_flows[family]["packets"] - before_flows[family]["packets"]
        for family in before_flows
    }
    route_activity = all(value > 0 for value in deltas.values())
    before_total = sum(flow["packets"] for flow in before_flows.values())
    after_total = sum(flow["packets"] for flow in after_flows.values())
    retained = controllers_during == configured_before
    return forwarding and speakers_disabled and route_activity and retained and stopped and not captured_data, {
        "controller_pid": pid, "controller_sigstop": True,
        "controllers_during_disconnect": controllers_during,
        "controller_config_retained": retained,
        "controller_process_stopped": stopped,
        "disconnect_method": "SIGSTOP; controller addresses retained to avoid flushing OVS flows",
        "warmups": warmups, "probes": forwards, "speaker_forwarding": sysctls,
        "ovs_packets_before": before_total, "ovs_packets_after": after_total,
        "route_flows_before": before_flows, "route_flows_after": after_flows,
        "route_flow_packet_deltas": deltas,
        "speaker_capture": str(capture.relative_to(runtime.ROOT)),
        "ordinary_packets_seen_in_speaker": len(captured_data),
        "ordinary_forwarding_survived_control_disconnect": forwarding,
    }


def _transit(family):
    status, topology = _state()
    destination = "10.3.0.10" if family == 4 else "2001:db8:3::10"
    prefix = "10.3.0.0/24" if family == 4 else "2001:db8:3::/64"
    before = None
    if status["plane"] == "ovs":
        before = _route_flow("br-l3-2", family, prefix)

    def probe():
        return _ping("l3-h1a", destination, family)

    ping, capture = _capture_customer(
        topology["links"]["23"]["interfaces"][0],
        "transit%d" % family, probe, families=(family,))
    family_packets = capture["ipv4_packets"] if family == 4 else capture["ipv6_packets"]
    passed = ping["loss_percent"] == 0 and family_packets > 0
    measurements = {
        "probe": ping, "route": _route(1, destination, family),
        "plane": status["plane"], "kind": "transit",
        "as2_customer_capture": capture,
    }
    if status["plane"] == "ovs":
        after = _route_flow("br-l3-2", family, prefix)
        measurements["route_flow_before"] = before
        measurements["route_flow_after"] = after
        measurements["route_flow_packet_delta"] = after["packets"] - before["packets"]
        passed = passed and after["packets"] > before["packets"]
    return passed, measurements


def run_case(case):
    status, _ = _state()
    dispatch = {
        "bgp4": lambda: _bgp(4),
        "bgp6": lambda: _bgp(6),
        "frr4": lambda: (_require_plane(status, "frr") or
                         _ping_case("l3-h1a", "10.3.0.10", 4, "inter")),
        "frr6": lambda: (_require_plane(status, "frr") or
                         _ping_case("l3-h1a", "2001:db8:3::10", 6, "inter")),
        "intra4": lambda: _ping_case("l3-h1a", "10.1.0.11", 4, "intra"),
        "intra6": lambda: _ping_case("l3-h1a", "2001:db8:1::11", 6, "intra"),
        "inter4": lambda: _ping_case("l3-h1a", "10.2.0.10", 4, "inter"),
        "inter6": lambda: _ping_case("l3-h1a", "2001:db8:2::10", 6, "inter"),
        "transit4": lambda: _transit(4),
        "transit6": lambda: _transit(6),
        "vxlan": _vxlan,
        "ttl4": lambda: _ttl(4),
        "ttl6": lambda: _ttl(6),
        "fib-sync": _fib_sync,
        "withdrawal": _withdrawal,
        "failover": _failover,
        "policy": _policy,
        "new-as": _new_as,
        "control-down": _control_down,
    }
    if case not in dispatch:
        raise ValueError("unknown check case %s" % case)
    if case in {"withdrawal", "failover", "policy", "new-as", "control-down"}:
        with runtime.lifecycle_lock():
            return dispatch[case]()
    return dispatch[case]()
