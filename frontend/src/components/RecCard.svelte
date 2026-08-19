<script lang="ts">
  import RecCardHead from './RecCardHead.svelte';
  import RecEvidenceSummary from './RecEvidenceSummary.svelte';
  import Disclosure from './Disclosure.svelte';

  export let rec: Record<string, any>;
  export let highlighted = false;

  $: rows = Array.isArray(rec.evidence_rows) ? rec.evidence_rows : [];
</script>

<div class="rec {rec.badge}" class:is-highlighted={highlighted} id={rec.anchor || undefined} aria-current={highlighted ? 'true' : undefined}>
  <RecCardHead
    badge={rec.badge}
    priorityLabel={rec.priority_label || 'Medium'}
    category={rec.category}
    title={rec.title}
    detail={rec.detail}
  />
  <Disclosure variant="evidence" chevron="none">
    <svelte:fragment slot="summary">Why this?</svelte:fragment>
    {#if rec.evidence_summary}
      <RecEvidenceSummary text={rec.evidence_summary} />
    {/if}
    {#if rows.length}
      <dl class="rec-evidence-rows">
        {#each rows as row}
          <div class="rec-evidence-row">
            <dt class="rec-evidence-key">{row.label}</dt>
            <dd class="rec-evidence-value">{row.value}</dd>
          </div>
        {/each}
      </dl>
    {:else if rec.evidence}
      <p class="rec-evidence-stats">{rec.evidence}</p>
    {/if}
  </Disclosure>
</div>

<style>
  .rec-evidence-stats {
    margin: var(--space-2) 0 0;
    font-size: 11px;
    line-height: 1.45;
    color: var(--color-neutral-400);
    font-variant-numeric: tabular-nums;
  }
</style>
