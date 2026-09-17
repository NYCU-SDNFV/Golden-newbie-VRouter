"""Create and remove the persistent Lab 3 namespace/OVS topology.

All resources carry the l3- or br-l3- prefix and are enumerated in the state
file.  This module never kills processes and never removes unrecorded objects.
"""
import json
import os
import re
import subprocess
from pathlib import Path

from topo.model import AS, LINKS, VXLAN, bare, links_for, speaker_mac

STATE_DIR = Path("/run/lab3")


class CommandError(RuntimeError):
    pass


def run(argv, timeout=15, check=True, capture=True):
    try:
        proc = subprocess.run(
            argv, text=True, timeout=timeout, check=False,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except subprocess.TimeoutExpired as exc:
        raise CommandError("timeout: %s" % " ".join(argv)) from exc
    if check and proc.returncode:
        raise CommandError("%s failed (%d): %s" % (
            " ".join(argv), proc.returncode, (proc.stderr or "").strip()))
    return proc


def ns_exec(namespace, *argv, **kwargs):
    return run(["ip", "netns", "exec", namespace] + list(argv), **kwargs)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".new")
    with pending.open("w", encoding="ascii", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(pending), str(path))


def _must_absent(kind, name):
    if kind == "netns":
        present = name in run(["ip", "netns", "list"]).stdout.split()
    else:
        present = run(["ovs-vsctl", "br-exists", name], check=False).returncode == 0
    if present:
        raise CommandError("refusing to reuse unowned %s %s; run clean with its state file" %
                           (kind, name))


def _disable_offloads(namespace, interface):
    result = ns_exec(
        namespace, "ethtool", "-K", interface,
        "tx", "off", "rx", "off", "tso", "off", "gso", "off", "gro", "off",
        "lro", "off", check=False,
    )
    if result.returncode and "not supported" not in (result.stderr or "").lower():
        raise CommandError("could not disable offloads on %s/%s: %s" %
                           (namespace, interface, result.stderr.strip()))


def _add_ns(state, name):
    _must_absent("netns", name)
    run(["ip", "netns", "add", name])
    state["namespaces"].append(name)
    atomic_json(STATE_DIR / "topology.json", state)
    ns_exec(name, "ip", "link", "set", "lo", "up")


def _add_bridge(state, asn, plane):
    name = "br-l3-%d" % asn
    _must_absent("bridge", name)
    run(["ovs-vsctl", "--may-exist", "add-br", name])
    state["bridges"].append(name)
    atomic_json(STATE_DIR / "topology.json", state)
    run(["ovs-vsctl", "set", "bridge", name, "protocols=OpenFlow13",
         "other-config:datapath-id=%016x" % asn,
         "fail_mode=secure"])


def temporary_peer_name(root_interface):
    """Return a unique root-namespace name used before moving a veth peer."""
    name = "v-" + root_interface
    if name == "eth0" or len(name) > 15:
        raise ValueError("cannot derive a safe temporary peer name from %s" %
                         root_interface)
    return name


def _veth_to_bridge(state, namespace, ns_if, root_if, bridge, mac=None):
    temporary = temporary_peer_name(root_if)
    run(["ip", "link", "add", root_if, "type", "veth", "peer", "name", temporary])
    state["root_interfaces"].append(root_if)
    atomic_json(STATE_DIR / "topology.json", state)
    run(["ip", "link", "set", temporary, "netns", namespace])
    ns_exec(namespace, "ip", "link", "set", temporary, "name", ns_if)
    if mac:
        ns_exec(namespace, "ip", "link", "set", "dev", ns_if, "address", mac)
    ns_exec(namespace, "ip", "link", "set", ns_if, "mtu", "1500")
    ns_exec(namespace, "ip", "link", "set", ns_if, "up")
    _disable_offloads(namespace, ns_if)
    run(["ip", "link", "set", root_if, "mtu", "1500"])
    run(["ip", "link", "set", root_if, "up"])
    run(["ovs-vsctl", "--may-exist", "add-port", bridge, root_if])


