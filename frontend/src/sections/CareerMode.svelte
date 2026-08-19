<script>
  import { flip } from 'svelte/animate';
  import { fade, fly } from 'svelte/transition';
  import CareerNode from '../components/CareerNode.svelte';
  import MetricTooltip from '../components/MetricTooltip.svelte';
  import Chip from '../components/Chip.svelte';
  import Modal from '../components/Modal.svelte';
  import SectionHeader from '../components/SectionHeader.svelte';
  import { dropCareerBlock } from '../lib/api.js';

  // The ladder to render, resolved by Report.svelte. Career follows neither the
  // queue filter nor the game-window filter, so it is not read off the slice.
  export let career = null;
  export let playerSlug = '';
  export let buildSlug = '';
  // True while an analysis job is running. A drop shifts every slot left, so
  // acting on a ladder that is mid-rebuild would target the wrong block.
  export let busy = false;
  export let onDropped = () => {};
  // Slot awaiting its replacement from the regenerate a drop kicked off. Rendered
  // as a skeleton until the rebuilt report lands and the real block takes over.
  export let pendingSlot = null;

  let confirmSlot = null;
  let droppingSlot = null;
  let dropError = '';
  let catalogOpen = false;

  $: ladder = career || {
    has_career: false, blocks: [], rules: [], legend: [], congrats: null, step_catalog: [],
  };
  $: stepCatalog = ladder.step_catalog || [];
  $: canDrop = !!(playerSlug && buildSlug);
  // Career spans every ranked game, so it does not follow the queue filter. The
  // caption says so rather than the ladder disappearing on a filtered view.
  $: tracksAllRanked = ladder.tracks_all_ranked !== false;
  $: visibleBlocks = (ladder.blocks || []).filter(
    (block, index) => (block.slot ?? index) !== pendingSlot
  );
  $: awaitingReplacement = pendingSlot !== null;

  function askDrop(slot) {
    dropError = '';
    confirmSlot = slot;
  }

  function cancelDrop() {
    confirmSlot = null;
  }

  function blockSlot(block, index) {
    return block.slot ?? index;
  }

  async function confirmDrop(slot) {
    droppingSlot = slot;
    dropError = '';
    try {
      const result = await dropCareerBlock(playerSlug, buildSlug, slot);
      confirmSlot = null;
      onDropped(result);
    } catch (err) {
      dropError = err.message || 'Could not drop this block.';
    } finally {
      droppingSlot = null;
    }
  }
</script>

