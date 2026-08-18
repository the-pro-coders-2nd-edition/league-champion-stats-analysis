<script lang="ts">
  import RecCardHead from './RecCardHead.svelte';
  import RecEvidenceSummary from './RecEvidenceSummary.svelte';

  export let rec: Record<string, any>;

  $: pValueSuffix = rec.p_value != null ? ` · p = ${Number(rec.p_value).toFixed(4)}` : '';
  $: sampleSuffix = rec.sample_size ? ` · ${rec.sample_size} games` : '';
</script>

<div class="rec {rec.badge}" id={rec.anchor || undefined}>
  <RecCardHead
    badge={rec.badge}
    priorityLabel={rec.priority_label || 'Medium'}
    category={rec.category}
    title={rec.title}
    detail={rec.detail}
  />
  <details class="rec-evidence">
    <summary>Why this?</summary>
    {#if rec.evidence_summary}
      <RecEvidenceSummary text={rec.evidence_summary} />
    {/if}
    <p class="meta">Stats: {rec.evidence}{pValueSuffix}{sampleSuffix}</p>
  </details>
</div>