def _root_veth_link(state, name, left_bridge, right_bridge):
    left, right = "p%s-a" % name, "p%s-b" % name
    run(["ip", "link", "add", left, "type", "veth", "peer", "name", right])
    state["root_interfaces"].extend([left, right])
    atomic_json(STATE_DIR / "topology.json", state)
    for interface, bridge in ((left, left_bridge), (right, right_bridge)):
        run(["ip", "link", "set", interface, "mtu", "1500"])
        run(["ip", "link", "set", interface, "up"])
        run(["ethtool", "-K", interface, "tx", "off", "rx", "off", "tso", "off",
             "gso", "off", "gro", "off", "lro", "off"], check=False)
        run(["ovs-vsctl", "--may-exist", "add-port", bridge, interface])
    return left, right


def _ofport(interface):
    text = run(["ovs-vsctl", "get", "Interface", interface, "ofport"]).stdout.strip()
    value = int(text)
    if value <= 0:
        raise CommandError("OVS did not allocate an ofport for %s: %s" % (interface, text))
    return value


def _configure_frr_plane(state):
    """Isolate each LAN and peer segment while using OVS as plain L2."""
    for entry in state["ases"].values():
        bridge = entry["bridge"]
        run(["ovs-ofctl", "-O", "OpenFlow13", "del-flows", bridge])
        host_ports = [host["ofport"] for host in entry["hosts"]]
        lan_port = entry["speaker_ports"]["lan"]["ofport"]
        for port in host_ports:
            outputs = [value for value in host_ports + [lan_port] if value != port]
            actions = ",".join("output:%d" % value for value in outputs)
            run(["ovs-ofctl", "-O", "OpenFlow13", "add-flow", bridge,
                 "priority=100,in_port=%d,actions=%s" % (port, actions)])
        lan_actions = ",".join("output:%d" % value for value in host_ports)
        run(["ovs-ofctl", "-O", "OpenFlow13", "add-flow", bridge,
             "priority=100,in_port=%d,actions=%s" % (lan_port, lan_actions)])
        for name, external in entry["external_ports"].items():
            speaker = entry["speaker_ports"][name]
            run(["ovs-ofctl", "-O", "OpenFlow13", "add-flow", bridge,
                 "priority=100,in_port=%d,actions=output:%d" %
                 (speaker["ofport"], external["ofport"])])
            run(["ovs-ofctl", "-O", "OpenFlow13", "add-flow", bridge,
                 "priority=100,in_port=%d,actions=output:%d" %
                 (external["ofport"], speaker["ofport"])])


def ovs_bootstrap_flow_specs(entry):
    """Return control-only flows needed to establish BGP before os-ken starts."""
    specs = []
    for name, speaker in entry["speaker_ports"].items():
        if name == "lan":
            continue
        external = entry["external_ports"][name]
        specs.append("priority=4100,in_port=%d,actions=output:%d" %
                     (speaker["ofport"], external["ofport"]))
        for protocol in ("ip", "ipv6"):
            for direction in ("tp_src", "tp_dst"):
                specs.append(
                    "priority=4200,in_port=%d,%s,nw_proto=6,%s=179,"
                    "actions=output:%d" %
                    (external["ofport"], protocol, direction, speaker["ofport"]))
        specs.append("priority=4200,in_port=%d,arp,actions=output:%d" %
                     (external["ofport"], speaker["ofport"]))
        for icmp_type in (135, 136):
            specs.append(
                "priority=4200,in_port=%d,icmp6,icmp_type=%d,"
                "actions=output:%d" %
                (external["ofport"], icmp_type, speaker["ofport"]))
    return specs


def _configure_ovs_bootstrap(state):
    for entry in state["ases"].values():
        bridge = entry["bridge"]
        run(["ovs-ofctl", "-O", "OpenFlow13", "del-flows", bridge])
        for spec in ovs_bootstrap_flow_specs(entry):
            run(["ovs-ofctl", "-O", "OpenFlow13", "add-flow", bridge, spec])


