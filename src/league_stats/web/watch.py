"""Transitional re-export: split across two Phase 7 moves.

``watch_public_fields`` moved to ``league_stats_common.watch_fields`` (Task 2)
-- it formats ``JobStore`` rows, so it travels with ``JobStore`` rather than
with ``WatchPoller``. ``WatchPoller``/``MatchIdSource`` (and their private
helpers ``_Budget``/``_backoff_for``) moved to ``league_stats_cron_watch.watch``
(Task 4). Plain ``import *`` is safe for both -- no test monkeypatches this
module's globals by attribute -- but ``_Budget``/``_backoff_for`` need an
explicit re-export since ``tests/test_watch.py`` imports them directly and
``import *`` skips leading-underscore names.
"""

from league_stats_common.watch_fields import watch_public_fields  # noqa: F401
from league_stats_cron_watch.watch import *  # noqa: F401,F403
from league_stats_cron_watch.watch import _Budget, _backoff_for  # noqa: F401
