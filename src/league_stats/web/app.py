"""Transitional re-export shim -- real code now lives in `league_stats_api_ui.app`.

Aliases `sys.modules` to the real module rather than doing a plain `import *`,
because 4 test files (`tests/test_web_api.py`, `tests/test_welcome_back_cache.py`,
`tests/test_web_metrics.py`, `tests/test_web_account_filter.py`) do
`from league_stats.web import app as web_app` and then heavily monkeypatch
attributes directly on that `web_app` object -- `_verify_players_exist`,
`_build_precheck_client`, `gemini_reply`, `WatchPoller`, `AnalysisWorker`,
`WelcomeBackSubscriber`, `load_config`, `load_all_records`. `create_app`'s
nested closures always resolve those names against the module they were
actually *defined* in (the new `league_stats_api_ui.app`), not whichever
module a caller imported it through. A plain `import *` shim would leave
every one of those mocks patching a namespace `create_app` never looks at.
`sys.modules[__name__] = _real_module` makes both import paths resolve to
the literal same module object, so patching either is equivalent, and
underscore names (`_verify_players_exist`, `_build_precheck_client`, etc.)
come along for free.
"""

import sys

from league_stats_api_ui import app as _real_module

sys.modules[__name__] = _real_module
