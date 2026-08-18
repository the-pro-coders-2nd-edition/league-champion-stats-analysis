<script>
  import { tick } from 'svelte';
  import SectionHeader from '../components/SectionHeader.svelte';
  import Panel from '../components/Panel.svelte';
  import UiChipBadge from '../components/UiChipBadge.svelte';
  import FormWrBridge from '../components/FormWrBridge.svelte';
  import SegmentedControl from '../components/SegmentedControl.svelte';
  import Callout from '../components/Callout.svelte';
  import MetricDeltaTable from '../components/MetricDeltaTable.svelte';
  import PlotlyFigure from '../lib/PlotlyFigure.svelte';
  import { resizePlotlySoon } from '../lib/plotlyResize.js';
  import TrendRow from '../components/TrendRow.svelte';
  import { metricLabelFromRow } from '../lib/html.js';

  export let data;

  let activeTab = 'pulse';

  $: formAvailable = !!data.form_available;
  $: snapshot = data.form_snapshot || {};
  $: trend = snapshot.trend || 'stable';
  $: unavailableText = data.form_insufficient_reason || 'Need more games for Form Tracker.';
  $: formScoreText = data.form_snapshot && data.form_snapshot.form_score !== undefined
    ? (data.form_snapshot.form_score >= 0 ? '+' : '') + Math.round(data.form_snapshot.form_score)
    : '0';
  $: confidence = snapshot.confidence || 'insufficient';

  $: wrFrom = Math.round((snapshot.baseline_winrate || 0) * 100) + '%';
  $: wrTo = Math.round((snapshot.recent_winrate || 0) * 100) + '%';
  $: wrDeltaPp = Number(snapshot.winrate_delta_pp || 0);
  $: wrDeltaClass = wrDeltaPp > 0 ? 'form-wr-delta--up' : (wrDeltaPp < 0 ? 'form-wr-delta--down' : '');
  $: wrDeltaText = wrDeltaPp > 0
    ? 'up ' + Math.round(wrDeltaPp)
    : (wrDeltaPp < 0 ? 'down ' + Math.round(-wrDeltaPp) : 'no change');

  $: stories = data.form_stories || [];
  $: topImproved = data.form_top_improved || [];
  $: topRegressed = data.form_top_regressed || [];
  $: deltaRows = (data.form_delta_rows || []).map((row) => ({
    label: row.label,
    icon_href: row.icon_href,
    icon_tone: row.icon_tone,
    value: row.recent,
    baseline: row.baseline,
    gap: row.gap,
    gap_color: row.gap_color,
    verdict: row.verdict,
  }));

  const formTabs = [
    { value: 'pulse', label: 'Pulse' },
    { value: 'evidence', label: 'Evidence' },
  ];

  function selectFormTab(tab) {
    activeTab = tab;
    tick().then(() => {
      const panel = document.getElementById(`form-panel-${tab}`);
      if (panel) resizePlotlySoon(panel);
    });
  }
</script>

