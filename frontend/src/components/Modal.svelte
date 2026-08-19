<script>
  import { fade, fly } from 'svelte/transition';

  export let open = false;
  export let title = '';
  export let onClose = () => {};
  // 'large' fills most of the viewport, for content-heavy modals (e.g. the
  // new-games recap) instead of the default small centered dialog.
  export let size = 'default';

  function handleWindowKeydown(event) {
    if (open && event.key === 'Escape') onClose();
  }

  function handleBackdropClick(event) {
    if (event.target === event.currentTarget) onClose();
  }
</script>

<svelte:window on:keydown={handleWindowKeydown} />

{#if open}
  <!-- The backdrop is a decorative click-outside-to-dismiss area; the actual
       dialog semantics live on .modal-panel below. Escape already closes it via
       svelte:window, so this needs no keyboard handler of its own. -->
  <!-- svelte-ignore a11y-click-events-have-key-events -->
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div
    class="modal-backdrop"
    on:click={handleBackdropClick}
    transition:fade={{ duration: 150 }}
  >
    <div
      class="modal-panel{size === 'large' ? ' modal-panel--large' : ''}"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      transition:fly={{ y: 12, duration: 180 }}
    >
      <div class="modal-head">
        <h3 class="modal-title">{title}</h3>
        <button type="button" class="modal-close" aria-label="Close" on:click={onClose}>
          <iconify-icon icon="lucide:x" aria-hidden="true"></iconify-icon>
        </button>
      </div>
      <div class="modal-body">
        <slot />
      </div>
    </div>
  </div>
{/if}

<style>
  .modal-backdrop {
    position: fixed;
    inset: 0;
    z-index: 50;
    display: grid;
    place-items: center;
    padding: var(--space-4);
    background: rgba(0, 0, 0, 0.55);
  }
  .modal-panel {
    display: grid;
    grid-template-rows: auto 1fr;
    width: min(680px, 100%);
    max-height: min(80vh, 720px);
    border: 1px solid var(--color-divider);
    border-radius: var(--radius-lg);
    background: var(--color-surface);
    box-shadow: var(--shadow-lg);
  }
  .modal-panel--large {
    width: 75vw;
    max-height: 85vh;
  }
  .modal-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    padding: var(--space-4) var(--space-4) var(--space-3);
    border-bottom: 1px solid var(--color-divider);
  }
  .modal-title {
    margin: 0;
    font-size: 15px;
    font-weight: 700;
    color: var(--color-text);
  }
  .modal-close {
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    border: 1px solid var(--color-divider);
    border-radius: 999px;
    background: var(--color-surface-2);
    color: var(--color-neutral-400);
    font-size: 14px;
    cursor: pointer;
  }
  /* iconify-icon renders an inline SVG with its own baseline offset, which
     visibly de-centers it inside a circular button even under place-items:
     center. block display removes that baseline, same fix as
     .report-refresh-btn and .chatbot-toggle. */
  .modal-close iconify-icon {
    display: block;
  }
  .modal-close:hover {
    color: var(--color-text);
    border-color: var(--color-accent);
  }
  .modal-body {
    overflow-y: auto;
    padding: var(--space-4);
  }
</style>
