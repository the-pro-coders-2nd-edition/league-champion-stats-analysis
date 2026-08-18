<script>
  import { fetchBuild } from '../lib/api.js';
  import Overview from '../sections/Overview.svelte';
  import Coaching from '../sections/Coaching.svelte';
  import FormTracker from '../sections/FormTracker.svelte';
  import RankPeers from '../sections/RankPeers.svelte';

  export let params = {};

  let payload = null;
  let error = null;

  $: fetchBuild(params.slug, params.buildSlug)
    .then((result) => { payload = result; })
    .catch((err) => { error = err; });
</script>

{#if error}
  <p class="report-error">Failed to load this report.</p>
{:else if payload === null}
  <p class="report-loading">Loading…</p>
{:else}
  <Overview data={payload} />
  <Coaching data={payload} />
  <FormTracker data={payload} />
  <RankPeers data={payload} />
{/if}
