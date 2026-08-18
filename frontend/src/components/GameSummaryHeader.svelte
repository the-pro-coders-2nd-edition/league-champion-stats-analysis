<script lang="ts">
  export let result: 'win' | 'loss' = 'win';
  export let kda: string = '';
  export let score: number = 0;
  export let metaText: string = '';
  export let hasMetaChip: boolean = false;
  export let hasLoadout: boolean = false;
</script>

<!-- Class names below are kept identical to the pre-migration markup (`game-review-stage-*`)
     because `report.css` still targets some of their descendants globally — `.game-icon` inside
     `.game-review-stage-matchup`, and the runes/summoners icon sizing inside `.game-review-stage-
     loadout` — and those descendants arrive through the `champion`/`opponent`/`loadout` slots, so
     a scoped selector here cannot reach them. -->
<div class="game-review-stage-inner">
  <span class="game-review-result game-review-result--stage {result === 'win' ? 'game-review-result--win' : 'game-review-result--loss'}">
    {result === 'win' ? 'Victory' : 'Defeat'}
  </span>
  <div class="game-review-stage-matchup">
    <slot name="champion" />
    <span class="game-review-matchup-vs">vs</span>
    <slot name="opponent" />
  </div>
  <div class="game-review-stage-kda">{kda}</div>
  <div class="game-review-stage-score">
    <span class="game-review-stage-score-value">{score}</span>
    <slot name="score-chip" />
  </div>
  <div class="game-review-stage-meta">
    {#if hasMetaChip}
      <span class="game-review-stage-meta-chip"><slot name="meta-chip" /></span>
    {/if}
    <span class="game-review-stage-meta-text">{metaText}</span>
  </div>
  {#if hasLoadout}
    <div class="game-review-stage-loadout">
      <slot name="loadout" />
    </div>
  {/if}
</div>

<style>
  .game-review-stage-inner {
    margin-bottom: 14px;
    display: grid;
    grid-template-columns: auto 1fr auto;
    grid-template-areas:
      "result matchup score"
      "result kda score"
      "meta meta meta"
      "loadout loadout loadout";
    gap: 6px 16px;
    align-items: center;
    padding: 14px 16px;
    border-radius: 12px;
    border: 1px solid var(--color-divider);
    background: var(--color-surface-2);
  }
  .game-review-result--stage {
    grid-area: result;
    writing-mode: vertical-rl;
    transform: rotate(180deg);
    padding: 10px 6px;
    font-size: 12px;
    letter-spacing: .08em;
    text-transform: uppercase;
  }
  .game-review-stage-matchup {
    grid-area: matchup;
    display: flex; align-items: center; gap: 12px;
  }
  .game-review-matchup-vs { font-size: 12px; font-weight: 700; color: var(--color-neutral-400); text-transform: uppercase; }
  .game-review-stage-kda {
    grid-area: kda;
    font-size: 18px; font-weight: 700; letter-spacing: -.02em;
  }
  .game-review-stage-score {
    grid-area: score;
    display: flex; flex-direction: column; align-items: flex-end; gap: 6px;
  }
  .game-review-stage-score-value {
    font-size: 40px; font-weight: 800; line-height: 1; color: var(--color-accent); letter-spacing: -.03em;
  }
  .game-review-stage-meta {
    grid-area: meta;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    color: var(--color-neutral-400);
  }
  .game-review-stage-meta-text { color: var(--color-neutral-400); }
  .game-review-stage-loadout {
    grid-area: loadout;
    display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
    padding-top: 4px; border-top: 1px solid var(--color-divider);
  }
  @media (max-width: 860px) {
    .game-review-stage-inner {
      grid-template-columns: auto 1fr;
      grid-template-areas:
        "result matchup"
        "result kda"
        "score score"
        "meta meta"
        "loadout loadout";
    }
    .game-review-stage-score { align-items: flex-start; flex-direction: row; gap: 10px; }
    .game-review-stage-score-value { font-size: 32px; }
  }
</style>
