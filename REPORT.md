# Lab 3 report

Keep the headings. Record the actual next hops, packet observations, and timing
from your runs; do not copy another machine's numbers. The TA checks the content
of these explanations during the supervised assessment.

## Topology and addressing

TODO: Draw your topology and list customer prefixes, peer addresses, AS numbers, and default/alternate paths.

## Control and data planes

TODO: Identify which process performs each control/data-plane action and cite your packet/counter evidence.

## Prefix matching and route lifecycle

TODO: Explain LPM, next-hop resolution, and how your controller handles route additions, replacements and withdrawals.

## L2 versus L3 hop behavior

TODO: Compare measured intra/inter/transit hop counts and explain both Ethernet rewrites and the TTL/Hop Limit=1 result.

## Failure and recovery

TODO: For each injected event, compare your prediction, observed route/flow changes, measured recovery and restoration.

## VXLAN and MTU

TODO: Explain your captured outer/inner headers, VNI, measured boundary, and why the underlay and overlay MTUs differ.

## Limitations

TODO: State tested behavior, unsupported cases, assumptions and what an additional production-quality test would need.

## Evidence claims

After running the experiments, use `python3 tests/grade.py evidence` to inspect
the summary, then record your claims here. Each claim is checked against a fresh
canonical run. Volatile timings and run IDs are not hashed into your report:
rerunning the same successful experiment must not invalidate a truthful report.
Keep the full JSON, logs and captures as the source of your numerical discussion.

<!-- BEGIN EVIDENCE -->
```json
{}
```
<!-- END EVIDENCE -->
