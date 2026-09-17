"""Protected lifecycle for the persistent Lab 3 runtime."""
import json
import os
import re
import re
import shutil
import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from harness import oracle, routing
from topo import lab3_topo

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path("/run/lab3")
RUNTIME_FILE = STATE_DIR / "runtime.json"
TOPOLOGY_FILE = STATE_DIR / "topology.json"
RESULTS = ROOT / "results"
CAPTURES = ROOT / "captures"
LOCK_FILE = Path("/run/lab3.lock")


class RuntimeFailure(RuntimeError):
    pass


def atomic_json(path, value):
    lab3_topo.atomic_json(path, value)


def load_json(path):
    with Path(path).open(encoding="ascii") as stream:
        return json.load(stream)


def prepare_evidence_directories():
    owner = ROOT.stat()
    for directory in (RESULTS, CAPTURES):
        if directory.is_symlink():
            raise RuntimeFailure("evidence directory must not be a symlink: %s" % directory)
        try:
            directory.mkdir()
        except FileExistsError:
            if not directory.is_dir():
                raise RuntimeFailure("evidence path is not a directory: %s" % directory)
        else:
            os.chown(directory, owner.st_uid, owner.st_gid)


@contextmanager
def lifecycle_lock(timeout=10):
    import fcntl

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    stream = LOCK_FILE.open("a+", encoding="ascii")
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                stream.close()
                raise RuntimeFailure("timed out waiting for the Lab 3 lifecycle lock")
            time.sleep(0.05)
    try:
        yield
    finally:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def _verify_osken_runtime():
    try:
        from os_ken.ofproto import ofproto_v1_3, ofproto_v1_3_parser
    except ImportError as exc:
        raise RuntimeFailure("os-ken OpenFlow 1.3 modules are unavailable") from exc
    if not hasattr(ofproto_v1_3, "OFPR_INVALID_TTL"):
        raise RuntimeFailure("installed os-ken lacks OFPR_INVALID_TTL")
    if not hasattr(ofproto_v1_3_parser, "OFPActionDecNwTtl"):
        raise RuntimeFailure("installed os-ken lacks OFPActionDecNwTtl")


def _validate_implementation(plane):
    for as_number in range(1, 5):
        try:
            config = routing.build_frr_config(as_number)
        except NotImplementedError as exc:
            raise RuntimeFailure("BGP configuration TODO is incomplete: %s" % exc) from exc
        required = ("router bgp ", "address-family ipv4 unicast",
                    "address-family ipv6 unicast")
        if not isinstance(config, str) or not all(token in config for token in required):
            raise RuntimeFailure(
                "BGP configuration for AS%d lacks required dual-stack sections" % as_number)
    if plane == "frr":
        return
    probes = (
        ("FIB JSON parser", lambda: routing.parse_fib("[]"),
         lambda value: isinstance(value, list)),
        ("route replacement", lambda: routing.RouteTable().replace([]),
         lambda value: isinstance(value, tuple) and len(value) == 2),
        ("neighbor parser", lambda: routing.parse_neighbors("[]"),
         lambda value: isinstance(value, dict)),
        ("forwarding actions", lambda: routing.forwarding_actions(
            routing.Route(4, "192.0.2.0/24", "", "test0", "test"),
            "02:00:00:00:00:01", "02:00:00:00:00:02", 1),
         lambda value: isinstance(value, dict) and value.get("decrement_ttl")),
        ("next-hop resolution", lambda: routing.resolve_next_hop(
            routing.Route(4, "192.0.2.0/24", "", "test0", "test"), {},
            {"test0": {"ofport": 1, "destination_mac": "02:00:00:00:00:02"}}),
         lambda value: value == ("02:00:00:00:00:02", 1)),
    )
    for name, probe, validator in probes:
        try:
            value = probe()
        except NotImplementedError as exc:
            raise RuntimeFailure("%s TODO is incomplete: %s" % (name, exc)) from exc
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RuntimeFailure("%s preflight failed: %s" % (name, exc)) from exc
        if not validator(value):
            raise RuntimeFailure("%s returned an invalid preflight result" % name)
    try:
        from harness.controller import VirtualRouter
    except (ImportError, AttributeError) as exc:
        raise RuntimeFailure("OVS controller cannot be imported: %s" % exc) from exc
    methods = ("_base_pipeline", "_sync_as", "packet_in")
    missing = [name for name in methods if not callable(getattr(VirtualRouter, name, None))]
    if missing:
        raise RuntimeFailure("OVS controller TODOs are incomplete: missing %s" %
                             ", ".join(missing))


