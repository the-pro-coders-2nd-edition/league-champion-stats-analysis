<script lang="ts">
  export let id: string = '';

  let className = '';
  export { className as class };
</script>

<div class="panel {className}" {id}>
  {#if $$slots.stage}
    <div class="panel-stage">
      <div class="panel-stage-inner">
        <slot name="stage" />
      </div>
    </div>
  {/if}
  <slot />
</div>

<style>
  .panel {
    min-width: 0;
    border: 1px solid var(--color-divider);
    border-radius: var(--radius-lg);
    background: var(--color-surface);
    padding: 16px 18px 18px;
    box-shadow: var(--shadow-md);
  }
  .panel:global(.panel-form) {
    padding: 14px 16px;
    border-radius: var(--radius-md);
    box-shadow: none;
  }
  .panel:global(.panel-form--compact) {
    padding: 12px 14px;
  }
  .panel-stage { margin-bottom: 14px; }
  .panel-stage-inner {
    display: grid;
    /* Peer used minmax(160px, 240px), Form used minmax(160px, 220px) — a 20px delta not worth
       a prop. Picking 240px (peer's wider rank figure needs the room; form's shorter score
       column fits fine with the extra space too). */
    grid-template-columns: minmax(160px, 240px) minmax(0, 1fr);
    gap: 16px;
    align-items: stretch;
    padding: 14px 16px;
    border-radius: 12px;
    border: 1px solid var(--color-divider);
    background: var(--color-surface-2);
  }
  @media (max-width: 860px) {
    .panel-stage-inner { grid-template-columns: 1fr; }
  }
</style>
