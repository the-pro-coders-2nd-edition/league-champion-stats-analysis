<script>
  // RFC-003: a loose approximation of each report tab's layout, shown while the report
  // payload for the active tab's data is still in flight (first navigation into a report,
  // or switching to a different build/champion). Every tab's data comes from one
  // fetchBuild() call, so only the currently active category ever needs a skeleton.
  import Skeleton from './Skeleton.svelte';

  export let category = 'summary';
</script>

<div class="report-skeleton" aria-hidden="true" aria-busy="true">
  {#if category === 'summary'}
    <div class="rs-row">
      <Skeleton height="180px" radius="18px" />
      <div class="rs-col rs-col--narrow">
        <Skeleton height="86px" radius="18px" />
        <Skeleton height="86px" radius="18px" />
      </div>
    </div>
    <div class="rs-grid">
      {#each Array(6) as _}
        <Skeleton height="54px" radius="12px" />
      {/each}
    </div>
  {:else if category === 'games'}
    <div class="rs-row">
      <div class="rs-col rs-col--rail">
        {#each Array(6) as _}
          <Skeleton height="52px" radius="10px" />
        {/each}
      </div>
      <Skeleton height="420px" radius="18px" />
    </div>
  {:else if category === 'career'}
    <Skeleton height="28px" width="220px" radius="6px" />
    <div class="rs-row rs-row--top">
      <div class="rs-col">
        {#each Array(3) as _}
          <div class="rs-goal">
            <Skeleton height="27px" width="27px" circle={true} />
            <Skeleton height="46px" radius="10px" />
          </div>
        {/each}
      </div>
      <div class="rs-col">
        {#each Array(3) as _}
          <div class="rs-goal">
            <Skeleton height="27px" width="27px" circle={true} />
            <Skeleton height="46px" radius="10px" />
          </div>
        {/each}
      </div>
    </div>
  {:else if category === 'performance'}
    <Skeleton height="140px" radius="18px" />
    <div class="rs-col" style="margin-top: 16px;">
      {#each Array(4) as _}
        <Skeleton height="48px" radius="10px" />
      {/each}
    </div>
  {:else if category === 'champion'}
    <Skeleton height="220px" radius="14px" />
    <div class="rs-row rs-row--top">
      <Skeleton height="160px" radius="14px" />
      <Skeleton height="160px" radius="14px" />
    </div>
  {:else}
    {#each Array(3) as _}
      <div class="rs-section">
        <Skeleton height="20px" width="160px" radius="6px" />
        <div class="rs-grid rs-grid--cards">
          <Skeleton height="70px" radius="12px" />
          <Skeleton height="70px" radius="12px" />
          <Skeleton height="70px" radius="12px" />
        </div>
        <Skeleton height="200px" radius="14px" />
      </div>
    {/each}
  {/if}
</div>

<style>
  .report-skeleton { display: flex; flex-direction: column; gap: var(--space-6); }
  .rs-row { display: flex; gap: var(--space-4); align-items: stretch; }
  /* :global -- .skeleton is Skeleton.svelte's own scoped root element; styling it from
     here requires reaching past that scoping since Svelte doesn't let a parent's plain
     selectors match into a child component. */
  .rs-row > :global(.skeleton) { flex: 1; }
  .rs-row--top { align-items: flex-start; }
  .rs-col { display: flex; flex-direction: column; gap: var(--space-3); flex: 1; min-width: 0; }
  .rs-col--narrow { flex: 0 0 220px; }
  .rs-col--rail { flex: 0 0 260px; }
  .rs-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-3); }
  .rs-grid--cards { grid-template-columns: repeat(3, minmax(0, 1fr)); margin: var(--space-3) 0; }
  .rs-goal { display: flex; align-items: center; gap: var(--space-3); }
  /* :global -- same reason as above: reaching into Skeleton.svelte's scoped root. */
  .rs-goal > :global(.skeleton:last-child) { flex: 1; }
  .rs-section { display: flex; flex-direction: column; gap: var(--space-3); }

  @media (max-width: 860px) {
    .rs-row { flex-direction: column; }
    .rs-col--narrow, .rs-col--rail { flex: 1; }
    .rs-grid { grid-template-columns: 1fr; }
    .rs-grid--cards { grid-template-columns: 1fr; }
  }
</style>
