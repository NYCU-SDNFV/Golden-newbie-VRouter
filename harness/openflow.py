"""The negotiated OpenFlow 1.3 boundary used by this lab, not a global SDK stub.

os-ken Datapath can negotiate other versions. Only a controller advertising
OpenFlow 1.3 may use this structural view of its modules and messages.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from os_ken.ofproto.ofproto_parser import MsgBase
    from os_ken.ofproto.ofproto_v1_3_parser import (
        OFPActionDecNwTtl, OFPActionOutput, OFPActionSetField, OFPFlowMod,
        OFPInstructionActions, OFPMatch, OFPPacketOut, OFPSetAsync, OFPSetConfig,
    )


class Constants13(Protocol):
    OFP_VERSION: int
    OFP_HEADER_SIZE: int
    OFP_HEADER_PACK_STR: str
    OFPIT_APPLY_ACTIONS: int
    OFPFC_DELETE_STRICT: int
    OFPFC_DELETE: int
    OFPP_ANY: int
    OFPG_ANY: int
    OFPP_CONTROLLER: int
    OFPCML_NO_BUFFER: int
    OFPR_NO_MATCH: int
    OFPR_ACTION: int
    OFPR_INVALID_TTL: int
    OFPPR_ADD: int
    OFPPR_DELETE: int
    OFPPR_MODIFY: int
    OFP_NO_BUFFER: int


class Parser13(Protocol):
    MsgBase: type[MsgBase]
    OFPActionDecNwTtl: type[OFPActionDecNwTtl]
    OFPActionOutput: type[OFPActionOutput]
    OFPActionSetField: type[OFPActionSetField]
    OFPFlowMod: type[OFPFlowMod]
    OFPInstructionActions: type[OFPInstructionActions]
    OFPMatch: type[OFPMatch]
    OFPPacketOut: type[OFPPacketOut]
    OFPSetAsync: type[OFPSetAsync]
    OFPSetConfig: type[OFPSetConfig]


class Datapath13(Protocol):
    id: int | None
    ofproto: Constants13
    ofproto_parser: Parser13

    def send_msg(self, msg: MsgBase, close_socket: bool = False) -> bool: ...

    def close(self) -> None: ...
