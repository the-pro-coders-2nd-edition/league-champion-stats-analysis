<script lang="ts">
  // Puts its trigger AFTER the revealed region -- <details> cannot do this, and TieredCards,
  // Coaching and GameReview's "show N more" lists all need it. Absorbs RecExtendButton.
  export let open: boolean = false;
  export let label: string;
  export let openLabel: string = label;
  export let indicator: 'chevron' | 'icon' | 'none' = 'none';
  export let triggerClass: string = '';
  export let id: string = '';
  export let style: string = '';

  function toggle() {
    open = !open;
  }
</script>

<div class="show-more" class:is-open={open} {style}>
  <div class="show-more-content" id={id || undefined}>
    <slot />
  </div>
  <button
    type="button"
    class="show-more-trigger {triggerClass}"
    class:is-expanded={open}
    aria-expanded={open}
    aria-controls={id || undefined}
    on:click={toggle}
  >
    {#if indicator === 'icon'}
      <iconify-icon icon="lucide:chevron-{open ? 'up' : 'down'}" aria-hidden="true"></iconify-icon>
    {:else if indicator === 'chevron'}
      <span class="show-more-chevron" aria-hidden="true"></span>
    {/if}
    <span>{open ? openLabel : label}</span>
  </button>
</div>

<style>
  .show-more-content { display: none; }
  .show-more.is-open .show-more-content {
    display: var(--show-more-content-display, block);
    flex-direction: var(--show-more-content-direction, column);
    flex-wrap: var(--show-more-content-wrap, nowrap);
    gap: var(--show-more-content-gap, 8px);
    margin-top: var(--show-more-content-margin-top, 0);
  }

  .show-more-trigger { font: inherit; }
  .show-more-trigger:focus-visible {
    outline: none;
    box-shadow: var(--focus-ring);
    border-radius: 4px;
  }

  /* rec-extend chrome (Coaching) */
  .show-more-trigger.rec-extend {
    display: block;
    margin: 16px auto 0;
    padding: 10px 20px;
    border: 1px solid var(--color-divider);
    border-radius: 8px;
    background: var(--color-surface-2);
    color: var(--color-text);
    cursor: pointer;
  }
  .show-more-trigger.rec-extend:hover { border-color: var(--color-accent); color: var(--color-accent); }
  .show-more-trigger.rec-extend.is-expanded { margin-top: 8px; }

  /* game-review-more-toggle chrome */
  .show-more-trigger.game-review-more-toggle {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    width: 100%;
    margin: 0;
    padding: 8px 10px;
    border: 1px dashed var(--color-divider);
    border-radius: 10px;
    background: transparent;
    color: var(--color-neutral-400);
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: border-color 0.15s, color 0.15s, background 0.15s;
  }
  .show-more-trigger.game-review-more-toggle:hover {
    border-color: var(--color-accent);
    color: var(--color-accent);
    background: var(--color-surface-2);
  }
  .show-more-trigger.game-review-more-toggle iconify-icon { font-size: 16px; }

  /* cards-more-trigger chrome (TieredCards) */
  .show-more-trigger.cards-more-trigger {
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    width: fit-content;
    padding: 0;
    border: 0;
    background: none;
    font-size: 12px;
    font-weight: 400;
    color: var(--color-neutral-400);
    transition: color 0.15s;
  }
  .show-more-trigger.cards-more-trigger:hover { color: var(--color-text); }
  .show-more-trigger.cards-more-trigger .show-more-chevron {
    font-size: 10px;
    color: var(--color-neutral-600);
    transition: transform 0.18s;
  }
  .show-more-trigger.cards-more-trigger .show-more-chevron::before { content: "▸"; }
  .show-more-trigger.cards-more-trigger.is-expanded .show-more-chevron { transform: rotate(90deg); }
</style>
