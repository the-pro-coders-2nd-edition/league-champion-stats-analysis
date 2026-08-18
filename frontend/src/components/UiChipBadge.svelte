<script lang="ts">
  export let tone: string;
  export let label: string;
  export let id: string = '';
</script>

<span class="ui-chip ui-chip--{tone}" id="{id}">{label}</span>
