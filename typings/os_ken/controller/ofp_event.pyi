from os_ken.ofproto.ofproto_v1_3_parser import OFPPacketIn, OFPSwitchFeatures

class EventOFPSwitchFeatures:
    msg: OFPSwitchFeatures
    timestamp: float
    def __init__(self, msg: OFPSwitchFeatures) -> None: ...

class EventOFPPacketIn:
    msg: OFPPacketIn
    timestamp: float
    def __init__(self, msg: OFPPacketIn) -> None: ...
