<script lang="ts">
  type Tone = 'good' | 'bad' | 'flat';
  type Line = { kicker: string; value: string };

  export let tone: Tone = 'flat';
  export let label = '';
  export let title = '';
  export let body = '';
  export let lines: Line[] = [];
  export let edge = false;
</script>

<div class="callout callout--{tone}" class:callout--edge={edge}>
  {#if label}<span class="callout-label">{label}</span>{/if}
  {#if title}<strong class="callout-title">{title}</strong>{/if}
  {#if body}<p class="callout-body">{body}</p>{/if}
  {#if lines.length}
    <div class="callout-lines">
      {#each lines as line}
        <p class="callout-line"><span class="callout-kicker">{line.kicker}</span> {line.value}</p>
      {/each}
    </div>
  {/if}
</div>

<style>
  .callout {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 12px 14px;
    border-radius: var(--radius-md);
    border: 1px solid var(--color-divider);
    background: var(--color-surface);
  }
  .callout--edge {
    border-left-width: 3px;
  }
  .callout--good.callout--edge { border-left-color: var(--tone-good-line); }
  .callout--bad.callout--edge { border-left-color: var(--tone-bad-line); }
  .callout--flat.callout--edge { border-left-color: var(--tone-flat-line); }
  .callout-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--color-neutral-400);
  }
  .callout--good .callout-label { color: var(--tone-good-fg); }
  .callout--bad .callout-label { color: var(--tone-bad-fg); }
  .callout-title {
    font-size: 14px;
    font-weight: 700;
    line-height: 1.3;
  }
  .callout-body {
    margin: 0;
    font-size: 12px;
    color: var(--color-neutral-400);
    line-height: 1.4;
  }
  .callout-lines {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .callout-line {
    margin: 0;
    font-size: 12px;
    line-height: 1.45;
    color: var(--color-neutral-400);
  }
  /* Last line is always the "action" row in the family's one real caller (FormTracker). */
  .callout-line:last-child {
    color: var(--color-text);
  }
  .callout-kicker {
    display: inline-block;
    min-width: 3.6em;
    margin-right: 6px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--color-neutral-400);
  }
  .callout-line:last-child .callout-kicker {
    color: var(--color-accent);
  }
</style>
