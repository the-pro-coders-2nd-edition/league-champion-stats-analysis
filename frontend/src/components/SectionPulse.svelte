<script>
  import Meter from './Meter.svelte';
  import SectionPulseHint from './SectionPulseHint.svelte';
  import SectionPulseFix from './SectionPulseFix.svelte';

  export let sectionId;
  export let verdict = undefined;

  // v.tone is "strong" | "solid" | "focus" (bundles.py:_score_verdict_sentence), not a Meter tone.
  const METER_TONE = { strong: 'good', focus: 'bad' };

  $: v = verdict || {};
  $: toneClass = v.tone ? ` section-pulse--${v.tone}` : '';
  $: score = Math.max(0, Math.min(100, Math.round(Number(v.score) || 0)));
  $: meterTone = METER_TONE[v.tone] || 'flat';
</script>

<div class="section-pulse{toneClass}" data-section-verdict={sectionId} hidden={!v.text}>
  <div class="section-pulse-callout">
    <span class="section-pulse-label">{v.label || 'Score'}</span>
    <div class="section-pulse-head">
      <strong class="section-pulse-title">{v.text || ''}</strong>
      <span class="section-pulse-score">{score}</span>
    </div>
    <Meter value={score} tone={meterTone} size="md" reference={50} --meter-margin="var(--space-3) 0 var(--space-2)" />
    {#if v.hint || v.value}
      <SectionPulseHint text={v.hint || v.value} />
    {/if}
  </div>
  {#if v.fix && v.fix.title}
    <SectionPulseFix anchor={v.fix.anchor || 'coaching'} title={v.fix.title} />
  {/if}
</div>

<style>
  .section-pulse-label {
    display: inline-flex; align-items: center; align-self: flex-start;
    width: fit-content; height: 19px; padding: 0 7px;
    border-radius: var(--radius-sm);
    font-size: 11px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase;
    background: var(--tone-flat-soft); color: var(--tone-flat-fg);
  }
  :global(.section-pulse--strong) .section-pulse-label {
    background: var(--tone-good-soft); color: var(--tone-good-fg);
  }
  :global(.section-pulse--focus) .section-pulse-label {
    background: var(--tone-bad-soft); color: var(--tone-bad-fg);
  }
  .section-pulse-head {
    display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-3);
    margin-top: var(--space-3);
  }
  .section-pulse-title {
    font-family: var(--font-heading); font-size: 17px; font-weight: 700; line-height: 1.3; min-width: 0;
  }
  .section-pulse-score {
    flex-shrink: 0; font-family: var(--font-heading); font-size: 23px; font-weight: 700;
    letter-spacing: -.02em; font-variant-numeric: tabular-nums; color: var(--color-text);
  }
  :global(.section-pulse--strong) .section-pulse-score { color: var(--tone-good-fg); }
  :global(.section-pulse--focus) .section-pulse-score { color: var(--tone-bad-fg); }
</style>
