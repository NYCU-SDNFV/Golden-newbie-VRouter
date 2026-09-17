"""Editable routing core for the Lab 3 virtual router.

Students complete the marked functions.  The protected lifecycle and checker
call these functions rather than duplicating policy.  OpenFlow serialization
is intentionally left to os-ken; Lab 2 already taught the wire encoding.
"""
import ipaddress
import json
import socket
import struct
from dataclasses import dataclass

from topo.model import AS, links_for


@dataclass(frozen=True)
class Route:
    family: int
    prefix: str
    gateway: str
    device: str
    protocol: str
    metric: int = 0

    @property
    def prefixlen(self):
        return ipaddress.ip_network(self.prefix, strict=False).prefixlen


def bgp_stanza(as_number, include_dormant=False):
    """Build dual-stack FRR BGP policy for one AS.

    AS1 deliberately prefers routes learned through AS2 (local-pref 200), so
    AS1->AS2->AS3 is the baseline and the direct AS1-AS3 edge is a backup.
    """
    raise NotImplementedError("TODO BGP: configure IPv4/IPv6 eBGP, announcements, timers, and AS1 policy")


def build_frr_config(as_number):
    """Return one integrated zebra/bgpd configuration."""
    data = AS[as_number]
    return "\n".join([
        "frr defaults traditional",
        "hostname l3-r%d" % as_number,
        "service integrated-vtysh-config",
        "log stdout informational",
        "ip forwarding",
        "ipv6 forwarding",
        "!",
        bgp_stanza(as_number, include_dormant=(as_number == 4)),
        "!",
        "line vty",
        "",
    ])


def parse_fib(payload, family=None):
    """Parse `ip -j route` output into selected unicast Route objects.

    Multipath entries are expanded.  Unreachable/blackhole routes and routes
    without a usable device are excluded.  Prefixes are canonicalized.
    """
    raise NotImplementedError("TODO FIB: parse real ip -j route output, including replacement and multipath fields")


class RouteTable:
    """Controller route state keyed by address family and canonical prefix."""

    def __init__(self):
        self._routes = {}

    def replace(self, routes):
        """Replace best routes and return (added_or_changed, withdrawn).

        Lower metric wins for duplicate prefixes.  A deterministic gateway and
        device tie-break makes repeated JSON snapshots stable.
        """
        raise NotImplementedError("TODO FIB state: replace changed routes and explicitly withdraw missing prefixes")

    def values(self):
        return list(self._routes.values())

    def lookup(self, address):
        ip = ipaddress.ip_address(address)
        candidates = [
            route for route in self._routes.values()
            if route.family == ip.version and
            ip in ipaddress.ip_network(route.prefix, strict=False)
        ]
        return max(candidates, key=lambda route: route.prefixlen) if candidates else None


def flow_priority(prefix, base=1000):
    """OpenFlow priority preserving IP longest-prefix matching."""
    network = ipaddress.ip_network(prefix, strict=False)
    return base + network.prefixlen


def forwarding_actions(route, source_mac, destination_mac, output_port):
    """Return an explicit, serializer-neutral routed forwarding plan."""
    raise NotImplementedError("TODO forwarding: decrement the L3 lifetime once, rewrite both Ethernet addresses, and output")


def parse_neighbors(payload):
    """Map (device, IP) to reachable link-layer address from `ip -j neigh`."""
    raise NotImplementedError("TODO neighbors: accept only usable actual ARP/ND cache entries")


def resolve_next_hop(route, neighbors, interface_map):
    """Resolve a FIB route to (destination MAC, OpenFlow output port)."""
    raise NotImplementedError("TODO gateway: combine the selected FIB next hop, actual neighbor state, and OVS port")


def internet_checksum(data):
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    total = (total & 0xffff) + (total >> 16)
    total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff


def mac_bytes(value):
    raw = bytes(int(part, 16) for part in value.split(":"))
    if len(raw) != 6:
        raise ValueError("invalid MAC")
    return raw


def build_arp_reply(frame, gateway_ip, gateway_mac):
    """Build a validated Ethernet/IPv4 ARP gateway reply, or return None."""
    if len(frame) < 42:
        return None
    dst, src, ethertype = frame[:6], frame[6:12], struct.unpack("!H", frame[12:14])[0]
    fields = struct.unpack("!HHBBH6s4s6s4s", frame[14:42])
    htype, proto, hlen, plen, operation, sha, spa, tha, tpa = fields
    target = socket.inet_aton(gateway_ip)
    if (ethertype != 0x0806 or htype != 1 or proto != 0x0800 or
            hlen != 6 or plen != 4 or operation != 1 or tpa != target or
            sha != src or spa == b"\x00" * 4 or src[0] & 1):
        return None
    gateway = mac_bytes(gateway_mac)
    arp = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 2,
                      gateway, target, sha, spa)
    return sha + gateway + struct.pack("!H", 0x0806) + arp