def _requirements(plane):
    required = ["ip", "ovs-vsctl", "ovs-ofctl", "vtysh", "tcpdump", "ping",
                "ethtool"]
    if plane == "ovs":
        required.extend(["osken-manager", "ovs-appctl"])
    missing = [name for name in required if not shutil.which(name)]
    for daemon in ("zebra", "bgpd"):
        if not Path("/usr/lib/frr", daemon).is_file():
            missing.append("/usr/lib/frr/" + daemon)
    if missing:
        raise RuntimeFailure("missing required course-image tools: %s" %
                             ", ".join(missing))
    if os.geteuid() != 0:
        raise RuntimeFailure("deploy and clean require root in the course container")
    if plane == "ovs":
        _verify_osken_runtime()


def _pid_from(path):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            return int(Path(path).read_text(encoding="ascii").strip())
        except (FileNotFoundError, ValueError):
            time.sleep(0.05)
    raise RuntimeFailure("daemon did not create PID file %s" % path)


def process_command(pid):
    try:
        return Path("/proc/%d/cmdline" % pid).read_bytes().replace(b"\x00", b" ").decode(
            "utf-8", "replace")
    except FileNotFoundError:
        return ""


def owned_process_status(pid, tokens):
    command = process_command(pid)
    if not command:
        return {"running": False, "owned": False, "command": ""}
    try:
        state = Path("/proc/%d/stat" % pid).read_text(
            encoding="ascii").split(")", 1)[1].strip().split()[0]
    except (FileNotFoundError, IndexError):
        return {"running": False, "owned": False, "command": command}
    owned = all(token in command for token in tokens)
    return {"running": state not in ("Z", "X"), "owned": owned, "command": command}


def controller_state_fresh(value, now=None):
    now = time.time() if now is None else now
    age = now - float(value.get("updated", 0))
    return (
        age >= 0 and age <= 3 and value.get("ready") and
        not value.get("last_error") and
        set(value.get("datapaths", [])) == {1, 2, 3, 4})


def controller_connections(topology):
    result = {}
    for entry in topology["ases"].values():
        bridge = entry["bridge"]
        record = lab3_topo.run(
            ["ovs-vsctl", "get", "Bridge", bridge, "controller"], check=False)
        identifiers = re.findall(
            r"[0-9a-f]{8}-[0-9a-f-]{27,}", record.stdout or "", re.I)
        connected = bool(identifiers)
        for identifier in identifiers:
            value = lab3_topo.run(
                ["ovs-vsctl", "get", "Controller", identifier, "is_connected"],
                check=False)
            connected = connected and value.returncode == 0 and (
                value.stdout.strip().lower() == "true")
        result[bridge] = connected
    return result


def _require_owned(pid, tokens):
    process = owned_process_status(pid, tokens)
    if not process["running"]:
        return False
    if not process["owned"]:
        raise RuntimeFailure("PID %d is not an owned Lab 3 process: %s" %
                             (pid, process["command"]))
    return True


def stop_owned(pid, tokens, timeout=4):
    if not _require_owned(pid, tokens):
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_command(pid):
            return
        time.sleep(0.05)
    if _require_owned(pid, tokens):
        os.kill(pid, signal.SIGKILL)


def _prepare_frr_dir(pathspace):
    import grp
    import pwd

    directory = Path("/run/frr") / pathspace
    directory.mkdir(parents=True, exist_ok=True)
    try:
        uid, gid = pwd.getpwnam("frr").pw_uid, grp.getgrnam("frr").gr_gid
    except KeyError as exc:
        raise RuntimeFailure("course image has no frr user/group") from exc
    os.chown(str(directory), uid, gid)
    os.chmod(str(directory), 0o775)
    return uid, gid


