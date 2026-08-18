<script lang="ts">
  export let tone: string;
  export let label: string;
  export let id: string = '';
  export let title: string = '';
</script>

<span class="ui-chip ui-chip--{tone}" id="{id}" title={title || undefined}>{label}</span>
