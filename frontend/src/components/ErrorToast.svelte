<script>
  import { errorToasts, dismissErrorToast } from '../lib/errorToast.js';
</script>

{#if $errorToasts.length > 0}
  <div class="error-toast-stack">
    {#each $errorToasts as toast (toast.id)}
      <div class="error-toast" role="alert">
        <button
          type="button"
          class="error-toast-close"
          aria-label="Dismiss"
          on:click={() => dismissErrorToast(toast.id)}
        >×</button>
        <span class="error-toast-message">{toast.message}</span>
      </div>
    {/each}
  </div>
{/if}

<style>
  /* Top-right, deliberately separate from WelcomeBackToast (bottom-right,
     above the chatbot toggle) so a celebratory toast and an error toast can
     never overlap if both fire around the same time. */
  .error-toast-stack {
    position: fixed;
    top: var(--space-4);
    right: var(--space-4);
    z-index: 80;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    max-width: 320px;
  }

  .error-toast {
    position: relative;
    display: flex;
    align-items: center;
    padding: var(--space-3) var(--space-6) var(--space-3) var(--space-4);
    border-radius: var(--radius-md);
    border: 1px solid var(--color-divider);
    border-left: 3px solid var(--tone-bad-line);
    background: var(--color-surface-2);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.24);
    opacity: 0;
    transform: translateY(-12px);
    animation: errorToastIn 0.25s ease forwards;
  }

  .error-toast-message {
    font-size: 13px;
    color: var(--color-neutral-200);
  }

  .error-toast-close {
    position: absolute;
    top: var(--space-2);
    right: var(--space-2);
    width: 20px;
    height: 20px;
    padding: 0;
    border: none;
    background: transparent;
    color: var(--color-neutral-500);
    font-size: 16px;
    line-height: 1;
    cursor: pointer;
  }
  .error-toast-close:hover { color: var(--color-neutral-300); }

  @keyframes errorToastIn { to { opacity: 1; transform: translateY(0); } }

  @media (prefers-reduced-motion: reduce) {
    .error-toast { animation: none; opacity: 1; transform: none; }
  }
</style>
