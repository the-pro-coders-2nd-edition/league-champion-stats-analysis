"""Transitional re-export shim -- real code now lives in `league_stats_peers.analysis.peer.baseline`.

Aliases `sys.modules` to the real module rather than doing a plain
`import *`, because several tests monkeypatch this module's own globals
directly by attribute (`MIN_EXACT_GAMES`, `HIGH_CONFIDENCE_GAMES`,
`try_static_benchmark`, `try_role_benchmark`, `read_live_cache`,
`write_live_cache`) via a reference obtained through this old import path
(e.g. `tests/test_peer_baseline.py`, `tests/test_peers_service.py`). A
plain `import *` shim would copy those names into a *separate* module
object, so patching them there would not affect `resolve_peer_baseline`'s
own global lookups, which always resolve against the module it was
actually defined in. Aliasing makes both import paths resolve to the
exact same module object, so patching either one is equivalent.
"""

import sys

from league_stats_peers.analysis.peer import baseline as _real_module

sys.modules[__name__] = _real_module
