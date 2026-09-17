"""Protected parsers used by grading oracles.

This module intentionally has no dependency on editable student modules.
"""
import ipaddress
import json
import re


def parse_kernel_routes(payload, family=None):
    rows = json.loads(payload) if isinstance(payload, str) else payload
    routes = []
    for row in rows:
        if row.get("type", "unicast") != "unicast":
            continue
        destination = row.get("dst", "default")
        guessed = 6 if ":" in destination or ":" in row.get("gateway", "") else 4
        value = row.get("family", guessed)
        if isinstance(value, str) and not value.isdigit():
            current = {"inet": 4, "inet6": 6}.get(value)
            if current is None:
                continue
        else:
            current = int(value)
        if family and current != family:
            continue
        if destination == "default":
            destination = "::/0" if current == 6 else "0.0.0.0/0"
        prefix = str(ipaddress.ip_network(destination, strict=False))
        for hop in row.get("nexthops") or [row]:
            device = hop.get("dev") or row.get("dev")
            if not device:
                continue
            routes.append({
                "family": current,
                "prefix": prefix,
                "gateway": hop.get("gateway", row.get("gateway", "")),
                "device": device,
                "protocol": str(row.get("protocol", "kernel")),
                "metric": int(hop.get("metric", row.get("metric", 0)) or 0),
            })
    selected = {}
    for route in routes:
        key = (route["family"], route["prefix"])
        rank = (route["metric"], route["gateway"], route["device"])
        if key not in selected or rank < (
                selected[key]["metric"], selected[key]["gateway"],
                selected[key]["device"]):
            selected[key] = route
    return sorted(selected.values(), key=lambda route: (
        route["family"], ipaddress.ip_network(route["prefix"]).network_address,
        -ipaddress.ip_network(route["prefix"]).prefixlen))


def parse_kernel_neighbors(payload):
    rows = json.loads(payload) if isinstance(payload, str) else payload
    usable = {}
    rejected = {"FAILED", "INCOMPLETE", "NONE"}
    for row in rows:
        raw_states = row.get("state", [])
        states = {raw_states} if isinstance(raw_states, str) else set(raw_states)
        if (row.get("dst") and row.get("dev") and row.get("lladdr") and
                not states.intersection(rejected)):
            address = str(ipaddress.ip_address(row["dst"]))
            usable[(row["dev"], address)] = row["lladdr"].lower()
    return usable


def parse_ovs_flows(payload):
    flows = []
    for line in payload.splitlines():
        if " actions=" not in line or "priority=" not in line:
            continue
        match_text, actions_text = line.split(" actions=", 1)
        cookie_match = re.search(r"\bcookie=(0x[0-9a-f]+)", match_text, re.I)
        priority_match = re.search(r"\bpriority=(\d+)", match_text)
        packets_match = re.search(r"\bn_packets=(\d+)", match_text)
        prefix_match = re.search(r"\bnw_dst=([^,\s]+)", match_text)
        family = 4
        if prefix_match is None:
            prefix_match = re.search(r"\bipv6_dst=([^,\s]+)", match_text)
            family = 6
        if prefix_match is None:
            if re.search(r"(?:^|,)ipv6(?:,|$)", match_text):
                family = 6
            elif re.search(r"(?:^|,)ip(?:,|$)", match_text):
                family = 4
            else:
                family = None
        cookie = int(cookie_match.group(1), 16) if cookie_match else 0
        fields = dict(re.findall(
            r"set_field:([^,\s]+)->(eth_src|eth_dst)", actions_text))
        output_match = re.search(r"(?:^|,)output:(\d+)(?:,|$)", actions_text)
        flows.append({
            "raw": line.strip(),
            "cookie": cookie,
            "route_cookie": (cookie >> 32) == 0x4c330000,
            "priority": int(priority_match.group(1)),
            "packets": int(packets_match.group(1)) if packets_match else 0,
            "family": family,
            "prefix": (str(ipaddress.ip_network(prefix_match.group(1), strict=False))
                       if prefix_match else ""),
            "dec_ttl": bool(re.search(
                r"(?:^|,)dec_ttl(?:\(\d+\))?(?:,|$)", actions_text)),
            "eth_src": next(
                (value.lower() for value, field in fields.items()
                 if field == "eth_src"), ""),
            "eth_dst": next(
                (value.lower() for value, field in fields.items()
                 if field == "eth_dst"), ""),
            "output": int(output_match.group(1)) if output_match else None,
            "actions": actions_text.strip(),
        })
    return flows


def flow_priority(prefix):
    return 1000 + ipaddress.ip_network(prefix, strict=False).prefixlen
