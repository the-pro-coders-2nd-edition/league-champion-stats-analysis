"""Transitional re-export shim -- real code now lives in `league_stats_runner.service`.

Aliased via `sys.modules` (not `import *`) because tests monkeypatch this
module's globals by attribute through this old import path
(`tests/test_runner_service.py`, `tests/test_runner_metrics.py`, both patch
`execute_job` on `league_stats.runner.service`); a plain `import *` would
leave those mocks pointed at a dead copy of the name instead of the real
module the code actually runs against.
"""

import sys

import league_stats_runner.service as _real_module

sys.modules[__name__] = _real_module
