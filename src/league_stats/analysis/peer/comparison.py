"""Transitional re-export shim -- real code now lives in `league_stats_peers.analysis.peer.comparison`.

Aliases `sys.modules` to the real module rather than doing a plain
`import *`, because `tests/test_peer_comparison.py` monkeypatches this
module's `resolve_peer_baseline` name directly via a reference obtained
through this old import path. A plain `import *` shim would copy that name
into a *separate* module object, so patching it there would not affect
`build_peer_comparison`'s own global lookup, which always resolves against
the module it was actually defined in. Aliasing also means underscore
names (`_verdict`, `_extract_champion_role_from_match`,
`_comparison_summary_line`, all imported directly by tests) are naturally
present without needing an explicit re-export list.
"""

import sys

from league_stats_peers.analysis.peer import comparison as _real_module

sys.modules[__name__] = _real_module
