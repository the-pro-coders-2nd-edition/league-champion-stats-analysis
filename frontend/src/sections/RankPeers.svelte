<script>
  import Pill from '../components/Pill.svelte';
  import Panel from '../components/Panel.svelte';
  import PeerRankValue from '../components/PeerRankValue.svelte';
  import PeerBalanceChip from '../components/PeerBalanceChip.svelte';
  import UiChipBadge from '../components/UiChipBadge.svelte';
  import TrendRow from '../components/TrendRow.svelte';
  import DataTableHead from '../components/DataTableHead.svelte';
  import DataTableRow from '../components/DataTableRow.svelte';
  import { escapeHtml, metricLabelFromRow } from '../lib/html.js';
  import { computeWindowScopeLabel } from '../lib/windowScope.js';

  export let data;
  export let peerStageDetail = '';
  export let peerFailed = false;
  export let peerUnavailable = false;

  function peerTableRowCellsHtml(row) {
    const style = row.gap_color ? ` style="color: ${row.gap_color}"` : '';
    return `<td>${metricLabelFromRow(row)}</td><td>${escapeHtml(row.yours)}</td><td>${escapeHtml(row.peer_avg)}</td>` +
      `<td class="delta-${row.verdict}"${style}>${escapeHtml(row.gap)}</td><td class="delta-${row.verdict}"${style}>${escapeHtml(row.verdict)}</td>`;
  }

  $: windowScopeLabel = computeWindowScopeLabel(data);

  $: hasPeerComparison = !!data.has_peer_comparison;
  $: pending = !hasPeerComparison && !!data.status_endpoint && !peerFailed && !peerUnavailable;
  $: pendingMessage = peerStageDetail
    || 'Peer comparison is still running — this section updates when it finishes.';

  $: peerComparison = data.peer_comparison || {};
  $: peerRows = data.peer_rows || [];
  $: peerAbove = peerRows.filter((row) => row.verdict === 'above');
  $: peerBelow = peerRows.filter((row) => row.verdict === 'below');

  $: tableColumns = [
    { html: 'Metric', id: '' },
    { html: 'You', id: '' },
    { html: `${(peerComparison.tier || '').charAt(0).toUpperCase()}${(peerComparison.tier || '').slice(1).toLowerCase()} ${data.build_label || ''} avg`, id: 'peer-table-peer-header' },
    { html: 'Gap', id: '' },
    { html: 'Verdict', id: '' },
  ];
</script>

