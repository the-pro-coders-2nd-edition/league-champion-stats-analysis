"""Transitional re-export shim -- real code now lives in `league_stats_cron_watch.__main__`.

Aliases `sys.modules` to the real module rather than doing a plain
`import *`, because `tests/test_cron_watch_service.py`'s
`test_serve_accepts_a_riot_api_key_supplied_only_via_dotenv` does
`import league_stats.cron_watch.__main__ as main_module` then
`monkeypatch.setattr(main_module, "JobStore", _fake_job_store)` before calling
`main_module.serve()`. `serve()`'s `JobStore` global always resolves against
the module it was actually *defined* in (the new `league_stats_cron_watch.__main__`),
not whichever module a caller imported it through. A plain shim would leave
the monkeypatched `JobStore` on a namespace `serve()` never looks at, so the
test would fall through to the real (heavy, hanging) gRPC/sqlite startup path
instead of raising the sentinel. `sys.modules[__name__] = _real_module` makes
both import paths resolve to the literal same module object, so patching
either is equivalent.

`docker-compose.yml` still runs `python -m league_stats.cron_watch` until
Task 8 of Phase 7 renames it to `python -m league_stats_cron_watch`, so this
module must also stay runnable as a script -- same concern
`peers/__main__.py`'s shim documents, though that one didn't need the
`sys.modules` aliasing since nothing monkeypatches its globals. The `if
__name__ == "__main__"` guard below runs when this file executes as `-m
league_stats.cron_watch` (where `__name__` is `"__main__"`, not this
module's real dotted name, so the aliasing line is a harmless no-op in that
case); the aliasing takes effect when this module is imported normally by
its dotted name, which is the case the test above exercises.
"""

import sys

from league_stats_cron_watch import __main__ as _real_module

sys.modules[__name__] = _real_module

if __name__ == "__main__":
    _real_module.main()
