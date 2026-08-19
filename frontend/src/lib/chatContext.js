// Scopes what stats the chatbot sends to Gemini to the tab the player is looking
// at, instead of the whole report. Built from the live $view/career the page
// already holds, so it reflects the active queue/window/account filters and
// includes career data (never persisted to the on-disk summary.json the server
// falls back to).

const IDENTITY_KEYS = ['player', 'champion', 'role', 'build_label', 'games'];

const SECTIONS_BY_TAB = {
  summary: ['overview', 'rank', 'peer_comparison', 'win_correlations', 'ml_model'],
  performance: ['peer_comparison', 'rank', 'overview'],
  champion: ['matchups', 'items', 'runes'],
  deepdive: [
    'laning',
    'economy',
    'vision',
    'deaths',
    'teamfights',
    'positioning',
    'objectives',
    'macro',
  ],
};

export const STARTER_PROMPTS_BY_TAB = {
  summary: [
    'What should I focus on to win more games?',
    'What is driving my win rate the most?',
    'How do I compare to players at my rank?',
  ],
  games: [
    'How did my last game go?',
    'What went wrong in my worst recent game?',
    'Which of my games counted toward a Career goal?',
  ],
  career: [
    'What do I need to hit my next Career goal?',
    'Which Career step is closest to unlocking?',
    'Explain how my Career ladder works.',
  ],
  performance: [
    'How do I compare to players at my rank?',
    'Is my form trending up or down?',
    'What is my biggest gap versus peers?',
  ],
  champion: [
    'What is my best matchup?',
    'What runes should I be running?',
    'Why do I struggle against my worst matchup?',
  ],
  deepdive: [
    'Where am I losing the lane?',
    'How is my vision score?',
    'Am I dying too much in teamfights?',
  ],
};

function identity(view) {
  const out = {};
  for (const key of IDENTITY_KEYS) {
    if (view && view[key] != null) out[key] = view[key];
  }
  return out;
}

function compactCareerBlocks(career) {
  if (!career || !career.has_career) return { has_career: false };
  const blocks = (career.blocks || []).map((block) => ({
    name: block.name,
    metric: block.metric,
    state_label: block.state_label,
    is_active: block.is_active,
    goals: block.is_active ? (block.goals || []).map((goal) => goal.text) : undefined,
  }));
  return { has_career: true, blocks };
}

function trimmedRecentGames(view, limit) {
  const recentGames = view && view.recent_games;
  if (!recentGames) return undefined;
  if (limit == null) return recentGames;
  return {
    ...recentGames,
    games: (recentGames.games || []).slice(0, limit).map((game) => ({
      index: game.index,
      date: game.date,
      result: game.result,
      opponent: game.opponent,
      kda: game.kda,
      score: game.score,
    })),
  };
}

/** Build the stats payload to send to the chatbot for the given active tab. */
export function buildTabContext(view, career, tab) {
  const context = identity(view);

  if (tab === 'games') {
    context.recent_games = trimmedRecentGames(view, null);
    context.career = compactCareerBlocks(career);
    return context;
  }

  if (tab === 'career') {
    context.career = career || { has_career: false };
    context.overview = view && view.overview;
    return context;
  }

  if (tab === 'performance') {
    for (const key of SECTIONS_BY_TAB.performance) {
      if (view && view[key] != null) context[key] = view[key];
    }
    context.recent_games = trimmedRecentGames(view, 5);
    return context;
  }

  const sections = SECTIONS_BY_TAB[tab] || SECTIONS_BY_TAB.summary;
  for (const key of sections) {
    if (view && view[key] != null) context[key] = view[key];
  }
  return context;
}
