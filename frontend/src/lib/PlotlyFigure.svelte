<script>
  import { onMount, afterUpdate, onDestroy } from 'svelte';

  export let html = '';
  export let id = '';
  export let figureClass = 'figure';

  let container;
  let renderedHtml;

  function purge() {
    if (!container || !window.Plotly || !window.Plotly.purge) return;
    container.querySelectorAll('.js-plotly-plot').forEach((plot) => {
      window.Plotly.purge(plot);
    });
  }

  // Figures arrive as server-rendered HTML blobs (a div plus a <script> calling
  // Plotly.newPlot). Assigning via innerHTML never executes that <script>, so it
  // must be recreated and re-inserted for the browser to run it.
  function renderFigure() {
    if (!container) return;
    purge();
    container.innerHTML = html || '';
    container.querySelectorAll('script').forEach((script) => {
      const replacement = document.createElement('script');
      if (script.src) {
        replacement.src = script.src;
      } else {
        replacement.textContent = script.textContent;
      }
      script.parentNode.replaceChild(replacement, script);
    });
  }

  onMount(() => {
    renderedHtml = html;
    renderFigure();
  });

  afterUpdate(() => {
    if (html !== renderedHtml) {
      renderedHtml = html;
      renderFigure();
    }
  });

  onDestroy(purge);
</script>

<div class={figureClass} {id} bind:this={container}></div>
