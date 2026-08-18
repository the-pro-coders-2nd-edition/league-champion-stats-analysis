<script lang="ts">
  import SectionPulseBody from './SectionPulseBody.svelte';
  import SectionPulseHint from './SectionPulseHint.svelte';
  import SectionPulseFix from './SectionPulseFix.svelte';

  export let sectionId: string;
  export let verdict: Record<string, any> | null | undefined = undefined;

  $: v = verdict || {};
  $: toneClass = v.tone ? ` section-pulse--${v.tone}` : '';
</script>

<div class="section-pulse{toneClass}" data-section-verdict={sectionId} hidden={!v.text}>
  <div class="section-pulse-callout">
    <SectionPulseBody label={v.label || 'Score'} title={v.text || ''} score={v.score} />
    {#if v.hint || v.value}
      <SectionPulseHint text={v.hint || v.value} />
    {/if}
  </div>
  {#if v.fix && v.fix.title}
    <SectionPulseFix anchor={v.fix.anchor || 'coaching'} title={v.fix.title} />
  {/if}
</div>
