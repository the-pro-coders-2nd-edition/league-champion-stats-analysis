<script lang="ts">
  import { getContext } from 'svelte';
  import { REPORT_NAV_KEY } from '../lib/reportNav.js';

  export let anchor: string;
  export let title: string;

  const reportNav = getContext<{ scrollToSection: (id: string) => void } | undefined>(REPORT_NAV_KEY);

  function onClick(event: MouseEvent) {
    event.preventDefault();
    if (reportNav) {
      reportNav.scrollToSection(anchor);
      return;
    }
    document.getElementById(anchor)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
</script>

<a class="section-pulse-fix" href="#{anchor}" on:click={onClick}>
  <span class="section-pulse-fix-kicker">Try this</span>
  <span class="section-pulse-fix-title">{title}</span>
</a>

<style>
  :global(.section-pulse-fix) {
    display: grid; align-content: center; gap: var(--space-1);
    max-width: 220px; padding: var(--space-4);
    border: 1px solid var(--color-divider); border-radius: 10px;
    background: var(--color-surface-2);
    color: var(--color-text); text-decoration: none;
    transition: border-color .15s, transform .15s, background .15s;
  }
  :global(.section-pulse-fix:hover) {
    border-color: var(--color-accent);
    background: rgba(65, 183, 140, 0.08);
    transform: translateX(2px);
  }
  :global(.section-pulse-fix-kicker) {
    font-size: 10px; letter-spacing: .1em; text-transform: uppercase;
    color: var(--color-accent);
  }
  :global(.section-pulse-fix-title) { font-size: 12px; line-height: 1.4; color: var(--color-text); }
</style>