def create(plane):
    """Create the topology and return its serializable metadata."""
    if plane not in ("frr", "ovs"):
        raise ValueError("plane must be frr or ovs")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "version": 1, "plane": plane, "namespaces": [], "bridges": [],
        "root_interfaces": [], "ases": {}, "links": {}, "vxlan": VXLAN,
    }
    atomic_json(STATE_DIR / "topology.json", state)
    try:
        for asn in sorted(AS):
            _add_ns(state, "l3-r%d" % asn)
            for host in AS[asn]["hosts"]:
                _add_ns(state, host["name"])
            _add_bridge(state, asn, plane)

        for asn, data in sorted(AS.items()):
            router = "l3-r%d" % asn
            bridge = "br-l3-%d" % asn
            entry = {
                "asn": data["asn"], "namespace": router, "bridge": bridge,
                "dpid": asn, "gw4": data["gw4"], "gw6": data["gw6"],
                "gw_mac": data["gw_mac"], "lan4": data["lan4"],
                "lan6": data["lan6"], "hosts": [], "speaker_ports": {},
                "external_ports": {},
            }
            lan_mac = speaker_mac(asn, "01")
            root_lan = "p%ds-lan" % asn
            _veth_to_bridge(state, router, "r%d-lan" % asn, root_lan, bridge, lan_mac)
            ns_exec(router, "ip", "addr", "add", data["gw4"] + "/24",
                    "dev", "r%d-lan" % asn)
            ns_exec(router, "ip", "-6", "addr", "add", data["gw6"] + "/64",
                    "dev", "r%d-lan" % asn)
            entry["speaker_ports"]["lan"] = {
                "root": root_lan, "namespace_if": "r%d-lan" % asn, "mac": lan_mac,
            }

            for index, host in enumerate(data["hosts"]):
                root_if = "p%dh%d" % (asn, index)
                ns_if = "eth0"
                _veth_to_bridge(state, host["name"], ns_if, root_if, bridge, host["mac"])
                ns_exec(host["name"], "ip", "addr", "add", host["ipv4"], "dev", ns_if)
                ns_exec(host["name"], "ip", "-6", "addr", "add", host["ipv6"], "dev", ns_if)
                ns_exec(host["name"], "ip", "route", "add", "default", "via", data["gw4"])
                ns_exec(host["name"], "ip", "-6", "route", "add", "default",
                        "via", data["gw6"])
                entry["hosts"].append(dict(host, root=root_if, namespace_if=ns_if))

            for link in links_for(asn):
                suffix = link["name"]
                ns_if = "r%d-%s" % (asn, suffix)
                root_if = "p%ds%s" % (asn, suffix)
                mac = speaker_mac(asn, suffix)
                _veth_to_bridge(state, router, ns_if, root_if, bridge, mac)
                ns_exec(router, "ip", "addr", "add", link["ipv4"], "dev", ns_if)
                ns_exec(router, "ip", "-6", "addr", "add", link["ipv6"], "dev", ns_if)
                entry["speaker_ports"][suffix] = {
                    "root": root_if, "namespace_if": ns_if, "mac": mac,
                    "ipv4": bare(link["ipv4"]), "ipv6": bare(link["ipv6"]),
                    "peer4": link["peer4"], "peer6": link["peer6"],
                    "peer_as": link["peer_as"],
                }
            ns_exec(router, "sysctl", "-q", "-w",
                    "net.ipv4.ip_forward=%d" % (1 if plane == "frr" else 0))
            ns_exec(router, "sysctl", "-q", "-w",
                    "net.ipv6.conf.all.forwarding=%d" % (1 if plane == "frr" else 0))
            ns_exec(router, "sysctl", "-q", "-w", "net.ipv4.conf.all.rp_filter=0")
            ns_exec(router, "sysctl", "-q", "-w", "net.ipv4.conf.default.rp_filter=0")
            for speaker in entry["speaker_ports"].values():
                ns_exec(router, "sysctl", "-q", "-w",
                        "net.ipv4.conf.%s.rp_filter=0" % speaker["namespace_if"])
            state["ases"][str(asn)] = entry

        for name, link in LINKS.items():
            left_as, right_as = link["ends"]
            left, right = _root_veth_link(
                state, name, "br-l3-%d" % left_as, "br-l3-%d" % right_as)
            state["links"][name] = {
                "ends": [left_as, right_as], "interfaces": [left, right],
                "dormant": bool(link.get("dormant")),
            }
            state["ases"][str(left_as)]["external_ports"][name] = {"root": left}
            state["ases"][str(right_as)]["external_ports"][name] = {"root": right}

        for entry in state["ases"].values():
            for port in entry["speaker_ports"].values():
                port["ofport"] = _ofport(port["root"])
            for host in entry["hosts"]:
                host["ofport"] = _ofport(host["root"])
            for port in entry["external_ports"].values():
                port["ofport"] = _ofport(port["root"])

        if plane == "frr":
            _configure_frr_plane(state)
        else:
            _configure_ovs_bootstrap(state)

        atomic_json(STATE_DIR / "topology.json", state)
        return state
    except (CommandError, OSError, ValueError) as create_error:
        try:
            remove(state)
        except CommandError as rollback_error:
            raise CommandError("%s; rollback failed: %s; state preserved in %s" %
                               (create_error, rollback_error,
                                STATE_DIR / "topology.json")) from create_error
        raise


