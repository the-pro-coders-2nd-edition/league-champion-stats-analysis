"""Transitional re-export shim -- real code now lives in `league_stats_peers.service`.

Aliases `sys.modules` to the real module rather than doing a plain
`import *`, because `tests/test_peers_service.py` monkeypatches this
module's own names directly by attribute (`resolve_peer_baseline`,
`grpc.insecure_channel`, and Prometheus counters/histograms) via a
reference obtained through this old import path
(`from league_stats.peers import service as peers_service`). A plain
`import *` shim would copy those names into a *separate* module object, so
patching them there would not affect `PeersServicer`'s own global lookups,
which always resolve against the module it was actually defined in.
Aliasing also means underscore names (`_parse_rank`, `_PeerStoreAdapter`,
`_build_riot_client_for_platform`, `_db_name_from_uri`, all imported
directly by tests) are naturally present without needing an explicit
re-export list.
"""

import sys

from league_stats_peers import service as _real_module

sys.modules[__name__] = _real_module
