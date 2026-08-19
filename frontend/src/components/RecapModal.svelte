<script>
  import { getContext } from 'svelte';
  import Modal from './Modal.svelte';
  import IconCellSolo from './IconCellSolo.svelte';
  import { careerGoalsForGame } from '../lib/careerGameGoals.js';
  import { REPORT_NAV_KEY, handleNavClick } from '../lib/reportNav.js';

  // The ladder carrying `pending_recap` (new_match_ids, progress deltas).
  export let career = null;
  // The "all ranked" Game Review bundle -- Career spans both queues, so new
  // games are looked up there regardless of the active queue filter.
  export let gameReviewAll = null;
  export let onClose = () => {};

  const reportNav = getContext(REPORT_NAV_KEY);

  $: recap = career?.pending_recap || null;
  $: open = !!recap;
  $: liveBlock = (career?.blocks || []).find((b) => b.is_active) || null;
  $: liveGoals = liveBlock?.goals || [];
  $: allGames = gameReviewAll?.games || [];
  $: newGames = recap
    ? recap.new_match_ids
        .map((id) => allGames.find((g) => g.match_id === id))
        .filter(Boolean)
        .reverse() // most recent first
    : [];
  $: missingCount = recap ? recap.new_match_ids.length - newGames.length : 0;
  // Every goal in the live block gets its own ring -- a block holds three at
  // once, not one -- with the before/after delta from this recap when the
  // block hasn't changed since the reader's last visit.
  $: goalRings = liveGoals.map((goal) => {
    const progress = (recap?.progress || []).find((item) => item.column === goal.column);
    const delta = progress ? progress.after - progress.before : 0;
    return { ...goal, delta };
  });

  function goalMarks(game) {
    return careerGoalsForGame(career, game);
  }

  function goToCareer(event) {
    handleNavClick(reportNav, 'career')(event);
    onClose();
  }
</script>

