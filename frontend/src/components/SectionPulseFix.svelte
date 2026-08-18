<script lang="ts">
  export let anchor: string;
  export let title: string;
</script>

<a class="section-pulse-fix" href="#{anchor}">
  <span class="section-pulse-fix-kicker">Try this</span>
  <span class="section-pulse-fix-title">{title}</span>
</a>

<style>
  :global(.section-pulse-fix) {
    display: grid; align-content: center; gap: var(--space-1);
    max-width: 220px; padding: var(--space-4);
    border-radius: var(--radius-sm);
    background: var(--color-neutral-900);
    color: var(--color-text); text-decoration: none;
    transition: background .15s;
  }
  :global(.section-pulse-fix:hover) { background: var(--color-surface-2); }
  :global(.section-pulse-fix-kicker) {
    font-size: 10px; letter-spacing: .1em; text-transform: uppercase;
    color: var(--color-neutral-500);
  }
  :global(.section-pulse-fix-title) { font-size: 12px; line-height: 1.4; color: var(--color-text); }
</style>