def add_vxlan():
    for side in ("left", "right"):
        data = VXLAN[side]
        ns = data["namespace"]
        ns_exec(ns, "ip", "link", "add", "vxlan100", "type", "vxlan",
                "id", str(VXLAN["vni"]), "local", data["local"],
                "remote", data["remote"], "dstport", str(VXLAN["port"]),
                "dev", "eth0", "nolearning")
        ns_exec(ns, "ip", "link", "set", "vxlan100", "mtu", str(VXLAN["mtu"]))
        ns_exec(ns, "ip", "addr", "add", data["overlay"], "dev", "vxlan100")
        ns_exec(ns, "ip", "link", "set", "vxlan100", "up")


def set_link(state, name, up):
    if name not in state["links"]:
        raise ValueError("unknown link %s" % name)
    word = "up" if up else "down"
    for interface in state["links"][name]["interfaces"]:
        run(["ip", "link", "set", interface, word])


def set_speaker_forwarding(state, enabled):
    value = "1" if enabled else "0"
    for entry in state["ases"].values():
        ns_exec(entry["namespace"], "sysctl", "-q", "-w",
                "net.ipv4.ip_forward=" + value)
        ns_exec(entry["namespace"], "sysctl", "-q", "-w",
                "net.ipv6.conf.all.forwarding=" + value)


def remove(state):
    validators = (
        ("namespace", state.get("namespaces", []), r"l3-(?:r[1-4]|h(?:1a|1b|2|3|4))"),
        ("bridge", state.get("bridges", []), r"br-l3-[1-4]"),
        ("interface", state.get("root_interfaces", []),
         r"p(?:[1-4](?:s(?:-lan|12|13|23|24)|h[01])|(?:12|13|23|24)-[ab])"),
    )
    for kind, names, pattern in validators:
        invalid = [name for name in names if re.fullmatch(pattern, name) is None]
        if invalid:
            raise CommandError("refusing to remove unowned %s names: %s" %
                               (kind, ", ".join(invalid)))
    errors = []
    for bridge in reversed(state.get("bridges", [])):
        result = run(["ovs-vsctl", "--if-exists", "del-br", bridge], check=False)
        if result.returncode:
            errors.append("del-br %s: %s" % (bridge, result.stderr.strip()))
    listed_links = run(["ip", "-o", "link", "show"], check=False)
    if listed_links.returncode:
        errors.append("list-links: %s" % listed_links.stderr.strip())
        existing_interfaces = set()
    else:
        existing_interfaces = {
            line.split()[1].rstrip(":").split("@", 1)[0]
            for line in listed_links.stdout.splitlines() if len(line.split()) >= 2
        }
    for interface in state.get("root_interfaces", []):
        if interface not in existing_interfaces:
            continue
        result = run(["ip", "link", "del", interface], check=False)
        if result.returncode:
            errors.append("del-link %s: %s" % (interface, result.stderr.strip()))
        else:
            existing_interfaces.discard(interface)
            if interface.endswith("-a"):
                existing_interfaces.discard(interface[:-1] + "b")
            elif interface.endswith("-b"):
                existing_interfaces.discard(interface[:-1] + "a")
    listed = run(["ip", "netns", "list"], check=False)
    if listed.returncode:
        errors.append("list-netns: %s" % listed.stderr.strip())
        existing_namespaces = set()
    else:
        existing_namespaces = {
            line.split()[0] for line in listed.stdout.splitlines() if line.split()
        }
    for namespace in reversed(state.get("namespaces", [])):
        if namespace not in existing_namespaces:
            continue
        result = run(["ip", "netns", "del", namespace], check=False)
        if result.returncode:
            errors.append("del-netns %s: %s" % (namespace, result.stderr.strip()))
    if errors:
        raise CommandError("; ".join(errors))