{#if hasPeerComparison}
<section id="rank-peers" class="report-section report-section--performance">
  <h2 class="section-title section-title--performance">
    <iconify-icon icon="lucide:bar-chart-2" class="metric-icon metric-icon--accent" aria-hidden="true"></iconify-icon>
    <span>Rank peer comparison</span>
    <Pill tone="flat" variant="outline" extraClass="scope-chip--window" dot={false} label={windowScopeLabel} />
  </h2>
  <p class="sub sub--lead" id="peer-subtitle">Your averages vs {data.build_label} at <strong>{peerComparison.rank_label}</strong> · {peerComparison.peer_games} peer games ({peerComparison.peer_players} players, {peerComparison.confidence} confidence)</p>
  <Panel id="peer-dossier">
    <svelte:fragment slot="stage">
      <div class="peer-stage-rank">
        <div class="label">Rank peers</div>
        <div class="peer-rank-value" id="peer-rank-value">
          <PeerRankValue icon={data.peer_rank_icon || ''} label={peerComparison.rank_label} />
        </div>
      </div>
      <div class="peer-stage-meta">
        <div class="peer-balance" id="peer-balance">
          <PeerBalanceChip modifier="above" countId="peer-above-count" count={peerAbove.length} label="above" />
          <PeerBalanceChip modifier="below" countId="peer-below-count" count={peerBelow.length} label="below" />
        </div>
        <div class="peer-meta-chips" id="peer-meta-chips">
          <UiChipBadge tone="meta" label={`${peerComparison.peer_games} peer games`} />
          <UiChipBadge tone="meta" label={`${peerComparison.peer_players} players`} />
          <UiChipBadge tone="confidence" label={`${peerComparison.confidence} confidence`} />
        </div>
      </div>
    </svelte:fragment>
    <div class="peer-drivers" id="peer-drivers">
      <div class="peer-driver-section">
        <h4 class="peer-feed-title">Above peers</h4>
        <div class="peer-driver-feed" id="peer-above-list">
          {#if peerAbove.length}
            {#each peerAbove.slice(0, 6) as row}
              <TrendRow blockClass="peer-driver" tone="positive" label={metricLabelFromRow(row)} values={`${row.yours} vs ${row.peer_avg}`} gap={row.gap} gapColor={row.gap_color || ''} />
            {/each}
          {:else}
            <p class="sub">No above-peer metrics.</p>
          {/if}
        </div>
      </div>
      <div class="peer-driver-section">
        <h4 class="peer-feed-title">Below peers</h4>
        <div class="peer-driver-feed" id="peer-below-list">
          {#if peerBelow.length}
            {#each peerBelow.slice(0, 6) as row}
              <TrendRow blockClass="peer-driver" tone="negative" label={metricLabelFromRow(row)} values={`${row.yours} vs ${row.peer_avg}`} gap={row.gap} gapColor={row.gap_color || ''} />
            {/each}
          {:else}
            <p class="sub">No below-peer metrics.</p>
          {/if}
        </div>
      </div>
    </div>
    <details class="peer-all-metrics">
      <summary>All metrics</summary>
      <div class="table-scroll">
        <table>
          <DataTableHead columns={tableColumns} />
          <tbody id="peer-table-body">
            {#each peerRows as row}
              <DataTableRow cellsHtml={peerTableRowCellsHtml(row)} />
            {/each}
          </tbody>
        </table>
      </div>
    </details>
  </Panel>
</section>
{:else if pending}
<section id="rank-peers" class="report-section report-section--performance" data-peer-pending="1">
  <h2 class="section-title section-title--performance">
    <iconify-icon icon="lucide:bar-chart-2" class="metric-icon metric-icon--accent" aria-hidden="true"></iconify-icon>
    <span>Rank peer comparison</span>
    <Pill tone="flat" variant="outline" extraClass="scope-chip--window" dot={false} label={windowScopeLabel} />
  </h2>
  <Panel>
    <div class="peer-pending" role="status" aria-live="polite">
      <p class="sub" id="rank-peers-pending">{pendingMessage}</p>
    </div>
  </Panel>
</section>
{:else if peerFailed}
<section id="rank-peers" class="report-section report-section--performance" data-peer-failed="1">
  <h2 class="section-title section-title--performance">
    <iconify-icon icon="lucide:bar-chart-2" class="metric-icon metric-icon--accent" aria-hidden="true"></iconify-icon>
    <span>Rank peer comparison</span>
    <Pill tone="flat" variant="outline" extraClass="scope-chip--window" dot={false} label={windowScopeLabel} />
  </h2>
  <Panel>
    <div class="peer-pending" role="status">
      <p class="sub is-failed" id="rank-peers-pending">Peer comparison could not be completed for this report.</p>
    </div>
  </Panel>
</section>
{:else if peerUnavailable}
<section id="rank-peers" class="report-section report-section--performance" data-peer-unavailable="1">
  <h2 class="section-title section-title--performance">
    <iconify-icon icon="lucide:bar-chart-2" class="metric-icon metric-icon--accent" aria-hidden="true"></iconify-icon>
    <span>Rank peer comparison</span>
    <Pill tone="flat" variant="outline" extraClass="scope-chip--window" dot={false} label={windowScopeLabel} />
  </h2>
  <Panel>
    <div class="peer-pending" role="status">
      <p class="sub" id="rank-peers-pending">Rank peer comparison is not available for this build (rank or baseline data could not be resolved).</p>
    </div>
  </Panel>
</section>
{/if}