def _start_frr(as_number, state):
    namespace = "l3-r%d" % as_number
    pathspace = "lab3-r%d" % as_number
    directory = STATE_DIR / "frr" / ("r%d" % as_number)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(str(directory), 0o755)
    uid, gid = _prepare_frr_dir(pathspace)
    log_path = RESULTS / ("frr-r%d.log" % as_number)
    log_path.touch(exist_ok=True)
    os.chown(str(log_path), uid, gid)
    os.chmod(str(log_path), 0o664)
    config = routing.build_frr_config(as_number)
    config = config.replace(
        "log stdout informational",
        "log file %s informational" % log_path)
    config_path = directory / "frr.conf"
    config_path.write_text(config, encoding="ascii", newline="\n")
    os.chmod(str(config_path), 0o644)
    pids = {}
    for daemon in ("zebra", "bgpd"):
        pidfile = Path("/run/frr") / pathspace / (daemon + ".pid")
        command = [
            "ip", "netns", "exec", namespace, "/usr/lib/frr/" + daemon,
            "-d", "-N", pathspace, "-f", str(config_path), "-i", str(pidfile),
        ]
        proc = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=10, check=False)
        if proc.returncode:
            raise RuntimeFailure("%s failed for AS%d: %s" %
                                 (daemon, as_number, proc.stderr.strip()))
        pid = _pid_from(pidfile)
        state["frr"].append({
            "asn": as_number, "daemon": daemon, "pid": pid,
            "pathspace": pathspace, "namespace": namespace,
        })
        atomic_json(RUNTIME_FILE, state)
        if not _require_owned(pid, [daemon, pathspace]):
            raise RuntimeFailure("%s for AS%d exited during startup" % (daemon, as_number))
        pids[daemon] = pid
    return pids


def vtysh(as_number, commands, check=True):
    if isinstance(commands, str):
        commands = [commands]
    argv = ["ip", "netns", "exec", "l3-r%d" % as_number,
            "vtysh", "-N", "lab3-r%d" % as_number]
    for command in commands:
        argv.extend(["-c", command])
    proc = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=10, check=False)
    if check and proc.returncode:
        raise RuntimeFailure("vtysh AS%d failed: %s" %
                             (as_number, proc.stderr.strip()))
    return proc


def _established_count(value):
    count = 0
    if isinstance(value, dict):
        state = value.get("state") or value.get("peerState")
        if state == "Established":
            count += 1
        for nested in value.values():
            count += _established_count(nested)
    elif isinstance(value, list):
        for nested in value:
            count += _established_count(nested)
    return count


def bgp_summary(as_number, family):
    afi = "ipv4" if family == 4 else "ipv6"
    proc = vtysh(as_number, "show bgp %s unicast summary json" % afi)
    try:
        value = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeFailure("FRR AS%d returned invalid %s summary JSON: %s" %
                             (as_number, afi, proc.stdout[:200])) from exc
    return {"established": _established_count(value), "raw": value}


def wait_for(description, predicate, timeout=30, interval=0.25):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (RuntimeFailure, lab3_topo.CommandError, OSError,
                json.JSONDecodeError) as exc:
            last = str(exc)
        time.sleep(interval)
    raise RuntimeFailure("timed out waiting for %s (last=%r)" % (description, last))


