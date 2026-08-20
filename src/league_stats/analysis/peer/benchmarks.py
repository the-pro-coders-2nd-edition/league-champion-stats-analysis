"""Transitional re-export shim -- real code now lives in `league_stats_peers.analysis.peer.benchmarks`.

Aliases `sys.modules` to the real module rather than doing a plain
`import *`, because `tests/test_peer_comparison.py` monkeypatches this
module's `BENCHMARKS_DIR` global directly via a reference obtained through
this old import path. A plain `import *` shim would copy that name into a
*separate* module object, so patching it there would not affect
`resolve_benchmark_path`'s own global lookup, which always resolves
against the module it was actually defined in. Aliasing makes both import
paths resolve to the exact same module object, so patching either one is
equivalent.
"""

import sys

from league_stats_peers.analysis.peer import benchmarks as _real_module

sys.modules[__name__] = _real_module
