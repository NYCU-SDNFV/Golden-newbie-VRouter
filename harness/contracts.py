"""Shared data contracts, not routing policy.

Kernel/FRR JSON annotations describe the consumed fields of their documented
records; extra JSON fields remain untouched. Deserialization is not validation.
Measurement values that are only serialized are deliberately opaque; fields
used for decisions or arithmetic have their own concrete records below.
"""
from typing import Literal, NotRequired, TypeAlias, TypedDict


JSONValue: TypeAlias = (
    None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
)
Measurements: TypeAlias = dict[str, object]
CheckResult: TypeAlias = tuple[bool, Measurements]
RouteKey: TypeAlias = tuple[int, str]
Neighbors: TypeAlias = dict[tuple[str, str], str]


class KernelNextHop(TypedDict, total=False):
    dev: str
    gateway: str
    metric: int | str | None


class KernelRoute(KernelNextHop, total=False):
    type: str
    dst: str
    family: int | str
    protocol: int | str
    prefsrc: str
    nexthops: list[KernelNextHop] | None


class KernelNeighbor(TypedDict, total=False):
    dst: str
    dev: str
    lladdr: str
    state: str | list[str]


class KernelLink(TypedDict):
    operstate: str


class RouteRecord(TypedDict):
    family: int
    prefix: str
    gateway: str
    device: str
    protocol: str


class SelectedRoute(RouteRecord):
    metric: int


class InterfaceMapping(TypedDict):
    ofport: int
    destination_mac: NotRequired[str]


InterfaceMap: TypeAlias = dict[str, InterfaceMapping]


class ForwardingPlan(TypedDict):
    decrement_ttl: bool
    set_eth_src: str
    set_eth_dst: str
    output: int


class CommandOutput(TypedDict):
    returncode: int
    stdout: str
    stderr: str


class ObservedNeighbor(TypedDict):
    device: str
    address: str
    mac: str


class NeighborDiagnostic(TypedDict, total=False):
    status: str
    mac: str
    observed: float
    namespace: str
    device: str
    gateway: str
    family: int
    attempts: int
    command: list[str]
    last_result: CommandOutput
    next_retry: float
    observed_neighbors: list[ObservedNeighbor]


class ControllerState(TypedDict, total=False):
    ready: bool
    datapaths: list[int]
    last_error: str
    neighbor_resolution: dict[str, NeighborDiagnostic]
    routes: dict[str, list[RouteRecord]]
    updated: float
    age_seconds: float


class ProcessStatus(TypedDict):
    running: bool
    owned: bool
    command: str


class ProcessHealth(TypedDict):
    role: str
    pid: int
    running: bool
    owned: bool


class FRRProcess(TypedDict):
    asn: int
    daemon: str
    pid: int
    pathspace: str
    namespace: str


class ControllerProcess(TypedDict):
    pid: int
    command: list[str]
    log: str


class RuntimeState(TypedDict):
    version: int
    plane: str
    started: float
    scenario: str
    frr: list[FRRProcess]
    controller: ControllerProcess | None
    ready: NotRequired[float]


class CleanupState(TypedDict, total=False):
    frr: list[FRRProcess]
    controller: ControllerProcess | None


class UndeployedStatus(TypedDict):
    deployed: Literal[False]
    state_dir: str


class DeployedStatus(TypedDict):
    deployed: Literal[True]
    state_dir: str
    plane: str
    scenario: str
    started: float
    processes: list[ProcessHealth]
    healthy: bool | None
    controller: NotRequired[ControllerState]
    controller_connections: NotRequired[dict[str, bool]]


RuntimeStatus: TypeAlias = UndeployedStatus | DeployedStatus


class CleanResult(TypedDict):
    cleaned: Literal[True]


class BGPSummary(TypedDict):
    established: int
    raw: JSONValue


BGPObservations: TypeAlias = dict[tuple[int, int | str], int | str | list[str] | None]


class PipelineTrace(TypedDict):
    returncode: int
    invalid_ttl: bool
    controller: bool
    trace: str


class PingResult(TypedDict):
    returncode: int
    loss_percent: float
    output: str


class RouteLookup(TypedDict):
    destination: str
    gateway: str
    device: str
    source: str


class Absent(TypedDict):
    absent: Literal[True]


class Flow(TypedDict):
    raw: str
    cookie: int
    route_cookie: bool
    priority: int
    packets: int
    family: int | None
    prefix: str
    dec_ttl: bool
    eth_src: str
    eth_dst: str
    output: int | None
    actions: str


class PrefixRecord(TypedDict):
    family: int
    prefix: str


class UnresolvedRoute(PrefixRecord):
    device: str
    gateway: str


class ExpectedFlow(UnresolvedRoute):
    priority: int
    eth_src: str
    eth_dst: str
    output: int
    dec_ttl: bool


class PrefixObservation(TypedDict):
    installed: list[str]
    expected: list[str]
    missing: list[str]


class DuplicateFlow(TypedDict):
    family: int | None
    prefix: str
    count: int


class StaleFlow(TypedDict):
    family: int | None
    prefix: str
    raw: str


class LessSpecificFlow(TypedDict):
    expected: str
    actual: str
    actual_priority: int


class FieldDifference(TypedDict):
    expected: int | str | bool
    actual: int | str | bool | None


class WrongFlow(PrefixRecord):
    differences: dict[str, FieldDifference]
    raw: str


class FlowComparison(TypedDict):
    expected: list[ExpectedFlow]
    actual: list[Flow]
    unresolved_neighbors: list[UnresolvedRoute]
    missing: list[PrefixRecord]
    stale: list[StaleFlow]
    wrong: list[WrongFlow]
    less_specific: list[LessSpecificFlow]
    duplicate: list[DuplicateFlow]
    unowned_route_like: list[Flow]
    malformed_route_cookie: list[Flow]
    passed: bool


class CaptureResult(TypedDict):
    interface: str
    capture: str
    customer_packets: int
    ipv4_packets: int
    ipv6_packets: int
    decoded: str


class ForwardingConvergence(TypedDict):
    flows: dict[str, FlowComparison]
    first_reachable: dict[str, PingResult]


class LifetimeProbe(TypedDict):
    ttl_or_hop_limit: int
    returncode: int
    time_exceeded_visible: bool
    output: str
