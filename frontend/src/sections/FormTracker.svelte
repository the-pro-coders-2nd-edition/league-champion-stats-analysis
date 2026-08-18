<script>
  import { tick } from 'svelte';
  import Pill from '../components/Pill.svelte';
  import UiChipBadge from '../components/UiChipBadge.svelte';
  import FormWrBridge from '../components/FormWrBridge.svelte';
  import TabBar from '../components/TabBar.svelte';
  import FormStoryHead from '../components/FormStoryHead.svelte';
  import FormStoryLine from '../components/FormStoryLine.svelte';
  import DataTableHead from '../components/DataTableHead.svelte';
  import DataTableRow from '../components/DataTableRow.svelte';
  import PlotlyFigure from '../lib/PlotlyFigure.svelte';
  import { resizePlotlySoon } from '../lib/plotlyResize.js';
  import TrendRow from '../components/TrendRow.svelte';
  import { escapeHtml, metricLabelFromRow } from '../lib/html.js';

  export let data;

  let activeTab = 'pulse';

  function deltaRowCellsHtml(row) {
    const style = row.gap_color ? ` style="color: ${row.gap_color}"` : '';
    return `<td>${metricLabelFromRow(row)}</td><td>${escapeHtml(row.recent)}</td><td>${escapeHtml(row.baseline)}</td>` +
      `<td class="delta-${row.verdict}"${style}>${escapeHtml(row.gap)}</td><td class="delta-${row.verdict}"${style}>${escapeHtml(row.verdict)}</td>`;
  }

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
  $: deltaRows = data.form_delta_rows || [];

  const tableColumns = ['Metric', 'Recent', 'Baseline', 'Change', 'Verdict'].map((html) => ({ html, id: '' }));

  $: formTabs = [
    { value: 'pulse', label: 'Pulse', active: activeTab === 'pulse' },
    { value: 'evidence', label: 'Evidence', active: activeTab === 'evidence' },
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
  <h2 class="section-title section-title--performance">
    <span>Form tracker</span>
    <Pill tone="flat" variant="outline" dot={false} extraClass="" label="Recent games vs your baseline" />
  </h2>
  <p class="sub sub--lead" id="form-subtitle">{data.form_sample_subtitle || 'Recent form vs your personal baseline'} — independent of the game window filter above.</p>
  <div id="form-unavailable" class="form-empty" style={formAvailable ? 'display:none' : null}>
    <p id="form-unavailable-text">{unavailableText}</p>
  </div>
  <div id="form-content" style={formAvailable ? null : 'display:none'}>
    <div class="form-dossier form-dossier--{trend}" id="form-dossier">
      <div class="form-stage" id="form-stage">
        <div class="form-stage-inner">
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
        </div>
      </div>
      <TabBar
        containerId="form-tabs"
        buttonClass="form-tab"
        dataAttr="data-tab"
        tabs={formTabs}
        on:select={(e) => selectFormTab(e.detail)}
      />
      <div class="form-panel" id="form-panel-pulse" role="tabpanel" hidden={activeTab !== 'pulse'}>
        <div class="form-stories" id="form-stories">
          {#if stories.length}
            {#each stories as story}
              <article class="form-story form-story--{story.tone === 'keep' ? 'keep' : 'fix'}">
                <FormStoryHead label={story.tone === 'keep' ? 'Keep' : 'Fix'} title={story.title} />
                <FormStoryLine variant="driver" value={story.driver} />
                {#if story.habit}
                  <FormStoryLine variant="habit" value={story.habit} />
                {/if}
                <FormStoryLine variant="action" value={story.action} />
              </article>
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
        <details class="form-all-metrics">
          <summary>All metrics</summary>
          <div class="table-scroll">
            <table>
              <DataTableHead columns={tableColumns} />
              <tbody id="form-table-body">
                {#each deltaRows as row}
                  <DataTableRow cellsHtml={deltaRowCellsHtml(row)} />
                {/each}
              </tbody>
            </table>
          </div>
        </details>
      </div>
    </div>
  </div>
</section>
