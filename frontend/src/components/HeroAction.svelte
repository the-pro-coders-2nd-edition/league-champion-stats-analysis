<script lang="ts">
  export let anchor: string;
  export let index: number;
  export let label: string;
</script>

<a class="hero-action" href="#{anchor}">
  <span class="hero-action-index">{index}</span>
  <span>{label}</span>
</a>

<style>
  :global(.hero-action) {
    display: flex; align-items: flex-start; gap: 10px; padding: 11px 12px;
    background: var(--color-surface-2); border: 1px solid var(--color-divider); border-radius: 10px;
    color: var(--color-text); text-decoration: none; font-size: 13px; line-height: 1.4;
    transition: border-color .15s, transform .15s, background .15s;
  }
  :global(.hero-action:hover) { border-color: var(--color-accent); background: rgba(124, 108, 240, 0.08); transform: translateX(2px); }
  :global(.hero-action-index) {
    flex-shrink: 0; width: 22px; height: 22px; border-radius: 50%;
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 11px; font-weight: 700; background: var(--color-surface); color: var(--color-neutral-400);
  }
  :global(.hero-action-empty) { color: var(--color-neutral-400); font-size: 13px; margin: 0; }
</style>
