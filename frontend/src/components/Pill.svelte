<script lang="ts">
  type Tone = 'good' | 'warn' | 'bad' | 'flat' | 'accent';
  type Variant = 'soft' | 'outline';

  export let tone: Tone = 'flat';
  export let label: string;
  export let dot: boolean = true;
  export let variant: Variant = 'soft';
  export let extraClass: string = '';
</script>

<span class="pill pill--{tone} pill--{variant}{extraClass ? ' ' + extraClass : ''}">
  {#if dot}<i class="pill__dot"></i>{/if}<span>{label}</span>
</span>

<style>
  /* :global -- client-side re-renders (queue/window filter changes) rebuild
     Coaching cards and account rows with plain markup, not <Pill>, so these
     classes must resolve without Svelte's per-component scoping hash. */
  :global(.pill) {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    height: 19px;
    padding: 0 7px;
    border-radius: calc(var(--radius-md) * 0.75);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.02em;
    white-space: nowrap;
  }
  :global(.pill__dot) {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  :global(.pill--good) { color: var(--tone-good-fg); }
  :global(.pill--warn) { color: var(--tone-warn-fg); }
  :global(.pill--bad) { color: var(--tone-bad-fg); }
  :global(.pill--flat) { color: var(--tone-flat-fg); }
  :global(.pill--accent) { color: var(--tone-accent-fg); }

  :global(.pill--good .pill__dot) { background: var(--tone-good-line); }
  :global(.pill--warn .pill__dot) { background: var(--tone-warn-line); }
  :global(.pill--bad .pill__dot) { background: var(--tone-bad-line); }
  :global(.pill--flat .pill__dot) { background: var(--tone-flat-line); }
  :global(.pill--accent .pill__dot) { background: var(--tone-accent-line); }

  :global(.pill--soft.pill--good) { background: var(--tone-good-soft); }
  :global(.pill--soft.pill--warn) { background: var(--tone-warn-soft); }
  :global(.pill--soft.pill--bad) { background: var(--tone-bad-soft); }
  :global(.pill--soft.pill--flat) { background: var(--tone-flat-soft); }
  :global(.pill--soft.pill--accent) { background: var(--tone-accent-soft); }

  :global(.pill--outline) { background: transparent; }
  :global(.pill--outline.pill--good) { border: 1px solid var(--tone-good-line); }
  :global(.pill--outline.pill--warn) { border: 1px solid var(--tone-warn-line); }
  :global(.pill--outline.pill--bad) { border: 1px solid var(--tone-bad-line); }
  :global(.pill--outline.pill--flat) { border: 1px solid var(--tone-flat-line); }
  :global(.pill--outline.pill--accent) { border: 1px solid var(--tone-accent-line); }
</style>