def _icmpv6_checksum(source, destination, payload):
    pseudo = (socket.inet_pton(socket.AF_INET6, source) +
              socket.inet_pton(socket.AF_INET6, destination) +
              struct.pack("!I3xB", len(payload), 58))
    return internet_checksum(pseudo + payload)


def build_nd_advertisement(frame, gateway_ip, gateway_mac):
    """Build a standards-checked ICMPv6 neighbor advertisement.

    This scoped implementation handles a directly attached, extension-header
    free NS.  Invalid checksums, hop limits, codes, targets, and multicast
    sources are rejected instead of eliciting spoofed answers.
    """
    if len(frame) < 14 + 40 + 24:
        return None
    dst_mac, src_mac = frame[:6], frame[6:12]
    if struct.unpack("!H", frame[12:14])[0] != 0x86dd:
        return None
    version, payload_len, next_header, hop_limit = struct.unpack("!IHBB", frame[14:22])
    if version >> 28 != 6 or next_header != 58 or hop_limit != 255:
        return None
    source_raw, destination_raw = frame[22:38], frame[38:54]
    source = socket.inet_ntop(socket.AF_INET6, source_raw)
    destination = socket.inet_ntop(socket.AF_INET6, destination_raw)
    payload = frame[54:54 + payload_len]
    if len(payload) < 24 or payload[0] != 135 or payload[1] != 0:
        return None
    if _icmpv6_checksum(source, destination, payload) != 0:
        return None
    target_raw = payload[8:24]
    if target_raw != socket.inet_pton(socket.AF_INET6, gateway_ip):
        return None
    source_ip = ipaddress.ip_address(source)
    if source_ip.is_multicast:
        return None
    gateway = mac_bytes(gateway_mac)
    if source_ip.is_unspecified:
        reply_ip = "ff02::1"
        reply_mac = b"\x33\x33\x00\x00\x00\x01"
        flags = 0x20000000
    else:
        reply_ip = source
        reply_mac = src_mac
        flags = 0x60000000
    body = struct.pack("!BBHI16sBB6s", 136, 0, 0, flags, target_raw, 2, 1, gateway)
    checksum = _icmpv6_checksum(gateway_ip, reply_ip, body)
    body = body[:2] + struct.pack("!H", checksum) + body[4:]
    header = struct.pack(
        "!IHBB16s16s", 6 << 28, len(body), 58, 255,
        socket.inet_pton(socket.AF_INET6, gateway_ip),
        socket.inet_pton(socket.AF_INET6, reply_ip))
    return reply_mac + gateway + struct.pack("!H", 0x86dd) + header + body


def build_icmpv4_time_exceeded(frame, router_ip, router_mac):
    """Return an RFC 792 Time Exceeded frame for a plain IPv4 packet."""
    if len(frame) < 34 or struct.unpack("!H", frame[12:14])[0] != 0x0800:
        return None
    ip = frame[14:]
    ihl = (ip[0] & 0x0f) * 4
    if ip[0] >> 4 != 4 or ihl < 20 or len(ip) < ihl:
        return None
    source = socket.inet_ntoa(ip[12:16])
    quote = ip[:min(len(ip), ihl + 8)]
    icmp = struct.pack("!BBHI", 11, 0, 0, 0) + quote
    icmp = icmp[:2] + struct.pack("!H", internet_checksum(icmp)) + icmp[4:]
    total = 20 + len(icmp)
    outer = struct.pack(
        "!BBHHHBBH4s4s", 0x45, 0, total, 0, 0, 64, 1, 0,
        socket.inet_aton(router_ip), socket.inet_aton(source))
    outer = outer[:10] + struct.pack("!H", internet_checksum(outer)) + outer[12:]
    gateway = mac_bytes(router_mac)
    return frame[6:12] + gateway + struct.pack("!H", 0x0800) + outer + icmp


def build_icmpv6_time_exceeded(frame, router_ip, router_mac):
    """Return an RFC 4443 Time Exceeded frame for a plain IPv6 packet."""
    if len(frame) < 54 or struct.unpack("!H", frame[12:14])[0] != 0x86dd:
        return None
    invoking = frame[14:]
    if invoking[0] >> 4 != 6:
        return None
    source = socket.inet_ntop(socket.AF_INET6, invoking[8:24])
    quote = invoking[:min(len(invoking), 1232)]
    icmp = struct.pack("!BBHI", 3, 0, 0, 0) + quote
    checksum = _icmpv6_checksum(router_ip, source, icmp)
    icmp = icmp[:2] + struct.pack("!H", checksum) + icmp[4:]
    header = struct.pack(
        "!IHBB16s16s", 6 << 28, len(icmp), 58, 64,
        socket.inet_pton(socket.AF_INET6, router_ip),
        socket.inet_pton(socket.AF_INET6, source))
    gateway = mac_bytes(router_mac)
    return frame[6:12] + gateway + struct.pack("!H", 0x86dd) + header + icmp
