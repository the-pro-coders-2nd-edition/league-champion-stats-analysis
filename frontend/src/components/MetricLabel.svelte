<script>
  // Plain JS, not lang="ts": resolveMetricIcon lives in lib/html.js, which has no type
  // declarations (checkJs is off — see RFC-001 open question #9), and svelte-check flags
  // untyped-module imports as errors under lang="ts".
  import { resolveMetricIcon } from '../lib/html.js';

  export let label;
  export let iconHref = '';
  export let iconify = '';
  // Not part of RFC-001 step 15's prop list (label/iconHref/iconify), but the role icon is
  // still real behaviour (MetricCard's player-role glyph) that this rename must not drop.
  export let roleIconHref = '';

  $: icon = resolveMetricIcon(iconHref, iconify);
</script>

<span class="metric-label">
  {#if roleIconHref}
    <img src={roleIconHref} alt="" title="" class="role-icon role-icon--sm">
  {/if}
  {#if icon?.kind === 'img'}
    <img src={icon.src} alt="" class={icon.className} aria-hidden="true">
  {:else if icon?.kind === 'iconify'}
    <iconify-icon icon={icon.icon} class={icon.className} aria-hidden="true"></iconify-icon>
  {/if}
  <span>{label}</span>
</span>

<style>
  /* :global -- .metric-label is also written directly (not through this component) by
     DataTable.svelte and MetricDeltaTable.svelte's slotted cell markup; scoping here would
     leave those call sites unstyled. */
  :global(.metric-label) { display: inline-flex; align-items: center; gap: 6px; }
  :global(.metric-card-label .metric-label) { min-width: 0; }
</style>