def _wait_bgp():
    expected = {1: 2, 2: 2, 3: 2}
    expected_prefixes = {
        1: {"10.2.0.0/24", "10.3.0.0/24",
            "2001:db8:2::/64", "2001:db8:3::/64"},
        2: {"10.1.0.0/24", "10.3.0.0/24",
            "2001:db8:1::/64", "2001:db8:3::/64"},
        3: {"10.1.0.0/24", "10.2.0.0/24",
            "2001:db8:1::/64", "2001:db8:2::/64"},
    }

    def ready():
        observations = {}
        for asn, peers in expected.items():
            for family in (4, 6):
                count = bgp_summary(asn, family)["established"]
                observations[(asn, family)] = count
                if count < peers:
                    return False
            installed = set()
            for family, flag in ((4, "-4"), (6, "-6")):
                output = lab3_topo.ns_exec(
                    "l3-r%d" % asn, "ip", "-j", flag, "route", "show",
                    "proto", "bgp").stdout
                installed.update(route["prefix"] for route in
                                 oracle.parse_kernel_routes(output, family))
            observations[(asn, "prefixes")] = sorted(installed)
            if not expected_prefixes[asn].issubset(installed):
                return False
            if asn == 1:
                for flag, destination in (
                        ("-4", "10.3.0.10"), ("-6", "2001:db8:3::10")):
                    output = lab3_topo.ns_exec(
                        "l3-r1", "ip", "-j", flag, "route", "get",
                        destination).stdout
                    route = json.loads(output)[0]
                    observations[(asn, destination)] = route.get("dev")
                    if route.get("dev") != "r1-12":
                        return False
        return observations

    return wait_for("three-AS dual-stack BGP routes", ready, timeout=35, interval=0.5)


def _start_controller(state, topology):
    log_path = RESULTS / "controller.log"
    log_stream = log_path.open("ab", buffering=0)
    env = dict(os.environ)
    env["LAB3_STATE_DIR"] = str(STATE_DIR)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    command = [
        shutil.which("osken-manager"), "--ofp-tcp-listen-port", "6653",
        str(ROOT / "harness" / "controller.py"),
    ]
    process = subprocess.Popen(
        command, cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
        stdout=log_stream, stderr=subprocess.STDOUT, start_new_session=True)
    log_stream.close()
    state["controller"] = {"pid": process.pid, "command": command,
                           "log": str(log_path)}
    atomic_json(RUNTIME_FILE, state)
    time.sleep(0.1)
    if process.poll() is not None:
        raise RuntimeFailure("controller exited at startup; inspect %s" % log_path)
    for asn, entry in topology["ases"].items():
        lab3_topo.run([
            "ovs-vsctl", "set-controller", entry["bridge"],
            "tcp:127.0.0.1:6653",
        ])

    def ready():
        if process.poll() is not None:
            raise RuntimeFailure("controller exited; inspect %s" % log_path)
        try:
            status = load_json(STATE_DIR / "controller.json")
        except FileNotFoundError:
            return False
        if status.get("last_error"):
            raise RuntimeFailure(status["last_error"])
        routes = status.get("routes", {}).get("1", [])
        prefixes = {route["prefix"] for route in routes}
        return status if status.get("ready") and {
            "10.3.0.0/24", "2001:db8:3::/64"
        }.issubset(prefixes) else False

    return wait_for("OVS controller and initial FIB import", ready, timeout=25)


def _verify_invalid_ttl_pipeline(topology):
    entry = topology["ases"]["1"]
    host = entry["hosts"][0]
    fields = {
        "ipv4": (
            "in_port=%d,dl_src=%s,dl_dst=%s,dl_type=0x0800,"
            "nw_src=10.1.0.10,nw_dst=10.3.0.10,nw_proto=1,nw_ttl=1,icmp_type=8,icmp_code=0" %
            (host["ofport"], host["mac"], entry["gw_mac"])),
        "ipv6": (
            "in_port=%d,dl_src=%s,dl_dst=%s,dl_type=0x86dd,"
            "ipv6_src=2001:db8:1::10,ipv6_dst=2001:db8:3::10,"
            "nw_proto=58,nw_ttl=1,icmpv6_type=128,icmpv6_code=0" %
            (host["ofport"], host["mac"], entry["gw_mac"])),
    }

    def trace_ready():
        traces = {}
        for family, flow in fields.items():
            result = lab3_topo.run(
                ["ovs-appctl", "ofproto/trace", entry["bridge"], flow],
                timeout=5, check=False)
            text = (result.stdout or "") + (result.stderr or "")
            traces[family] = {
                "returncode": result.returncode,
                "invalid_ttl": bool(re.search(
                    r"controller\(reason=(?:2|invalid_ttl)(?:,|\))", text.lower())),
                "controller": "controller" in text.lower(),
                "trace": text[-4000:],
            }
        lab3_topo.atomic_json(RESULTS / "ttl-pipeline-trace.json", traces)
        return traces if all(
            value["returncode"] == 0 and value["invalid_ttl"] and
            value["controller"] for value in traces.values()) else False

    traces = wait_for(
        "OVS IPv4/IPv6 invalid-TTL PacketIn pipeline",
        trace_ready, timeout=8, interval=0.2)
    return traces


