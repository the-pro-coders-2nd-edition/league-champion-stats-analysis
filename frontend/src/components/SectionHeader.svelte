<script>
  import { getContext } from 'svelte';
  import { writable } from 'svelte/store';
  import Chip from './Chip.svelte';
  import { categoryForSection } from '../lib/reportNav.js';
  import { WINDOW_SCOPE_KEY } from '../lib/windowScope.js';

  export let id;
  export let title;
  export let icon = '';
  export let scope = '';
  export let lead = '';

  // No Report.svelte ancestor (e.g. a standalone render) falls back to an empty label
  // instead of throwing on a missing context.
  const contextScope = getContext(WINDOW_SCOPE_KEY) || writable('');

  $: category = categoryForSection(id);
  $: resolvedScope = scope || $contextScope;
</script>

<h2 class="section-title section-title--{category}">
  {#if icon}
    <iconify-icon icon="lucide:{icon}" class="metric-icon" aria-hidden="true"></iconify-icon>
  {/if}
  <span>{title}</span>
  <Chip tone="flat" fill={false} bordered={true} label={resolvedScope} />
</h2>
{#if lead || $$slots.lead}
  <p class="sub sub--lead">{#if $$slots.lead}<slot name="lead" />{:else}{lead}{/if}</p>
{/if}
