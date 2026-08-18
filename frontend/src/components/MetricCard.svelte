<script lang="ts">
  import MetricLabelSpan from './MetricLabelSpan.svelte';
  import MetricTooltip from './MetricTooltip.svelte';
  import MetricValue from './MetricValue.svelte';
  import MetricBenchmark from './MetricBenchmark.svelte';

  export let card;

  function metricIconHtml(card) {
    if (card.icon_href) {
      return `<img src="${card.icon_href}" alt="" class="metric-icon metric-icon--asset" aria-hidden="true">`;
    }
    if (card.iconify) {
      return `<iconify-icon icon="${card.iconify}" class="metric-icon" aria-hidden="true"></iconify-icon>`;
    }
    return '';
  }

  function roleIconHtml(card) {
    if (!card.role_icon_href) return '';
    return `<img src="${card.role_icon_href}" alt="" title="" class="role-icon role-icon--sm">`;
  }
</script>

<div class="card{card.tier === 'headline' ? ' card--headline' : ''}">
  <div class="label">
    <div class="metric-card-label">
      <MetricLabelSpan roleIconHtml={roleIconHtml(card)} iconHtml={metricIconHtml(card)} label={card.label} />
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
