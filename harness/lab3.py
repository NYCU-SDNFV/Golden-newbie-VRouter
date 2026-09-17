#!/usr/bin/env python3
"""Lab 3 command-line runtime.

Check mode writes exactly one JSON object to stdout.  Diagnostics and retained
daemon logs never contaminate that machine-readable stream.
"""
import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import checks, runtime
from topo import lab3_topo

CASES = [
    "bgp4", "bgp6", "frr4", "frr6", "intra4", "intra6", "inter4",
    "inter6", "transit4", "transit6", "vxlan", "ttl4", "ttl6",
    "fib-sync", "withdrawal", "failover", "policy", "new-as", "control-down",
]
SCENARIOS = [
    "link-down", "link-up", "policy-backup", "policy-default", "add-as",
    "remove-as",
]


def parser():
    root = argparse.ArgumentParser(description="SDNFV Lab 3 networking runtime")
    sub = root.add_subparsers(dest="command", required=True)
    deploy = sub.add_parser("deploy")
    deploy.add_argument("--plane", choices=("frr", "ovs"), default="ovs")
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    sub.add_parser("clean")
    check = sub.add_parser("check")
    check.add_argument("case", choices=CASES)
    check.add_argument("--json", action="store_true")
    scenario = sub.add_parser("scenario")
    scenario.add_argument("name", choices=SCENARIOS)
    return root


def emit(value):
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def main(argv=None):
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(message)s")
    try:
        if args.command == "deploy":
            emit(runtime.deploy(args.plane))
            return 0
        if args.command == "status":
            value = runtime.status()
            emit(value)
            return 0 if value.get("healthy", not value.get("deployed")) else 1
        if args.command == "clean":
            emit(runtime.clean())
            return 0
        if args.command == "scenario":
            emit(runtime.scenario(args.name))
            return 0
        passed, measurements = checks.run_case(args.case)
        emit({"case": args.case, "passed": bool(passed),
              "measurements": measurements, "errors": []})
        return 0 if passed else 1
    except (checks.CheckFailure, runtime.RuntimeFailure, lab3_topo.CommandError,
            OSError, subprocess.SubprocessError, json.JSONDecodeError,
            ValueError) as exc:
        if args.command == "check":
            emit({"case": args.case, "passed": False, "measurements": {},
                  "errors": [str(exc)]})
        else:
            print("lab3: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
