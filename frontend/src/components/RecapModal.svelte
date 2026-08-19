<script>
  import Modal from './Modal.svelte';
  import IconCellSolo from './IconCellSolo.svelte';
  import { careerGoalsForGame } from '../lib/careerGameGoals.js';

  // The ladder carrying `pending_recap` (new_match_ids, progress deltas).
  export let career = null;
  // The "all ranked" Game Review bundle -- Career spans both queues, so new
  // games are looked up there regardless of the active queue filter.
  export let gameReviewAll = null;
  export let onClose = () => {};

  $: recap = career?.pending_recap || null;
  $: open = !!recap;
  $: liveGoals = (career?.blocks || []).find((b) => b.is_active)?.goals || [];
  $: allGames = gameReviewAll?.games || [];
  $: newGames = recap
    ? recap.new_match_ids
        .map((id) => allGames.find((g) => g.match_id === id))
        .filter(Boolean)
        .reverse() // most recent first
    : [];
  $: missingCount = recap ? recap.new_match_ids.length - newGames.length : 0;
  $: progressRows = (recap?.progress || []).map((item) => {
    const goal = liveGoals.find((g) => g.column === item.column);
    return {
      ...item,
      text: goal?.text || item.column,
    };
  });

  function goalMarks(game) {
    return careerGoalsForGame(career, game);
  }
</script>

<Modal {open} title="What's new since your last visit" size="large" {onClose}>
  {#if recap}
    <div class="recap">
      {#if progressRows.length}
        <section class="recap-section">
          <h4 class="recap-section-title">Career progress</h4>
          <div class="recap-progress-list">
            {#each progressRows as row (row.column)}
              <div class="recap-progress-row">
                <span class="recap-progress-text">{row.text}</span>
                <span class="recap-progress-count">
                  {row.before} → <strong>{row.after}</strong> of {row.need}
                </span>
              </div>
            {/each}
          </div>
        </section>
      {/if}

      <section class="recap-section">
        <h4 class="recap-section-title">
          {newGames.length} game{newGames.length === 1 ? '' : 's'} since your last visit
        </h4>
        {#if missingCount > 0}
          <p class="recap-missing-note">
            +{missingCount} more, too old for a detailed recap.
          </p>
        {/if}
        <div class="recap-games">
          {#each newGames as game (game.match_id)}
            <div class="recap-game recap-game--{game.result}">
              <div class="recap-game-head">
                <IconCellSolo name={game.champion || 'You'} icon={game.champion_icon} />
                <span class="recap-game-vs">vs</span>
                <IconCellSolo name={game.opponent || 'Opponent'} icon={game.opponent_icon} />
                <span class="recap-game-result">{game.result === 'win' ? 'Win' : 'Loss'}</span>
                <span class="recap-game-score">{game.score?.overall ?? 0}</span>
              </div>
              {#if (game.behaviors_good || []).length || (game.behaviors_bad || []).length}
                <div class="recap-game-behaviors">
                  {#each (game.behaviors_good || []).slice(0, 1) as b}
                    <span class="recap-behavior recap-behavior--good">{b.title}</span>
                  {/each}
                  {#each (game.behaviors_bad || []).slice(0, 1) as b}
                    <span class="recap-behavior recap-behavior--bad">{b.title}</span>
                  {/each}
                </div>
              {/if}
              {#if goalMarks(game).length}
                <div class="recap-game-goals">
                  {#each goalMarks(game) as mark (mark.column)}
                    <span class="recap-goal-mark recap-goal-mark--{mark.outcome}">
                      {mark.outcome === 'met' ? '✓' : mark.outcome === 'missed' ? '✕' : '–'}
                      {mark.text}
                    </span>
                  {/each}
                </div>
              {/if}
            </div>
          {/each}
        </div>
      </section>
    </div>
  {/if}
</Modal>

<style>
  .recap {
    display: grid;
    gap: var(--space-5);
  }
  .recap-section-title {
    margin: 0 0 var(--space-3);
    font-size: 13px;
    font-weight: 700;
    color: var(--color-text);
  }
  .recap-missing-note {
    margin: -8px 0 var(--space-3);
    font-size: 12px;
    color: var(--color-neutral-500);
  }
  .recap-progress-list {
    display: grid;
    gap: var(--space-2);
  }
  .recap-progress-row {
    display: flex;
    justify-content: space-between;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-3);
    border: 1px solid var(--color-divider);
    border-radius: var(--radius-md);
    background: var(--color-surface-2);
    font-size: 12px;
  }
  .recap-progress-count { color: var(--color-neutral-400); white-space: nowrap; }
  .recap-games {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: var(--space-3);
  }
  .recap-game {
    display: grid;
    gap: var(--space-2);
    padding: var(--space-3);
    border: 1px solid var(--color-divider);
    border-radius: var(--radius-md);
    background: var(--color-surface-2);
  }
  .recap-game--win { border-left: 3px solid var(--tone-good-fg); }
  .recap-game--loss { border-left: 3px solid var(--tone-bad-fg); }
  .recap-game-head {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-size: 12px;
  }
  .recap-game-vs { color: var(--color-neutral-500); }
  .recap-game-result { margin-left: auto; font-weight: 700; }
  .recap-game-score { color: var(--color-neutral-400); }
  .recap-game-behaviors { display: flex; flex-wrap: wrap; gap: 6px; font-size: 11px; }
  .recap-behavior { padding: 2px 6px; border-radius: 999px; background: var(--color-surface); }
  .recap-behavior--good { color: var(--tone-good-fg); }
  .recap-behavior--bad { color: var(--tone-bad-fg); }
  .recap-game-goals { display: grid; gap: 2px; font-size: 11px; }
  .recap-goal-mark--met { color: var(--tone-good-fg); }
  .recap-goal-mark--missed { color: var(--tone-bad-fg); }
  .recap-goal-mark--untracked { color: var(--color-neutral-600); }
</style>
