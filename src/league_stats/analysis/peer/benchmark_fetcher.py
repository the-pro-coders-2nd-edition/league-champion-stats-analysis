"""Transitional re-export shim -- real code now lives in `league_stats_peers.analysis.peer.benchmark_fetcher`.

Aliases `sys.modules` to the real module rather than doing a plain
`import *`, because several tests monkeypatch this module's own globals
directly by attribute or dotted-string path (`MIN_BENCHMARK_GAMES`,
`TARGET_PEER_GAMES`, `MAX_MATCH_DOWNLOADS`, `MATCH_IDS_PER_PLAYER`) via
this old import path (e.g. `tests/test_benchmark_fetcher.py`,
`tests/test_peer_blend.py`, `tests/test_peers_service.py`). A plain
`import *` shim would copy those names into a *separate* module object, so
patching them there would not affect `fetch_benchmark_from_api`'s own
global lookups, which always resolve against the module it was actually
defined in. Aliasing makes both import paths resolve to the exact same
module object, so patching either one is equivalent.
"""

import sys

from league_stats_peers.analysis.peer import benchmark_fetcher as _real_module

sys.modules[__name__] = _real_module
