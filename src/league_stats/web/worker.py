"""Transitional re-export shim -- real code now lives in `league_stats_runner.worker`.

Aliased via `sys.modules` (not `import *`) because tests monkeypatch this
module's globals by attribute through this old import path
(`tests/test_web_worker.py`, `tests/test_runner_service.py`); a plain
`import *` would leave those mocks pointed at a dead copy of the names
instead of the real module the code actually runs against.
"""

import sys

import league_stats_runner.worker as _real_module

sys.modules[__name__] = _real_module
