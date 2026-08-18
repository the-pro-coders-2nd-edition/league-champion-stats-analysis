<script lang="ts">
  import { getContext } from 'svelte';
  import { REPORT_NAV_KEY, handleNavClick } from '../lib/reportNav.js';

  type Variant = 'row' | 'card';

  export let anchor: string;
  export let label: string;
  export let kicker = '';
  export let index: number | null = null;
  export let variant: Variant = 'row';

  const reportNav = getContext<{ scrollToSection: (id: string) => void } | undefined>(REPORT_NAV_KEY);
  const onClick = handleNavClick(reportNav, anchor);
</script>

{#if variant === 'card'}
  <a class="nav-link nav-link--card" href="#{anchor}" on:click={onClick}>
    {#if kicker}<span class="nav-link-kicker">{kicker}</span>{/if}
    <span class="nav-link-title">{label}</span>
  </a>
{:else}
  <a class="nav-link nav-link--row" href="#{anchor}" on:click={onClick}>
    {#if index != null}<span class="nav-link-index">{index}</span>{/if}
    <span>{label}</span>
  </a>
{/if}

<style>
  .nav-link {
    display: flex; align-items: flex-start; gap: 10px; padding: 11px 12px;
    background: var(--color-surface-2); border: 1px solid var(--color-divider); border-radius: 10px;
    color: var(--color-text); text-decoration: none; font-size: 13px; line-height: 1.4;
    transition: border-color .15s, transform .15s, background .15s;
  }
  .nav-link:hover { border-color: var(--color-accent); background: rgba(65, 183, 140, 0.08); transform: translateX(2px); }
  .nav-link-index {
    flex-shrink: 0; width: 22px; height: 22px; border-radius: 50%;
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 11px; font-weight: 700; background: var(--color-surface); color: var(--color-neutral-400);
  }
  .nav-link--card {
    display: grid; align-content: center; gap: var(--space-1);
    max-width: 220px; padding: var(--space-4);
  }
  .nav-link-kicker {
    font-size: 10px; letter-spacing: .1em; text-transform: uppercase;
    color: var(--color-accent);
  }
  .nav-link-title { font-size: 12px; line-height: 1.4; color: var(--color-text); }
</style>
