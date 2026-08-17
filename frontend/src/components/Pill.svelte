<script>
  const TONES = ['good', 'warn', 'bad', 'flat', 'accent'];
  const VARIANTS = ['soft', 'outline'];
  const EXTRA_CLASSES = ['', 'scope-chip--window'];

  export let tone = 'flat';
  export let label;
  export let dot = true;
  export let variant = 'soft';
  export let extraClass = '';

  if (!TONES.includes(tone)) throw new Error(`Pill: invalid tone "${tone}"`);
  if (!VARIANTS.includes(variant)) throw new Error(`Pill: invalid variant "${variant}"`);
  if (!EXTRA_CLASSES.includes(extraClass)) throw new Error(`Pill: invalid extraClass "${extraClass}"`);
</script>

<span class="pill pill--{tone} pill--{variant} {extraClass}">
  {#if dot}<i class="pill__dot"></i>{/if}
  {label}
</span>

<style>
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    line-height: 1.4;
    white-space: nowrap;
  }
  .pill__dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
    flex-shrink: 0;
  }

  .pill--good { color: var(--tone-good-fg); }
  .pill--warn { color: var(--tone-warn-fg); }
  .pill--bad { color: var(--tone-bad-fg); }
  .pill--flat { color: var(--tone-flat-fg); }
  .pill--accent { color: var(--tone-accent-fg); }

  .pill--soft.pill--good { background: var(--tone-good-soft); }
  .pill--soft.pill--warn { background: var(--tone-warn-soft); }
  .pill--soft.pill--bad { background: var(--tone-bad-soft); }
  .pill--soft.pill--flat { background: var(--tone-flat-soft); }
  .pill--soft.pill--accent { background: var(--tone-accent-soft); }

  .pill--outline {
    background: transparent;
  }
  .pill--outline.pill--good { border: 1px solid var(--tone-good-line); }
  .pill--outline.pill--warn { border: 1px solid var(--tone-warn-line); }
  .pill--outline.pill--bad { border: 1px solid var(--tone-bad-line); }
  .pill--outline.pill--flat { border: 1px solid var(--tone-flat-line); }
  .pill--outline.pill--accent { border: 1px solid var(--tone-accent-line); }
</style>
