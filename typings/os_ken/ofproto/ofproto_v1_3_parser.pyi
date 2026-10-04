from collections.abc import Sequence
from typing import Literal, TypeAlias, TypedDict, TypeVar, Unpack, overload

from harness.openflow import Datapath13
from os_ken.ofproto.ofproto_parser import MsgBase as MsgBase

_T = TypeVar("_T")
_MatchValue: TypeAlias = int | str | tuple[int, int] | tuple[str, str]

class _MatchFields(TypedDict, total=False):
    in_port: int
    eth_src: str | tuple[str, str]
    eth_dst: str | tuple[str, str]
    eth_type: int
    ipv4_dst: str | tuple[str, str]
    ipv6_dst: str | tuple[str, str]
    ip_proto: int
    tcp_src: int
    tcp_dst: int
    icmpv6_type: int

class OFPMatch:
    def __init__(
        self, type_: int | None = None, length: int | None = None,
        _ordered_fields: None = None,
        **kwargs: Unpack[_MatchFields],
    ) -> None: ...
    @overload
    def get(self, key: Literal["in_port"], default: _T | None = None) -> int | _T | None: ...
    @overload
    def get(self, key: str, default: _T | None = None) -> _MatchValue | _T | None: ...

class OFPSwitchFeatures(MsgBase):
    datapath_id: int | None
    n_buffers: int | None
    n_tables: int | None
    auxiliary_id: int | None
    capabilities: int | None
    def __init__(
        self, datapath: Datapath13, datapath_id: int | None = None,
        n_buffers: int | None = None, n_tables: int | None = None,
        auxiliary_id: int | None = None, capabilities: int | None = None,
    ) -> None: ...

class OFPPacketIn(MsgBase):
    buffer_id: int | None
    total_len: int | None
    reason: int | None
    table_id: int | None
    cookie: int | None
    match: OFPMatch | None
    data: bytes | None
    def __init__(
        self, datapath: Datapath13, buffer_id: int | None = None,
        total_len: int | None = None, reason: int | None = None,
        table_id: int | None = None, cookie: int | None = None,
        match: OFPMatch | None = None, data: bytes | None = None,
    ) -> None: ...

class OFPAction:
    def __init__(self) -> None: ...

class OFPInstruction: ...

class OFPInstructionActions(OFPInstruction):
    type: int
    actions: Sequence[OFPAction] | None
    def __init__(
        self, type_: int, actions: Sequence[OFPAction] | None = None,
        len_: int | None = None,
    ) -> None: ...

class OFPActionOutput(OFPAction):
    port: int
    max_len: int
    def __init__(
        self, port: int, max_len: int = ..., type_: int | None = None,
        len_: int | None = None,
    ) -> None: ...

class OFPActionDecNwTtl(OFPAction):
    def __init__(self, type_: int | None = None, len_: int | None = None) -> None: ...

class OFPActionSetField(OFPAction):
    key: str
    value: str
    @overload
    def __init__(self, field: None = None, *, eth_src: str) -> None: ...
    @overload
    def __init__(self, field: None = None, *, eth_dst: str) -> None: ...

class OFPFlowMod(MsgBase):
    cookie: int
    cookie_mask: int
    table_id: int
    command: int
    priority: int
    match: OFPMatch
    instructions: Sequence[OFPInstruction]
    def __init__(
        self, datapath: Datapath13, cookie: int = 0, cookie_mask: int = 0,
        table_id: int = 0, command: int = 0, idle_timeout: int = 0,
        hard_timeout: int = 0, priority: int = 32768, buffer_id: int = 4294967295,
        out_port: int = 0, out_group: int = 0, flags: int = 0,
        match: OFPMatch | None = None,
        instructions: Sequence[OFPInstruction] | None = None,
    ) -> None: ...

class OFPPacketOut(MsgBase):
    buffer_id: int | None
    in_port: int
    actions: Sequence[OFPAction] | None
    data: bytes | None
    def __init__(
        self, datapath: Datapath13, buffer_id: int | None = None,
        in_port: int | None = None, actions: Sequence[OFPAction] | None = None,
        data: bytes | None = None, actions_len: int | None = None,
    ) -> None: ...

class OFPSetConfig(MsgBase):
    flags: int
    miss_send_len: int
    def __init__(self, datapath: Datapath13, flags: int = 0, miss_send_len: int = 0) -> None: ...

class OFPSetAsync(MsgBase):
    packet_in_mask: Sequence[int]
    port_status_mask: Sequence[int]
    flow_removed_mask: Sequence[int]
    def __init__(
        self, datapath: Datapath13, packet_in_mask: Sequence[int],
        port_status_mask: Sequence[int], flow_removed_mask: Sequence[int],
    ) -> None: ...
