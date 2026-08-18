<script lang="ts">
  // Shared button chrome. Replaces the hand-rolled `.btn-primary` / `.btn-ghost` / `.btn-text`
  // trio duplicated across Landing.svelte and PlayerHub.svelte (RFC-001 step 19).
  export let variant: 'filled' | 'outlined' | 'bare' = 'filled';
  export let size: 'sm' | 'md' = 'md';
  export let type: 'button' | 'submit' | 'reset' = 'button';
  export let disabled: boolean = false;
  export let id: string = '';
  export let hidden: boolean = false;
  export let ariaLabel: string = '';

  let className = '';
  export { className as class };
</script>

<button
  {id}
  {type}
  {disabled}
  {hidden}
  aria-label={ariaLabel || undefined}
  class="ctl ctl--{variant} ctl--{size} {className}"
  on:click
>
  <slot />
</button>

<style>
  .ctl {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    border-radius: 8px;
    font: 600 14px/1.2 var(--font);
    cursor: pointer;
    transition: background .15s, border-color .15s, color .15s, filter .15s, box-shadow .15s;
  }
  .ctl:disabled { opacity: .45; cursor: not-allowed; }
  .ctl:focus-visible {
    outline: none;
    box-shadow: var(--focus-ring);
  }

  .ctl--filled {
    background: var(--accent);
    color: #fff;
    border: 1px solid var(--accent);
    font-weight: 700;
    box-shadow: 0 4px 16px rgba(124, 108, 240, .28);
  }
  .ctl--filled.ctl--md { padding: 12px 22px; font-size: 15px; }
  .ctl--filled.ctl--sm { padding: 9px 14px; font-size: 13px; }
  .ctl--filled:hover:not(:disabled) { filter: brightness(1.08); }

  .ctl--outlined {
    background: var(--panel-2);
    color: var(--muted);
    border: 1px solid var(--border);
  }
  .ctl--outlined.ctl--md { padding: 11px 18px; font-size: 14px; }
  .ctl--outlined.ctl--sm { padding: 8px 12px; font-size: 13px; }
  .ctl--outlined:hover:not(:disabled) { color: var(--accent); border-color: var(--accent); filter: none; }

  .ctl--bare {
    background: transparent;
    color: var(--muted);
    border: 1px solid transparent;
  }
  .ctl--bare.ctl--md { padding: 9px 12px; font-weight: 600; }
  .ctl--bare:hover:not(:disabled) { color: var(--accent); background: rgba(124, 108, 240, .08); filter: none; }
  .ctl--bare.ctl--sm {
    border: none;
    background: none;
    padding: 8px 4px;
    font: 600 13px/1.2 var(--font);
  }
  .ctl--bare.ctl--sm:hover:not(:disabled) { background: none; text-decoration: underline; }
</style>
