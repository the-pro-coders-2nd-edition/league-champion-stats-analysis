<script lang="ts">
  type Badge = 'high' | 'medium' | 'low';

  export let badge: Badge;
  export let priorityLabel: string;
  export let category: string;
  export let title: string;
  export let detail: string;
</script>

<div class="rec-head">
  <span class="badge badge--priority badge--{badge}">{priorityLabel}</span>
  <span class="badge">{category}</span>
  <strong>{title}</strong>
</div>
<p>{detail}</p>

<style>
  :global(.rec-head) { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 8px; }
  :global(.rec-head strong) { flex: 1 1 100%; font-size: 15px; }
  :global(.badge) {
    display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 10px;
    background: var(--color-surface-2); color: var(--color-neutral-400); margin-right: 8px;
    text-transform: uppercase; letter-spacing: .05em;
  }
  :global(.badge--priority) { font-weight: 700; }
  :global(.badge--high) { color: var(--color-bad); background: rgba(224, 85, 99, 0.12); }
  :global(.badge--medium) { color: #e0b155; background: rgba(224, 177, 85, 0.12); }
  :global(.badge--low) { color: var(--color-neutral-400); }
</style>
