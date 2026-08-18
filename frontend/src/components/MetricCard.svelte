<script>
  // Plain JS, not lang="ts": MetricLabel.svelte is untyped (it imports lib/html.js, which has no
  // declarations — see MetricLabel.svelte's own comment), and svelte-check flags an untyped child
  // component import as an error under lang="ts".
  import MetricLabel from './MetricLabel.svelte';
  import MetricTooltip from './MetricTooltip.svelte';
  import MetricValue from './MetricValue.svelte';
  import MetricBenchmark from './MetricBenchmark.svelte';

  export let card;
</script>

<div class="card{card.tier === 'headline' ? ' card--headline' : ''}">
  <div class="label">
    <div class="metric-card-label">
      <MetricLabel
        label={card.label}
        iconHref={card.icon_href || ''}
        iconify={card.iconify || ''}
        tone={card.icon_tone || 'muted'}
        roleIconHref={card.role_icon_href || ''}
      />
      {#if card.tooltip}
        <MetricTooltip label={card.label} tooltip={card.tooltip} />
      {/if}
    </div>
  </div>
  <MetricValue value={card.value} valueClass={card.value_class || ''} valueColor={card.value_color || ''} />
  {#if card.benchmark}
    <MetricBenchmark benchmark={card.benchmark} benchmarkTone={card.benchmark_tone || 'inline'} benchmarkColor={card.benchmark_color || ''} />
  {/if}
</div>