<Modal {open} title="Welcome back" size="large" {onClose}>
  {#if recap}
    <div class="recap">
      {#if liveBlock}
        <a
          href="#career"
          class="recap-hero"
          on:click={goToCareer}
          title="Open Career mode"
        >
          <div class="recap-hero-label">
            <b>{liveBlock.name}</b>
            <span>{newGames.length} new game{newGames.length === 1 ? '' : 's'}</span>
          </div>
          <div class="recap-rings">
            {#each goalRings as goal (goal.column)}
              <div class="recap-ring-item">
                <div
                  class="career-ring career-ring--{goal.state_class}"
                  style="--career-pct: {goal.pct}%"
                >
                  <div class="career-mark career-mark--{goal.state_class}">{goal.hit}/{goal.need}</div>
                </div>
                {#if goal.delta > 0}
                  <span class="recap-ring-delta">+{goal.delta}</span>
                {/if}
                <p class="recap-ring-caption">{goal.text}</p>
              </div>
            {/each}
          </div>
        </a>
      {/if}

      <div class="recap-games">
        {#each newGames as game, index (game.match_id)}
          <a
            href="#career"
            class="recap-game recap-game--{game.result}"
            style="animation-delay: {0.45 + index * 0.08}s"
            on:click={goToCareer}
            title="Open Career mode"
          >
            <div class="recap-game-matchup">
              <IconCellSolo name={game.champion || 'You'} icon={game.champion_icon} />
              <span class="recap-game-vs">vs</span>
              <IconCellSolo name={game.opponent || 'Opponent'} icon={game.opponent_icon} />
            </div>
            <div class="recap-game-num">{game.score?.overall ?? 0}</div>
            <div class="recap-game-num-lbl">score</div>
            <div class="recap-game-kda">{game.kda}</div>
            {#if goalMarks(game).length}
              <div class="recap-game-goals">
                {#each goalMarks(game) as mark (mark.column)}
                  <i
                    class="recap-goal-mark recap-goal-mark--{mark.outcome}"
                    title={mark.text}
                  >{mark.outcome === 'met' ? '✓' : mark.outcome === 'missed' ? '✕' : '–'}</i>
                {/each}
              </div>
            {/if}
          </a>
        {/each}
        {#if missingCount > 0}
          <div class="recap-more">+{missingCount} more</div>
        {/if}
      </div>
    </div>
  {/if}
</Modal>

<style>
  .recap {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-8);
    padding: var(--space-4) 0 var(--space-2);
  }

  .recap-hero {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-4);
    color: inherit;
    text-decoration: none;
    opacity: 0;
    transform: translateY(8px);
    animation: recapRiseIn .35s .05s ease forwards;
  }
  .recap-hero-label { text-align: center; }
  .recap-hero-label b { display: block; font-size: 16px; }
  .recap-hero-label span { font-size: 12px; color: var(--color-neutral-500); }

  .recap-rings { display: flex; gap: var(--space-6); flex-wrap: wrap; justify-content: center; }
  .recap-ring-item {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-2);
    width: 110px;
    opacity: 0;
    transform: scale(.85);
    animation: recapRingIn .4s cubic-bezier(.2, 1.2, .4, 1) forwards;
  }
  .recap-ring-caption {
    margin: 0;
    font-size: 11px;
    line-height: 1.35;
    color: var(--color-neutral-400);
    text-align: center;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .recap-ring-item:nth-child(1) { animation-delay: .15s; }
  .recap-ring-item:nth-child(2) { animation-delay: .25s; }
  .recap-ring-item:nth-child(3) { animation-delay: .35s; }

  /* Reuses CareerNode's own ring/mark classes (declared :global there so this
     works), scaled up for the hero moment instead of the Career tab's compact
     list size. CareerNode.svelte itself is untouched. */
  .recap-ring-item :global(.career-ring) { width: 84px; height: 84px; }
  .recap-ring-item :global(.career-mark) {
    width: 68px; height: 68px; font-size: 14px; font-weight: 800;
  }
  .recap-ring-delta {
    position: absolute; top: -4px; right: -4px;
    background: var(--tone-good-fg); color: var(--color-bg);
    font-size: 10px; font-weight: 800; padding: 2px 6px; border-radius: 999px;
  }

  .recap-games {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: var(--space-3);
  }
  .recap-game {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    width: 128px;
    padding: var(--space-3) var(--space-2) var(--space-2);
    border-radius: var(--radius-md);
    border: 1px solid var(--color-divider);
    border-top: 3px solid transparent;
    background: var(--color-surface-2);
    color: inherit;
    text-decoration: none;
    opacity: 0;
    transform: translateY(16px) scale(.92);
    animation: recapCardIn .4s cubic-bezier(.2, 1.2, .4, 1) forwards;
    transition: border-color .15s;
  }
  .recap-game:hover { border-color: var(--color-accent); }
  .recap-game--win { border-top-color: var(--tone-good-line); }
  .recap-game--loss { border-top-color: var(--tone-bad-line); }
  .recap-game-matchup { display: flex; align-items: center; gap: 4px; }
  .recap-game-vs { font-size: 9px; color: var(--color-neutral-600); font-weight: 700; }
  .recap-game-num { font-size: 22px; font-weight: 800; font-variant-numeric: tabular-nums; line-height: 1; }
  .recap-game--win .recap-game-num { color: var(--tone-good-fg); }
  .recap-game--loss .recap-game-num { color: var(--tone-bad-fg); }
  .recap-game-num-lbl {
    font-size: 9px; color: var(--color-neutral-600); text-transform: uppercase; letter-spacing: .06em;
  }
  .recap-game-kda { font-size: 11px; color: var(--color-neutral-400); font-variant-numeric: tabular-nums; }
  .recap-game-goals { display: flex; gap: 4px; margin-top: 2px; }
  .recap-goal-mark {
    width: 16px; height: 16px; border-radius: 999px; display: grid; place-items: center;
    font-style: normal; font-size: 9px; font-weight: 800;
  }
  .recap-goal-mark--met { background: var(--tone-good-soft); color: var(--tone-good-fg); }
  .recap-goal-mark--missed { background: var(--tone-bad-soft); color: var(--tone-bad-fg); }
  .recap-goal-mark--untracked { background: var(--color-surface-3); color: var(--color-neutral-600); }
  .recap-more {
    align-self: center;
    font-size: 12px;
    color: var(--color-neutral-500);
  }

  @keyframes recapRiseIn { to { opacity: 1; transform: translateY(0); } }
  @keyframes recapRingIn { to { opacity: 1; transform: scale(1); } }
  @keyframes recapCardIn { to { opacity: 1; transform: translateY(0) scale(1); } }

  @media (prefers-reduced-motion: reduce) {
    .recap-hero, .recap-ring-item, .recap-game { animation: none; opacity: 1; transform: none; }
  }
</style>
