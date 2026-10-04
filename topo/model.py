"""Canonical Lab 3 topology model.

The topology is deliberately data, not controller policy.  Both the protected
harness and the editable routing code consume this model.
"""
from typing import NotRequired, TypedDict


class HostConfig(TypedDict):
    name: str
    ipv4: str
    ipv6: str
    mac: str


class ASConfig(TypedDict):
    asn: int
    router_id: str
    lan4: str
    lan6: str
    gw4: str
    gw6: str
    gw_mac: str
    hosts: list[HostConfig]


class LinkConfig(TypedDict):
    ends: tuple[int, int]
    ipv4: tuple[str, str]
    ipv6: tuple[str, str]
    dormant: NotRequired[bool]


class PeerLink(TypedDict):
    name: str
    position: int
    peer_as: int
    ipv4: str
    ipv6: str
    peer4: str
    peer6: str
    dormant: bool


class VXLANEndpoint(TypedDict):
    namespace: str
    local: str
    remote: str
    overlay: str


class VXLANConfig(TypedDict):
    vni: int
    port: int
    mtu: int
    left: VXLANEndpoint
    right: VXLANEndpoint


class Port(TypedDict):
    root: str
    ofport: NotRequired[int]


class HostPort(HostConfig, Port):
    namespace_if: str


class SpeakerPort(Port):
    namespace_if: str
    mac: str
    ipv4: NotRequired[str]
    ipv6: NotRequired[str]
    peer4: NotRequired[str]
    peer6: NotRequired[str]
    peer_as: NotRequired[int]


class ASState(TypedDict):
    asn: int
    namespace: str
    bridge: str
    dpid: int
    gw4: str
    gw6: str
    gw_mac: str
    lan4: str
    lan6: str
    hosts: list[HostPort]
    speaker_ports: dict[str, SpeakerPort]
    external_ports: dict[str, Port]


class LinkState(TypedDict):
    ends: list[int]
    interfaces: list[str]
    dormant: bool


class TopologyResources(TypedDict, total=False):
    namespaces: list[str]
    bridges: list[str]
    root_interfaces: list[str]


class TopologyState(TopologyResources):
    version: int
    plane: str
    ases: dict[str, ASState]
    links: dict[str, LinkState]
    vxlan: VXLANConfig


def port_number(port: Port) -> int:
    """Read an allocated port; incomplete topology journals have no ofport yet."""
    if "ofport" not in port:
        raise KeyError("ofport")
    return port["ofport"]


AS: dict[int, ASConfig] = {
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

LINKS: dict[str, LinkConfig] = {
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

VXLAN: VXLANConfig = {
    "vni": 100, "port": 4789, "mtu": 1450,
    "left": {"namespace": "l3-h1a", "local": "10.1.0.10",
             "remote": "10.3.0.10", "overlay": "192.168.99.1/24"},
    "right": {"namespace": "l3-h3", "local": "10.3.0.10",
              "remote": "10.1.0.10", "overlay": "192.168.99.3/24"},
}


def speaker_mac(asn: int, suffix: str) -> str:
    """Return a stable locally administered MAC for a speaker interface."""
    return "02:10:%02x:00:%02x:%02x" % (asn, int(suffix[:1], 16), int(suffix[-1:], 16))


def bare(address: str) -> str:
    return address.split("/", 1)[0]


def links_for(asn: int) -> list[PeerLink]:
    result: list[PeerLink] = []
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
