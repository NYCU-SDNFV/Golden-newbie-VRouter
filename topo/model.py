"""Canonical Lab 3 topology model.

The topology is deliberately data, not controller policy.  Both the protected
harness and the editable routing code consume this model.
"""

AS = {
    1: {
        "asn": 65001, "router_id": "10.255.0.1",
        "lan4": "10.1.0.0/24", "lan6": "2001:db8:1::/64",
        "gw4": "10.1.0.1", "gw6": "2001:db8:1::1",
        "gw_mac": "02:aa:00:00:01:01",
        "hosts": [
            {"name": "l3-h1a", "ipv4": "10.1.0.10/24",
             "ipv6": "2001:db8:1::10/64", "mac": "02:00:00:01:00:0a"},
            {"name": "l3-h1b", "ipv4": "10.1.0.11/24",
             "ipv6": "2001:db8:1::11/64", "mac": "02:00:00:01:00:0b"},
        ],
    },
    2: {
        "asn": 65002, "router_id": "10.255.0.2",
        "lan4": "10.2.0.0/24", "lan6": "2001:db8:2::/64",
        "gw4": "10.2.0.1", "gw6": "2001:db8:2::1",
        "gw_mac": "02:aa:00:00:02:01",
        "hosts": [
            {"name": "l3-h2", "ipv4": "10.2.0.10/24",
             "ipv6": "2001:db8:2::10/64", "mac": "02:00:00:02:00:0a"},
        ],
    },
    3: {
        "asn": 65003, "router_id": "10.255.0.3",
        "lan4": "10.3.0.0/24", "lan6": "2001:db8:3::/64",
        "gw4": "10.3.0.1", "gw6": "2001:db8:3::1",
        "gw_mac": "02:aa:00:00:03:01",
        "hosts": [
            {"name": "l3-h3", "ipv4": "10.3.0.10/24",
             "ipv6": "2001:db8:3::10/64", "mac": "02:00:00:03:00:0a"},
        ],
    },
    4: {
        "asn": 65004, "router_id": "10.255.0.4",
        "lan4": "10.4.0.0/24", "lan6": "2001:db8:4::/64",
        "gw4": "10.4.0.1", "gw6": "2001:db8:4::1",
        "gw_mac": "02:aa:00:00:04:01",
        "hosts": [
            {"name": "l3-h4", "ipv4": "10.4.0.10/24",
             "ipv6": "2001:db8:4::10/64", "mac": "02:00:00:04:00:0a"},
        ],
    },
}

LINKS = {
    "12": {
        "ends": (1, 2), "ipv4": ("10.12.0.1/30", "10.12.0.2/30"),
        "ipv6": ("2001:db8:12::1/64", "2001:db8:12::2/64"),
    },
    "23": {
        "ends": (2, 3), "ipv4": ("10.23.0.1/30", "10.23.0.2/30"),
        "ipv6": ("2001:db8:23::1/64", "2001:db8:23::2/64"),
    },
    "13": {
        "ends": (1, 3), "ipv4": ("10.13.0.1/30", "10.13.0.2/30"),
        "ipv6": ("2001:db8:13::1/64", "2001:db8:13::2/64"),
    },
    "24": {
        "ends": (2, 4), "ipv4": ("10.24.0.1/30", "10.24.0.2/30"),
        "ipv6": ("2001:db8:24::1/64", "2001:db8:24::2/64"),
        "dormant": True,
    },
}

VXLAN = {
    "vni": 100, "port": 4789, "mtu": 1450,
    "left": {"namespace": "l3-h1a", "local": "10.1.0.10",
             "remote": "10.3.0.10", "overlay": "192.168.99.1/24"},
    "right": {"namespace": "l3-h3", "local": "10.3.0.10",
              "remote": "10.1.0.10", "overlay": "192.168.99.3/24"},
}


def speaker_mac(asn, suffix):
    """Return a stable locally administered MAC for a speaker interface."""
    return "02:10:%02x:00:%02x:%02x" % (asn, int(suffix[:1], 16), int(suffix[-1:], 16))


def bare(address):
    return address.split("/", 1)[0]


def links_for(asn):
    result = []
    for name, link in LINKS.items():
        if asn in link["ends"]:
            pos = link["ends"].index(asn)
            peer_pos = 1 - pos
            result.append({
                "name": name,
                "position": pos,
                "peer_as": link["ends"][peer_pos],
                "ipv4": link["ipv4"][pos],
                "ipv6": link["ipv6"][pos],
                "peer4": bare(link["ipv4"][peer_pos]),
                "peer6": bare(link["ipv6"][peer_pos]),
                "dormant": bool(link.get("dormant")),
            })
    return result