<section id="career" class="report-section report-section--career">
  <SectionHeader
    id="career"
    title="Career mode"
    icon="trending-up"
    scope="All ranked · last 20 games"
  />

  {#if ladder.has_career && stepCatalog.length}
    <button
      type="button"
      class="career-catalog-btn"
      on:click={() => (catalogOpen = true)}
    >
      <iconify-icon icon="lucide:list" aria-hidden="true"></iconify-icon>
      All steps
    </button>
  {/if}

  {#if ladder.has_career}
    {#if tracksAllRanked}
      <p class="career-scope-caption">
        Career reads <strong>all ranked games</strong>, Solo/Duo and Flex together, over a rolling
        <strong>20-game window</strong>. It does not follow the queue or game-window filters above,
        so it shows the same ladder whichever you pick.
      </p>
    {/if}

    {#if ladder.congrats}
      <div class="career-congrats">
        <div>
          <div class="career-congrats-label">Block complete</div>
          <div class="career-congrats-title">{ladder.congrats.title}</div>
          <p class="career-congrats-body">{ladder.congrats.body}</p>
        </div>
      </div>
    {/if}

    <div class="career-blocks">
      {#each visibleBlocks as block, index (block.name)}
        {@const slot = blockSlot(block, index)}
        <div
          class="career-block"
          animate:flip={{ duration: 420 }}
          in:fly={{ x: 24, duration: 320 }}
        >
          <div class="career-block-head">
            <span class="career-block-state">
              <Chip tone={block.tone} dot={true} label={block.state_label} />
            </span>
            {#if canDrop}
              <button
                type="button"
                class="career-drop-btn"
                aria-label="Drop {block.name}"
                disabled={busy || droppingSlot !== null}
                title={busy ? 'Wait for the current run to finish' : 'Discard this block and generate a replacement'}
                on:click={() => askDrop(slot)}
              >Drop block</button>
            {/if}
          </div>
          <h3 class="career-block-name">{block.name}</h3>
          <p class="career-block-metric">{block.metric}</p>

          {#if confirmSlot === slot}
            <div class="career-drop-confirm" role="alertdialog" aria-label="Confirm dropping {block.name}">
              <p class="career-drop-confirm-text">
                Drop <strong>{block.name}</strong>? Its progress is lost. Any block behind it moves
                left and a replacement is generated. If this track is still the best fit for the
                build it can come straight back, with fresh targets.
              </p>
              <div class="career-drop-confirm-actions">
                <button
                  type="button"
                  class="career-drop-confirm-yes"
                  disabled={droppingSlot !== null}
                  on:click={() => confirmDrop(slot)}
                >{droppingSlot === slot ? 'Dropping…' : 'Yes, drop it'}</button>
                <button
                  type="button"
                  class="career-drop-confirm-no"
                  disabled={droppingSlot !== null}
                  on:click={cancelDrop}
                >
                  Keep it
                </button>
              </div>
              {#if dropError}<p class="career-drop-error">{dropError}</p>{/if}
            </div>
          {/if}

          {#if block.is_active}
            {#each block.goals as goal}
              <CareerNode
                state={goal.state}
                stateClass={goal.state_class}
                tone={goal.tone}
                pct={goal.pct}
                mark={goal.mark}
                text={goal.text}
                why={goal.why}
                note={goal.note}
                count={goal.count}
                last={goal.last}
              />
            {/each}
          {:else}
            <div class="career-steps">
              {#each block.steps as step}
                {@const stepText = typeof step === 'string' ? step : step.text}
                {@const stepWhy = typeof step === 'string' ? '' : step.why}
                <div class="career-step">
                  <i class="career-step-bullet"></i>
                  <p class="career-step-text">
                    {stepText}
                    {#if stepWhy}<MetricTooltip label="this goal" tooltip={stepWhy} />{/if}
                  </p>
                </div>
              {/each}
            </div>
            <p class="career-block-unlock">{block.unlock}</p>
          {/if}
        </div>
      {/each}
      {#if awaitingReplacement}
        <div
          class="career-block career-block--skeleton"
          aria-hidden="true"
          in:fade={{ duration: 200 }}
        >
          <div class="career-skeleton-head"></div>
          <div class="career-skeleton-title"></div>
          <div class="career-skeleton-line"></div>
          <div class="career-skeleton-line career-skeleton-line--short"></div>
          <p class="career-skeleton-note">Generating your next block…</p>
        </div>
      {/if}
    </div>

    <h3 class="career-howto">How the ladder works</h3>

    <div class="career-rules">
      {#each ladder.rules as rule}
        <div class="career-rule">
          <div class="career-rule-key">{rule.key}</div>
          <div class="career-rule-value">{rule.value}</div>
          <p class="career-rule-note">{rule.note}</p>
        </div>
      {/each}
    </div>

    <div class="career-legend">
      <div class="career-legend-title">The five states a goal can be in</div>
      {#each ladder.legend as entry}
        <div class="career-legend-row">
          <div class="career-ring career-ring--{entry.state_class}" style="--career-pct: {entry.pct}%">
            <div class="career-mark career-mark--{entry.state_class}">{entry.mark}</div>
          </div>
          <div class="career-legend-name career-legend-name--{entry.state_class}">{entry.name}</div>
          <div class="career-legend-text">{entry.text}</div>
        </div>
      {/each}
    </div>
  {:else if ladder.awaiting_peers}
    <div class="career-blocks">
      <div class="career-block career-block--skeleton" aria-live="polite">
        <div class="career-skeleton-head"></div>
        <div class="career-skeleton-title"></div>
        <div class="career-skeleton-line"></div>
        <div class="career-skeleton-line career-skeleton-line--short"></div>
        <p class="career-skeleton-note">Waiting for rank comparison before setting Career goals…</p>
      </div>
    </div>
  {:else}
    <p class="career-empty">
      No career ladder yet. Play a few more ranked games on this build and a set of personal goals will appear here.
    </p>
  {/if}
</section>

<Modal open={catalogOpen} title="Every Career step" onClose={() => (catalogOpen = false)}>
  <p class="career-catalog-lead">
    Every step in the bank, with the number your own last 20 games would currently ask for. A
    block only ever draws its three goals from here — this is the full pool, not a preview of
    what is coming next.
  </p>
  {#each stepCatalog as category}
    <div class="career-catalog-category">
      <h4 class="career-catalog-category-name">{category.name}</h4>
      <div class="career-catalog-steps">
        {#each category.steps as step}
          <div
            class="career-catalog-step"
            class:career-catalog-step--muted={step.role_mismatch || step.insufficient_data}
          >
            {#if step.insufficient_data}
              <span class="career-catalog-step-text">Not enough data yet</span>
            {:else}
              <span class="career-catalog-step-text">{step.text}</span>
            {/if}
            {#if step.role_mismatch}
              <Chip tone="flat" fill={false} bordered={true} label="Not your role" />
            {/if}
          </div>
        {/each}
      </div>
    </div>
  {/each}
</Modal>

<style>
  /* report.css:2057 pushes .career-block-state right; with the "Block X"
     kicker gone the pill leads the row and the button takes the right edge. */
  .career-block-head .career-block-state { margin-left: 0; }

  .career-drop-btn {
    margin-left: auto;
    border: 1px solid var(--color-divider);
    background: var(--color-surface-2);
    color: var(--color-neutral-500);
    border-radius: 999px;
    padding: 4px 10px;
    font: inherit;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    transition: color .15s, border-color .15s;
  }
  .career-drop-btn:hover:not(:disabled) {
    color: var(--tone-bad-fg);
    border-color: var(--tone-bad-line);
  }
  .career-drop-btn:disabled { opacity: .45; cursor: not-allowed; }

  .career-drop-confirm {
    display: grid;
    gap: var(--space-3);
    margin: var(--space-3) 0;
    padding: var(--space-4);
    border: 1px solid var(--tone-bad-line);
    border-radius: var(--radius-md);
    background: var(--tone-bad-soft);
  }
  .career-drop-confirm-text {
    margin: 0;
    font-size: 12px;
    line-height: 1.5;
    color: var(--color-text);
  }
  .career-drop-confirm-actions { display: flex; gap: var(--space-2); flex-wrap: wrap; }
  .career-drop-confirm-yes, .career-drop-confirm-no {
    border-radius: 8px;
    padding: 6px 14px;
    font: inherit;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
  }
  .career-drop-confirm-yes {
    border: 1px solid var(--tone-bad-line);
    background: transparent;
    color: var(--tone-bad-fg);
  }
  .career-drop-confirm-no {
    border: 1px solid var(--color-divider);
    background: var(--color-surface-2);
    color: var(--color-text);
  }
  .career-drop-confirm-yes:disabled, .career-drop-confirm-no:disabled {
    opacity: .45;
    cursor: not-allowed;
  }
  .career-drop-error { margin: 0; font-size: 12px; color: var(--tone-bad-fg); }

  
  
  
  
  .career-howto {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
    align-items: center;
    gap: var(--space-3);
    margin: var(--space-6) 0 var(--space-4);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: .1em;
    text-transform: uppercase;
    color: var(--color-neutral-400);
  }
  .career-howto::before,
  .career-howto::after {
    content: '';
    height: 1px;
    background: var(--color-divider);
  }

  .career-scope-caption {
    margin: 0 0 var(--space-4);
    font-size: 12px;
    color: var(--color-neutral-500);
  }

  .career-catalog-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin: 0 0 var(--space-4);
    border: 1px solid var(--color-divider);
    background: var(--color-surface-2);
    color: var(--color-neutral-400);
    border-radius: 999px;
    padding: 4px 12px;
    font: inherit;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    transition: color .15s, border-color .15s;
  }
  .career-catalog-btn:hover {
    color: var(--color-accent);
    border-color: var(--color-accent);
  }

  .career-catalog-lead {
    margin: 0 0 var(--space-4);
    font-size: 12px;
    line-height: 1.5;
    color: var(--color-neutral-500);
  }
  .career-catalog-category { margin: 0 0 var(--space-4); }
  .career-catalog-category-name {
    margin: 0 0 var(--space-2);
    font-size: 12px;
    font-weight: 700;
    color: var(--color-text);
    text-transform: uppercase;
    letter-spacing: .04em;
  }
  .career-catalog-steps { display: grid; gap: var(--space-2); }
  .career-catalog-step {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-3);
    border: 1px solid var(--color-divider);
    border-radius: var(--radius-sm);
    background: var(--color-surface-2);
  }
  .career-catalog-step-text { font-size: 12px; line-height: 1.4; color: var(--color-text); }
  .career-catalog-step--muted { opacity: .55; }
  .career-catalog-step--muted .career-catalog-step-text { color: var(--color-neutral-500); }

  .career-block--skeleton {
    display: grid;
    align-content: start;
    gap: 10px;
    pointer-events: none;
    border-style: dashed;
  }
  .career-skeleton-head { width: 96px; height: 19px; border-radius: 6px; }
  .career-skeleton-title { width: 62%; height: 17px; border-radius: 6px; }
  .career-skeleton-line { width: 100%; height: 11px; border-radius: 6px; }
  .career-skeleton-line--short { width: 45%; }
  .career-skeleton-head,
  .career-skeleton-title,
  .career-skeleton-line {
    background: linear-gradient(
      90deg,
      var(--color-surface-2) 0%,
      var(--color-neutral-800) 50%,
      var(--color-surface-2) 100%
    );
    background-size: 200% 100%;
    animation: career-shimmer 1.4s ease-in-out infinite;
  }
  .career-skeleton-note {
    margin: 4px 0 0;
    font-size: 11px;
    color: var(--color-neutral-600);
  }
  @keyframes career-shimmer {
    from { background-position: 200% 0; }
    to { background-position: -200% 0; }
  }
  @media (prefers-reduced-motion: reduce) {
    .career-skeleton-head,
    .career-skeleton-title,
    .career-skeleton-line { animation: none; }
  }

  .career-drop-notice {
    margin: 0 0 var(--space-4);
    padding: var(--space-3) var(--space-4);
    border: 1px solid var(--color-divider);
    border-radius: var(--radius-md);
    background: var(--color-surface-2);
    font-size: 12px;
    line-height: 1.5;
    color: var(--color-neutral-400);
  }
</style>
