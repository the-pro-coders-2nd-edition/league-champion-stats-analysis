// Which Career goals a single reviewed game was judged on, and how it did.
//
// Only the live block counts games: a queued block is inert until it shifts left,
// so highlighting its metrics would claim credit the ladder does not give. And a
// block only counts games played after it appeared (`since_ms`), so a game older
// than the live block was never tracked by it -- reporting that as a miss would be
// a lie about the player's record.

const MET = 'met';
const MISSED = 'missed';
const UNTRACKED = 'untracked';

export const GOAL_OUTCOMES = { MET, MISSED, UNTRACKED };

function liveGoals(ladder) {
  if (!ladder || !ladder.has_career) return [];
  const live = (ladder.blocks || []).find((block) => block.is_active);
  return (live && live.goals) || [];
}

function outcomeFor(goal, game) {
  const stats = game.key_stats || {};
  const value = stats[goal.column];
  if (value === null || value === undefined) return null;

  // A block that appeared after this game never counted it. `since_ms` of 0 means
  // no recorded start line, so fall through rather than silently untracking.
  const created = Number(game.game_creation_ms) || 0;
  const since = Number(goal.since_ms) || 0;
  if (since && created && created <= since) return UNTRACKED;

  const target = Number(goal.target);
  const met = goal.comparator === 'under' ? Number(value) < target : Number(value) >= target;
  return met ? MET : MISSED;
}

/** The live block's goals this game was measured against, with its outcome on each. */
export function careerGoalsForGame(ladder, game) {
  if (!game) return [];
  return liveGoals(ladder)
    .map((goal) => {
      const outcome = outcomeFor(goal, game);
      if (outcome === null) return null;
      return {
        text: goal.text,
        why: goal.why || '',
        column: goal.column,
        state: goal.state,
        outcome,
        value: (game.key_stats || {})[goal.column],
        target: goal.target,
        comparator: goal.comparator,
      };
    })
    .filter(Boolean);
}

/** column -> outcome, for marking the matching rows in the stat lists. */
export function goalOutcomeByColumn(ladder, game) {
  const byColumn = {};
  for (const goal of careerGoalsForGame(ladder, game)) {
    byColumn[goal.column] = goal;
  }
  return byColumn;
}
