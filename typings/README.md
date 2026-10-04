# Inspected os-ken 2.8.1 subset

These declarations support this lab's OpenFlow 1.3 controller and editor hover
in the **generated student tree**. They are not a complete os-ken SDK, an
executable substitute, or a promise about other OpenFlow versions. Unsupported
members/fields should produce type errors; inspect the real API and add a
contract test before extending the subset.

The declarations were checked against the `os-ken==2.8.1` wheel and these
versioned upstream modules:

- [application lifecycle](https://github.com/openstack/os-ken/blob/2.8.1/os_ken/base/app_manager.py)
- [Datapath lifecycle and send results](https://github.com/openstack/os-ken/blob/2.8.1/os_ken/controller/controller.py)
- [generated events](https://github.com/openstack/os-ken/blob/2.8.1/os_ken/controller/ofp_event.py)
  and [signature-preserving decorators](https://github.com/openstack/os-ken/blob/2.8.1/os_ken/controller/handler.py)
- [green-thread lifecycle](https://github.com/openstack/os-ken/blob/2.8.1/os_ken/lib/hub.py)
- [message buffers](https://github.com/openstack/os-ken/blob/2.8.1/os_ken/ofproto/ofproto_parser.py)
  and [OpenFlow 1.3 codecs](https://github.com/openstack/os-ken/blob/2.8.1/os_ken/ofproto/ofproto_v1_3_parser.py)

`Datapath13` in [harness/openflow.py](../harness/openflow.py) is a local
structural view after **OpenFlow 1.3 negotiation**, not a globally narrowed
`os_ken.controller.controller.Datapath`. Its ID can still be absent before
features processing. `send_msg` returns the real boolean enqueue result.
`close()` returns `None`. Native inspection in the pinned course image also
confirmed the codec constructor defaults; the FlowMod declaration records the
numeric add-command, priority and no-buffer defaults explicitly.
New message buffers, PacketIn data/matches, and received optional fields may be
`None`; serialization creates a mutable buffer. The event decorator retains
the decorated callable's parameter and return types.

Match declarations cover only the fields used here, with numeric ports and
protocol fields and string/masked-string addresses; the internal ordered-field
bypass is not supported. Set-field declarations
cover the new API's Ethernet source/destination forms, not the deprecated
`OFPMatchField` positional API. The hub subset supports this lab's zero-argument
callback. Its returned protocol is the used interface of eventlet's real
`GreenThread`; `wait()` can return `None` when os-ken logs a callback
failure. Application constructor arguments are opaque because OSKenApp accepts
and ignores them; this is not a fallback for structured network data.

Import order matters in this image: initialize `os_ken.base.app_manager`
before importing `os_ken.controller.controller.Datapath` at runtime, otherwise
the SDK can fail with a circular import. The application preserves this order;
the local structural types do not import Datapath at runtime. Native tests also
run the Datapath test alone in a fresh Python process, rather than relying on
another test having populated the import cache.

The course uses Python 3.12, FRR/kernel JSON and os-ken 2.8.1, **not Mininet**.
The private source's typing tests use Pyright 1.1.414, positive `assert_type`
contracts, deliberate invalid calls, and the real shared student materializer.
Actual codec/lifecycle assertions additionally run inside the pinned course
image. Missing runtime-source warnings on a host with only these declarations
do not prove runtime compatibility.