def status():
    if not RUNTIME_FILE.is_file():
        return {"deployed": False, "state_dir": str(STATE_DIR)}
    state = load_json(RUNTIME_FILE)
    processes = []
    for item in state.get("frr", []):
        process = owned_process_status(
            item["pid"], [item["daemon"], item["pathspace"]])
        processes.append({
            "role": "%s-as%d" % (item["daemon"], item["asn"]),
            "pid": item["pid"],
            "running": process["running"], "owned": process["owned"],
        })
    controller = state.get("controller")
    if controller:
        process = owned_process_status(
            controller["pid"], ["osken-manager", "controller.py", str(ROOT)])
        processes.append({
            "role": "controller", "pid": controller["pid"],
            "running": process["running"], "owned": process["owned"],
        })
    value = {
        "deployed": True, "plane": state["plane"], "scenario": state["scenario"],
        "started": state["started"], "processes": processes,
        "healthy": (
            bool(state.get("ready")) and TOPOLOGY_FILE.is_file() and
            len(state.get("frr", [])) == 8 and
            (state["plane"] == "frr" or controller is not None) and
            all(item["running"] and item["owned"] for item in processes)
        ),
        "state_dir": str(STATE_DIR),
    }
    if (STATE_DIR / "controller.json").is_file():
        value["controller"] = load_json(STATE_DIR / "controller.json")
        age = time.time() - float(value["controller"].get("updated", 0))
        value["controller"]["age_seconds"] = age
        controller_fresh = controller_state_fresh(value["controller"])
        value["healthy"] = value["healthy"] and controller_fresh
    elif state["plane"] == "ovs":
        value["healthy"] = False
    if state["plane"] == "ovs" and TOPOLOGY_FILE.is_file():
        value["controller_connections"] = controller_connections(
            load_json(TOPOLOGY_FILE))
        value["healthy"] = value["healthy"] and all(
            value["controller_connections"].values())
    return value


def _deploy_unlocked(plane):
    _requirements(plane)
    _validate_implementation(plane)
    prepare_evidence_directories()
    if RUNTIME_FILE.is_file():
        existing = status()
        if existing.get("plane") == plane and existing.get("healthy"):
            return existing
        _clean_unlocked()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "version": 1, "plane": plane, "started": time.time(),
        "scenario": "default", "frr": [], "controller": None,
    }
    atomic_json(RUNTIME_FILE, state)
    topology = None
    try:
        topology = lab3_topo.create(plane)
        for as_number in sorted(int(value) for value in topology["ases"]):
            _start_frr(as_number, state)
        _wait_bgp()
        lab3_topo.set_speaker_forwarding(topology, plane == "frr")
        lab3_topo.add_vxlan()
        if plane == "ovs":
            _start_controller(state, topology)
            _verify_invalid_ttl_pipeline(topology)
        state["ready"] = time.time()
        atomic_json(RUNTIME_FILE, state)

        def healthy():
            observed = status()
            return observed if observed.get("healthy") else False

        return wait_for("healthy deployment and live controller connections",
                        healthy, timeout=10, interval=0.1)
    except (RuntimeFailure, lab3_topo.CommandError, OSError,
            subprocess.SubprocessError, json.JSONDecodeError, ValueError) as deploy_error:
        try:
            _clean_unlocked()
        except (RuntimeFailure, lab3_topo.CommandError, OSError,
                subprocess.SubprocessError, json.JSONDecodeError) as cleanup_error:
            raise RuntimeFailure(
                "deploy failed: %s; rollback failed: %s; state preserved in %s" %
                (deploy_error, cleanup_error, STATE_DIR)) from deploy_error
        raise


def deploy(plane):
    with lifecycle_lock():
        return _deploy_unlocked(plane)


