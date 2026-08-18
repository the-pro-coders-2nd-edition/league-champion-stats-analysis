<script>
  import CareerNode from '../components/CareerNode.svelte';
  import Chip from '../components/Chip.svelte';
  import SectionHeader from '../components/SectionHeader.svelte';
  import { dropCareerBlock } from '../lib/api.js';

  export let data;
  export let playerSlug = '';
  export let buildSlug = '';

  let confirmSlot = null;
  let dropping = false;
  let dropError = '';
  let dropped = false;

  $: career = data.career || { has_career: false, blocks: [], rules: [], legend: [], congrats: null };
  $: canDrop = !!(playerSlug && buildSlug);

  function askDrop(slot) {
    dropError = '';
    confirmSlot = slot;
  }

  function cancelDrop() {
    confirmSlot = null;
  }

  async function confirmDrop(block) {
    dropping = true;
    dropError = '';
    try {
      await dropCareerBlock(playerSlug, buildSlug, block.slot);
      dropped = true;
      confirmSlot = null;
    } catch (err) {
      dropError = err.message || 'Could not drop this block.';
    } finally {
      dropping = false;
    }
  }
</script>

<section id="career" class="report-section report-section--career">
  <SectionHeader id="career" title="Career mode" icon="trending-up" />

  {#if career.has_career}
    <div class="career-rules">
      {#each career.rules as rule}
        <div class="career-rule">
          <div class="career-rule-key">{rule.key}</div>
          <div class="career-rule-value">{rule.value}</div>
          <p class="career-rule-note">{rule.note}</p>
        </div>
      {/each}
    </div>

    {#if career.congrats}
      <div class="career-congrats">
        <div>
          <div class="career-congrats-label">Block complete</div>
          <div class="career-congrats-title">{career.congrats.title}</div>
          <p class="career-congrats-body">{career.congrats.body}</p>
        </div>
      </div>
    {/if}

    {#if dropped}
      <p class="career-drop-notice" role="status">
        Block dropped. The remaining block moved left and a replacement is being generated —
        this report updates when the run finishes.
      </p>
    {/if}

    <div class="career-legend">
      <div class="career-legend-title">The five states a goal can be in</div>
      {#each career.legend as entry}
        <div class="career-legend-row">
          <div class="career-ring career-ring--{entry.state_class}" style="--career-pct: {entry.pct}%">
            <div class="career-mark career-mark--{entry.state_class}">{entry.mark}</div>
          </div>
          <div class="career-legend-name career-legend-name--{entry.state_class}">{entry.name}</div>
          <div class="career-legend-text">{entry.text}</div>
        </div>
      {/each}
    </div>

    <div class="career-blocks">
      {#each career.blocks as block}
        <div class="career-block">
          <div class="career-block-head">
            <span class="career-block-position">{block.position}</span>
            <span class="career-block-state">
              <Chip tone={block.tone} dot={true} label={block.state_label} />
            </span>
            {#if canDrop && block.slot != null}
              <button
                type="button"
                class="career-drop-btn"
                aria-label="Drop {block.name}"
                disabled={dropping || dropped}
                on:click={() => askDrop(block.slot)}
              >Drop block</button>
            {/if}
          </div>
          <h3 class="career-block-name">{block.name}</h3>
          <p class="career-block-metric">{block.metric}</p>

          {#if confirmSlot === block.slot}
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
                  disabled={dropping}
                  on:click={() => confirmDrop(block)}
                >{dropping ? 'Dropping…' : 'Yes, drop it'}</button>
                <button type="button" class="career-drop-confirm-no" disabled={dropping} on:click={cancelDrop}>
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
                note={goal.note}
                count={goal.count}
                last={goal.last}
              />
            {/each}
          {:else}
            <div class="career-steps">
              {#each block.steps as step}
                <div class="career-step">
                  <i class="career-step-bullet"></i>
                  <p class="career-step-text">{step}</p>
                </div>
              {/each}
            </div>
            <p class="career-block-unlock">{block.unlock}</p>
          {/if}
        </div>
      {/each}
    </div>
  {:else}
    <p class="career-empty">
      No career ladder yet. Play a few more ranked games on this build and a set of personal goals will appear here.
    </p>
  {/if}
</section>

<style>
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
