<script>
  import DataTable from './DataTable.svelte';
  import { pyFloatStr } from '../lib/format.js';

  export let firstColumnLabel;
  export let rows;

  $: columns = [
    { label: firstColumnLabel },
    { label: 'Games' },
    { label: 'Win rate' },
    { label: 'Avg DPM', iconify: 'lucide:flame' },
    { label: 'Avg deaths', iconify: 'lucide:skull' },
  ];
</script>

<DataTable {columns} {rows}>
  <svelte:fragment slot="cells" let:row>
    <td><slot name="first" {row} /></td>
    <td>{row.games}</td>
    <td>{Math.round(row.winrate * 100)}%</td>
    <td>{pyFloatStr(row.avg_dpm)}</td>
    <td>{pyFloatStr(row.avg_deaths)}</td>
  </svelte:fragment>
</DataTable>
