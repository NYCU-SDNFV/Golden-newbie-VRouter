#!/usr/bin/env python3
"""Editable os-ken controller for the FRR-backed OVS routed data plane.

The controller consumes real kernel FIB and neighbor JSON from each FRR
namespace.  os-ken is used only as the OpenFlow codec/lifecycle library; the
route selection and forwarding decisions remain visible in routing.py.
"""
import json
import ipaddress
import logging
import os
import subprocess
import time
import zlib
from pathlib import Path

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from os_ken.lib import hub
from os_ken.ofproto import ofproto_v1_3

from harness.routing import (
    Route, RouteTable, build_arp_reply, build_icmpv4_time_exceeded,
    build_icmpv6_time_exceeded, build_nd_advertisement, flow_priority,
    forwarding_actions, parse_fib, parse_neighbors, resolve_next_hop,
)

LOG = logging.getLogger("lab3.controller")
STATE_DIR = Path(os.environ.get("LAB3_STATE_DIR", "/run/lab3"))


def _run(namespace, argv, timeout=5):
    command = ["ip", "netns", "exec", namespace] + list(argv)
    proc = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, check=False)
    if proc.returncode:
        raise RuntimeError("%s failed (%d): %s" %
                           (" ".join(command), proc.returncode, proc.stderr.strip()))
    return proc.stdout


def _atomic_json(path, value):
    pending = path.with_suffix(path.suffix + ".new")
    with pending.open("w", encoding="ascii", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(pending), str(path))


class VirtualRouter(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        raise NotImplementedError("TODO controller: import the FRR FIB, maintain LPM flows, handle gateways, and emit ICMP Time Exceeded")
