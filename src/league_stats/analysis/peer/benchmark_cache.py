"""Transitional re-export shim -- real code now lives in `league_stats_peers.analysis.peer.benchmark_cache`.

Aliases `sys.modules` to the real module rather than doing a plain
`import *`: `tests/conftest.py`'s autouse fixture and
`tests/test_peer_cache_invalidation.py`/`test_peer_blend.py` monkeypatch
this module's own globals directly by attribute (`_store`,
`_LIVE_CACHE_DIR`, `time.time`) via a reference obtained through this old
import path. A plain `import *` shim would copy those names into a
*separate* module object, so patching them there would not affect
`read_live_cache`/`write_live_cache`'s own global lookups, which always
resolve against the module they were actually defined in. Aliasing makes
both import paths resolve to the exact same module object, so patching
either one is equivalent.
"""

import sys

from league_stats_peers.analysis.peer import benchmark_cache as _real_module

sys.modules[__name__] = _real_module
