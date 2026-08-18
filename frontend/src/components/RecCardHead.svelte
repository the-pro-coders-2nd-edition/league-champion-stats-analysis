<script lang="ts">
  import Pill from './Pill.svelte';

  type Badge = 'high' | 'medium' | 'low';

  export let badge: Badge;
  export let priorityLabel: string;
  export let category: string;
  export let title: string;
  export let detail: string;

  const PRIORITY_TONE = { high: 'bad', medium: 'warn', low: 'flat' };
</script>

<div class="rec-head">
  <Pill tone={PRIORITY_TONE[badge]} variant="soft" dot={false} label={priorityLabel} />
  <span class="badge">{category}</span>
  <strong>{title}</strong>
</div>
<p>{detail}</p>

<style>
  :global(.rec-head) { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 8px; }
  :global(.rec-head strong) { flex: 1 1 100%; font-size: 15px; }
</style>