def _clean_unlocked():
    errors = []
    state = load_json(RUNTIME_FILE) if RUNTIME_FILE.is_file() else {}
    allowed_pathspaces = {"lab3-r%d" % asn for asn in range(1, 5)}
    for item in state.get("frr", []):
        if item.get("pathspace") not in allowed_pathspaces:
            raise RuntimeFailure("refusing to clean invalid FRR pathspace %r" %
                                 item.get("pathspace"))
        if item.get("daemon") not in ("zebra", "bgpd"):
            raise RuntimeFailure("refusing to clean invalid FRR daemon %r" %
                                 item.get("daemon"))
    controller = state.get("controller")
    if controller:
        try:
            stop_owned(controller["pid"], ["osken-manager", "controller.py", str(ROOT)])
        except (RuntimeFailure, OSError) as exc:
            errors.append(str(exc))
    for item in reversed(state.get("frr", [])):
        try:
            stop_owned(item["pid"], [item["daemon"], item["pathspace"]])
        except (RuntimeFailure, OSError) as exc:
            errors.append(str(exc))
    if TOPOLOGY_FILE.is_file():
        topology = load_json(TOPOLOGY_FILE)
        try:
            lab3_topo.remove(topology)
        except lab3_topo.CommandError as exc:
            errors.append(str(exc))
    if errors:
        raise RuntimeFailure("; ".join(errors))
    for item in state.get("frr", []):
        pathspace_dir = Path("/run/frr") / item["pathspace"]
        if pathspace_dir.is_dir():
            shutil.rmtree(pathspace_dir)
    if STATE_DIR.is_dir():
        shutil.rmtree(STATE_DIR)
    return {"cleaned": True}


def clean():
    with lifecycle_lock():
        return _clean_unlocked()


def update_state_scenario(name):
    state = load_json(RUNTIME_FILE)
    state["scenario"] = name
    atomic_json(RUNTIME_FILE, state)


def _scenario_unlocked(name):
    if not RUNTIME_FILE.is_file():
        raise RuntimeFailure("Lab 3 is not deployed")
    state = load_json(RUNTIME_FILE)
    topology = load_json(TOPOLOGY_FILE)
    if name == "link-down":
        lab3_topo.set_link(topology, "12", False)
    elif name == "link-up":
        lab3_topo.set_link(topology, "12", True)
    elif name == "policy-backup":
        vtysh(1, [
            "configure terminal", "router bgp 65001",
            "address-family ipv4 unicast",
            "no neighbor 10.12.0.2 route-map PREFER_AS2 in",
            "exit-address-family", "address-family ipv6 unicast",
            "no neighbor 2001:db8:12::2 route-map PREFER_AS2 in",
            "end", "clear bgp * soft in",
        ])
    elif name == "policy-default":
        vtysh(1, [
            "configure terminal", "router bgp 65001",
            "address-family ipv4 unicast",
            "neighbor 10.12.0.2 route-map PREFER_AS2 in",
            "exit-address-family", "address-family ipv6 unicast",
            "neighbor 2001:db8:12::2 route-map PREFER_AS2 in",
            "end", "clear bgp * soft in",
        ])
    elif name == "add-as":
        vtysh(2, [
            "configure terminal", "router bgp 65002",
            "neighbor 10.24.0.2 remote-as 65004",
            "neighbor 10.24.0.2 timers 2 6",
            "neighbor 2001:db8:24::2 remote-as 65004",
            "neighbor 2001:db8:24::2 timers 2 6",
            "address-family ipv4 unicast", "neighbor 10.24.0.2 activate",
            "exit-address-family", "address-family ipv6 unicast",
            "neighbor 2001:db8:24::2 activate", "end",
        ])
    elif name == "remove-as":
        vtysh(2, [
            "configure terminal", "router bgp 65002",
            "no neighbor 10.24.0.2", "no neighbor 2001:db8:24::2", "end",
        ])
    else:
        raise ValueError("unknown scenario %s" % name)
    update_state_scenario(name)
    return status()


def scenario(name):
    with lifecycle_lock():
        return _scenario_unlocked(name)
