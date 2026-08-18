/** Resize Plotly charts after layout changes (tabs, details, category panels). */

export function resizePlotlyIn(root) {
  if (!root || !window.Plotly?.Plots) return;
  root.querySelectorAll('.js-plotly-plot').forEach((plot) => {
    window.Plotly.Plots.resize(plot);
  });
}

export function resizePlotlySoon(root) {
  if (!root) return;
  requestAnimationFrame(() => {
    resizePlotlyIn(root);
    requestAnimationFrame(() => resizePlotlyIn(root));
  });
}

/** Capture-phase listener: <details> toggle does not bubble. */
export function bindPlotlyDetailsResize() {
  const onToggle = (event) => {
    const details = event.target;
    if (!details || details.tagName !== 'DETAILS' || !details.open) return;
    resizePlotlySoon(details);
  };
  document.addEventListener('toggle', onToggle, true);
  return () => document.removeEventListener('toggle', onToggle, true);
}
