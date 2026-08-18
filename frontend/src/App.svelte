<script>
  import Router from 'svelte-spa-router';
  import Landing from './routes/Landing.svelte';
  import PlayerHub from './routes/PlayerHub.svelte';
  import Report from './routes/Report.svelte';

  const routes = {
    '/': Landing,
    '/players/:slug': PlayerHub,
    '/players/:slug/:buildSlug': Report,
  };
</script>

<Router {routes} />
