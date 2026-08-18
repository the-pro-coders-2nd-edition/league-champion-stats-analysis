<script>
  import Pill from '../components/Pill.svelte';
  import PeerRankValue from '../components/PeerRankValue.svelte';
  import PeerBalanceChip from '../components/PeerBalanceChip.svelte';
  import PeerMetaChip from '../components/PeerMetaChip.svelte';
  import PeerDriverRow from '../components/PeerDriverRow.svelte';
  import DataTableHead from '../components/DataTableHead.svelte';
  import DataTableRow from '../components/DataTableRow.svelte';

  export let data;

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // Mirrors the generated peer_driver_row/data_table_row_peer_details partials, which call the
  // `metric_label` macro with `row.iconify` as its positional `icon` (icon-key) argument rather
  // than `iconify_id`. That icon-key lookup never resolves for real data (row.iconify already
  // holds a resolved "prefix:name" id), so no iconify icon is ever rendered here in practice —
  // only `icon_href` produces a visible icon. Replicated as-is.
  function metricIconHtml(row) {
    if (!row.icon_href) return '';
    const tone = row.icon_tone || 'muted';
    return `<img src="${row.icon_href}" alt="" class="metric-icon metric-icon--asset metric-icon--${tone}" aria-hidden="true">`;
  }

  function metricLabelHtml(row) {
    return `<span class="metric-label">${metricIconHtml(row)}<span>${escapeHtml(row.label)}</span></span>`;
  }

  function peerTableRowCellsHtml(row) {
    const style = row.gap_color ? ` style="color: ${row.gap_color}"` : '';
    return `<td>${metricLabelHtml(row)}</td><td>${escapeHtml(row.yours)}</td><td>${escapeHtml(row.peer_avg)}</td>` +
      `<td class="delta-${row.verdict}"${style}>${escapeHtml(row.gap)}</td><td class="delta-${row.verdict}"${style}>${escapeHtml(row.verdict)}</td>`;
  }

  $: windowScopeOption = (data.game_window_options || []).find((o) => o.key === data.game_window_default);
  $: windowScopeLabel = windowScopeOption ? `${windowScopeOption.label} games` : 'All games';

  $: hasPeerComparison = !!data.has_peer_comparison;
  $: pending = !hasPeerComparison && !!data.status_endpoint;

  $: peerComparison = data.peer_comparison || {};
  $: peerRows = data.peer_rows || [];
  $: peerAbove = peerRows.filter((row) => row.verdict === 'above');
  $: peerBelow = peerRows.filter((row) => row.verdict === 'below');
  $: peerLean = peerAbove.length > peerBelow.length
    ? 'above'
    : (peerBelow.length > peerAbove.length ? 'below' : 'even');

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
  <div class="peer-dossier peer-dossier--{peerLean}" id="peer-dossier">
    <div class="peer-stage" id="peer-stage">
      <div class="peer-stage-inner">
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
            <PeerMetaChip text={`${peerComparison.peer_games} peer games`} />
            <PeerMetaChip text={`${peerComparison.peer_players} players`} />
            <PeerMetaChip modifier="confidence" text={`${peerComparison.confidence} confidence`} />
          </div>
        </div>
      </div>
    </div>
    <div class="peer-drivers" id="peer-drivers">
      <div class="peer-driver-section">
        <h4 class="peer-feed-title">Above peers</h4>
        <div class="peer-driver-feed" id="peer-above-list">
          {#if peerAbove.length}
            {#each peerAbove.slice(0, 6) as row}
              <PeerDriverRow tone="above" label={metricLabelHtml(row)} values={`${row.yours} vs ${row.peer_avg}`} gap={row.gap} gapColor={row.gap_color || ''} />
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
              <PeerDriverRow tone="below" label={metricLabelHtml(row)} values={`${row.yours} vs ${row.peer_avg}`} gap={row.gap} gapColor={row.gap_color || ''} />
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
  </div>
</section>
{:else if pending}
<section id="rank-peers" class="report-section report-section--performance" data-peer-pending="1">
  <h2 class="section-title section-title--performance">
    <iconify-icon icon="lucide:bar-chart-2" class="metric-icon metric-icon--accent" aria-hidden="true"></iconify-icon>
    <span>Rank peer comparison</span>
    <Pill tone="flat" variant="outline" extraClass="scope-chip--window" dot={false} label={windowScopeLabel} />
  </h2>
  <div class="peer-dossier peer-dossier--pending">
    <div class="peer-pending" role="status" aria-live="polite">
      <p class="sub" id="rank-peers-pending">Peer comparison is still running — this section updates when it finishes.</p>
    </div>
  </div>
</section>
{/if}
