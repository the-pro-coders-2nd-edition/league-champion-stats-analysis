<script>
  export let build = null;

  const SLOTS = ['Q', 'W', 'E', 'R'];

  $: levels = (build && build.skill_levels_by_level) || [];
  $: icons = (build && build.ability_icons) || {};
  $: maxLevel = (build && build.skill_max_level) || ((build && build.skill_sequence) || []).length;
  $: visibleLevels = levels.slice(0, maxLevel);
  $: columns = Array.from({ length: maxLevel }, (_, index) => index + 1);
  $: rows = SLOTS.map((slot) => ({
    slot,
    icon: icons[slot] || null,
    cells: visibleLevels.map((row, levelIndex) => {
      const value = (row && row[slot]) || 0;
      const prev = levelIndex > 0 ? ((visibleLevels[levelIndex - 1] && visibleLevels[levelIndex - 1][slot]) || 0) : 0;
      return { levelIndex, gained: value > prev, empty: !value };
    }),
  }));
</script>

{#if !maxLevel || !levels.length}
  <p class="sub game-review-verdict-empty">No skill data for this game.</p>
{:else}
  <div class="game-review-skill-progression">
    <div class="game-review-skill-grid-wrap">
      <table class="game-review-skill-grid" aria-label="Skill points by level">
        <thead>
          <tr>
            <th class="game-review-skill-ability" scope="col" aria-label="Ability"></th>
            {#each columns as level (level)}
              <th scope="col">{level}</th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#each rows as row (row.slot)}
            <tr>
              <th class="game-review-skill-ability" scope="row">
                {#if row.icon}
                  <span class="icon-cell icon-cell--solo" title={row.slot}>
                    <img src={row.icon} alt={row.slot} class="game-icon game-icon--sm">
                  </span>
                {:else}
                  —
                {/if}
              </th>
              {#each row.cells as cell (cell.levelIndex)}
                <td
                  class="game-review-skill-cell{cell.gained ? ' game-review-skill-cell--gain' : ''}{cell.empty ? ' game-review-skill-cell--empty' : ''}"
                  aria-label={cell.gained ? `Level ${cell.levelIndex + 1} ${row.slot}` : null}
                ></td>
              {/each}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </div>
{/if}

<style>
  .game-review-skill-grid-wrap {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
  .game-review-skill-grid {
    border-collapse: collapse;
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    min-width: 100%;
  }
  .game-review-skill-grid th,
  .game-review-skill-grid td {
    border: 1px solid var(--color-divider);
    text-align: center;
    padding: 0;
    width: 18px;
    min-width: 18px;
    height: 18px;
  }
  .game-review-skill-grid th {
    font-size: 10px;
    font-weight: 700;
    color: var(--color-neutral-400);
    background: rgba(255, 255, 255, 0.03);
  }
  .game-review-skill-grid .game-review-skill-ability {
    width: 36px;
    min-width: 36px;
    padding: 4px;
    background: rgba(255, 255, 255, 0.03);
  }
  .game-review-skill-grid .game-review-skill-ability .game-icon {
    width: 24px;
    height: 24px;
    border-radius: 6px;
  }
  .game-review-skill-grid td.game-review-skill-cell--gain {
    background: rgba(65, 183, 140, 0.35);
  }
  .game-review-skill-grid td.game-review-skill-cell--empty {
    background: transparent;
  }
</style>
