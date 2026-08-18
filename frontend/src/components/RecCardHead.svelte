<script lang="ts">
  type Tone = 'good' | 'warn' | 'bad' | 'flat';

  export let priorityTone: Tone;
  export let priorityLabel: string;
  export let category: string;
  export let confidence: string;
  export let title: string;
  export let detail: string;
</script>

<div class="rec-head">
  <span class="pill pill--{priorityTone} pill--soft"><i class="pill__dot"></i><span>{priorityLabel}</span></span>
  <span class="pill pill--flat pill--soft"><span>{category}</span></span>
  <span class="rec-confidence">{confidence}</span>
</div>
<strong class="rec-title">{title}</strong>
<p class="rec-body">{detail}</p>

<style>
  /* :global -- renderRec() in report.html rebuilds this markup on queue/window
     filter changes, and .pill/.pill__dot come from Pill.svelte's own global
     styles, so nothing here can be scoped/hashed either. */
  :global(.rec-head) {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
    margin-bottom: var(--space-2);
  }
  :global(.rec-confidence) {
    margin-left: auto;
    font-size: 11px;
    color: var(--color-neutral-600);
    font-variant-numeric: tabular-nums;
  }
  :global(.rec-title) {
    display: block;
    font-family: var(--font-heading);
    font-weight: 700;
    font-size: 14px;
  }
  :global(.rec-body) {
    margin: var(--space-2) 0 0;
    color: var(--color-neutral-400);
    font-size: 12px;
    line-height: 1.55;
  }
</style>
