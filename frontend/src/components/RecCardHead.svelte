<script lang="ts">
  import Chip from './Chip.svelte';

  type Badge = 'high' | 'medium' | 'low';

  export let badge: Badge;
  export let priorityLabel: string;
  export let category: string;
  export let title: string;
  export let detail: string;

  const PRIORITY_TONE = { high: 'bad', medium: 'warn', low: 'flat' };
</script>

<div class="rec-head">
  <Chip tone={PRIORITY_TONE[badge]} fill={true} label={priorityLabel} />
  <Chip tone="flat" fill={true} label={category} />
</div>
<strong class="rec-title">{title}</strong>
<p class="rec-body">{detail}</p>

<style>
  .rec-head {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
    margin-bottom: var(--space-2);
  }
  .rec-title {
    display: block;
    font-family: var(--font-heading);
    font-weight: 700;
    font-size: 14px;
  }
  .rec-body {
    margin: var(--space-2) 0 0;
    color: var(--color-neutral-400);
    font-size: 12px;
    line-height: 1.55;
  }
</style>
