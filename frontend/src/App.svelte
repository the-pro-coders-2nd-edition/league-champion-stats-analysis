<script>
  import Router from 'svelte-spa-router';
  import Landing from './routes/Landing.svelte';
  import PlayerHub from './routes/PlayerHub.svelte';
  import Report from './routes/Report.svelte';

  const routes = {
    '/': Landing,
    '/players/:slug': PlayerHub,
    // ':tab?' (regexparam optional-param syntax) makes the report category part of
    // the URL, so e.g. '/players/hugros_euw/aatrox_top/career' opens directly on the
    // Career tab -- the whole point being that it can be bookmarked. Absent, it
    // defaults to the summary tab, so every link/bookmark made before this existed
    // keeps working unchanged.
    '/players/:slug/:buildSlug/:tab?': Report,
  };
</script>

<Router {routes} />
