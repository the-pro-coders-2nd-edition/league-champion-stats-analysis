<script lang="ts">
  import DataTable from './DataTable.svelte';

  export let rows: any[];
  export let valueHeader: string;
  export let baselineHeader: string;

  $: columns = [
    { label: 'Metric' },
    { label: valueHeader },
    { label: baselineHeader },
    { label: 'Change' },
    { label: 'Verdict' },
  ];
</script>

<DataTable {columns} {rows}>
  <svelte:fragment slot="cells" let:row>
    <td>
      <span class="metric-label">
        {#if row.icon_href}<img src={row.icon_href} alt="" class="metric-icon metric-icon--asset" aria-hidden="true">{/if}
        <span>{row.label}</span>
      </span>
    </td>
    <td>{row.value}</td>
    <td>{row.baseline}</td>
    <td class="delta-{row.verdict}" style={row.gap_color ? `color: ${row.gap_color}` : undefined}>{row.gap}</td>
    <td class="delta-{row.verdict}" style={row.gap_color ? `color: ${row.gap_color}` : undefined}>{row.verdict}</td>
  </svelte:fragment>
</DataTable>