<section id="form-tracker" class="report-section report-section--performance">
  <SectionHeader
    id="form-tracker"
    title="Form tracker"
    icon="bar-chart-2"
    scope="Recent games vs your baseline"
    lead="{data.form_sample_subtitle || 'Recent form vs your personal baseline'} — independent of the game window filter above."
  />
  <div id="form-unavailable" class="form-empty" style={formAvailable ? 'display:none' : null}>
    <p id="form-unavailable-text">{unavailableText}</p>
  </div>
  <div id="form-content" style={formAvailable ? null : 'display:none'}>
    <Panel id="form-dossier">
      <svelte:fragment slot="stage">
        <div class="form-stage-score">
          <div class="label">Form score</div>
          <div class="form-score-value form-score-value--{trend}" id="form-score-value">{formScoreText}</div>
          <div class="form-meta">
            <UiChipBadge tone={trend} label={trend} id="form-trend-badge" />
            <UiChipBadge tone="confidence" label="{confidence} confidence" id="form-confidence-badge" />
          </div>
        </div>
        <div class="form-stage-wr">
          <div class="label">Win rate</div>
          <div class="form-wr-bridge" id="form-wr-shift">
            {#if data.form_snapshot}
              <FormWrBridge from={wrFrom} to={wrTo} deltaClass={wrDeltaClass} deltaText={wrDeltaText} />
            {:else}—{/if}
          </div>
          <p class="form-headline" id="form-headline">{data.form_snapshot ? (data.form_snapshot.headline || '') : ''}</p>
        </div>
      </svelte:fragment>
      <SegmentedControl
        id="form-tabs"
        variant="pill"
        as="tablist"
        sticky
        items={formTabs}
        value={activeTab}
        on:select={(e) => selectFormTab(e.detail.value)}
      />
      <div class="form-panel" id="form-panel-pulse" role="tabpanel" hidden={activeTab !== 'pulse'}>
        <div class="form-stories" id="form-stories">
          {#if stories.length}
            {#each stories as story}
              <Callout
                tone={story.tone === 'keep' ? 'good' : 'bad'}
                edge
                label={story.tone === 'keep' ? 'Keep' : 'Fix'}
                title={story.title}
                lines={[
                  { kicker: 'Driver', value: story.driver },
                  ...(story.habit ? [{ kicker: 'Habit', value: story.habit }] : []),
                  { kicker: 'Action', value: story.action },
                ]}
              />
            {/each}
          {:else}
            <p class="sub form-stories-empty" id="form-stories-empty">No standout form stories for this window.</p>
          {/if}
        </div>
        <div class="figure-block form-figure-block">
          <PlotlyFigure id="fig-form_rolling_wr" html={(data.form_figures && data.form_figures.form_rolling_wr) || ''} />
          <p class="figure-caption">Rolling win rate — shaded region is your recent window; grey band is baseline WR.</p>
        </div>
      </div>
      <div class="form-panel" id="form-panel-evidence" role="tabpanel" hidden={activeTab !== 'evidence'}>
        <div class="form-movers" id="form-movers">
          <div class="form-mover-section">
            <h4 class="form-feed-title">Top improvements</h4>
            <div class="form-mover-feed" id="form-improved-list">
              {#if topImproved.length}
                {#each topImproved as row}
                  <TrendRow
                    blockClass="form-mover"
                    tone="positive"
                    label={metricLabelFromRow(row)}
                    values={`${row.baseline} → ${row.recent}`}
                    gap={row.gap}
                    gapColor={row.gap_color || ''}
                  />
                {/each}
              {:else}
                <p class="sub">No standout improvements.</p>
              {/if}
            </div>
          </div>
          <div class="form-mover-section">
            <h4 class="form-feed-title">Regressions</h4>
            <div class="form-mover-feed" id="form-regressed-list">
              {#if topRegressed.length}
                {#each topRegressed as row}
                  <TrendRow
                    blockClass="form-mover"
                    tone="negative"
                    label={metricLabelFromRow(row)}
                    values={`${row.baseline} → ${row.recent}`}
                    gap={row.gap}
                    gapColor={row.gap_color || ''}
                  />
                {/each}
              {:else}
                <p class="sub">No standout regressions.</p>
              {/if}
            </div>
          </div>
        </div>
        <div class="figure-block">
          <PlotlyFigure id="fig-form_metric_delta_bar" html={(data.form_figures && data.form_figures.form_metric_delta_bar) || ''} />
          <p class="figure-caption">Largest metric shifts between recent games and your baseline period.</p>
        </div>
        <details class="all-metrics-details">
          <summary>All metrics</summary>
          <MetricDeltaTable rows={deltaRows} valueHeader="Recent" baselineHeader="Baseline" />
        </details>
      </div>
    </Panel>
  </div>
</section>
