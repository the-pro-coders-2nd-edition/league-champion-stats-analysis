/* Status palette for the report. Nocturne is mono, so these are the one place
   status hue is defined: OKLCH-even steps on the same dark ground, low chroma,
   used only where a reader has to judge good / watch / bad at a glance.

   Source of truth: Claude Design project f86e4399-0a80-4039-afd7-d141337da4ec,
   file report-tones.js. Copy verbatim when the design system changes.

   Mirrored in src/league_stats/presentation/tones.py for the Python side --
   tests/test_tones.py checks worked examples against both. */
(function () {
  var T = {
    good: { fg: '#82d2bb', mid: '#2a9f7a', soft: '#10312a', line: '#41b78c' },
    warn: { fg: '#e0ae55', mid: '#8a6a2c', soft: '#2d2415', line: '#ddb258' },
    bad: { fg: '#e0815c', mid: '#a04434', soft: '#31191a', line: '#e05563' },
    flat: { fg: 'var(--color-neutral-400)', mid: 'var(--color-neutral-700)', soft: 'var(--color-neutral-800)', line: 'var(--color-neutral-500)' },
    accent: { fg: 'var(--color-accent-300)', mid: 'var(--color-accent-600)', soft: 'var(--color-accent-900)', line: 'var(--color-accent)' },
  };

  T.tone = function (name) { return T[name] || T.flat; };

  /* delta polarity: 1 = higher is better, -1 = lower is better */
  T.deltaTone = function (delta, polarity) {
    if (delta === null || delta === undefined) return 'flat';
    var good = delta * (polarity || 1);
    if (delta === 0) return 'flat';
    return good > 0 ? 'good' : good > -8 ? 'warn' : 'bad';
  };
  T.deltaLabel = function (delta, polarity, ref) {
    if (delta === null || delta === undefined) return 'no peer baseline';
    var arrow = delta === 0 ? '—' : delta > 0 ? '▲' : '▼';
    return arrow + ' ' + Math.abs(delta) + '%' + (ref ? ' vs ' + ref : '');
  };

  /* one place decides the verdict word and its tone from a 0-100 score */
  T.verdict = function (score) {
    if (score >= 70) return { label: 'Strength', tone: 'good' };
    if (score >= 45) return { label: 'Solid', tone: 'flat' };
    if (score >= 40) return { label: 'Watch', tone: 'warn' };
    return { label: 'Focus', tone: 'bad' };
  };

  T.pValue = function (p) {
    if (p === null || p === undefined) return 'descriptive';
    return p < 0.001 ? 'p < 0.001' : 'p = ' + p.toFixed(3);
  };

  T.priorityTone = function (priority, side) {
    if (side === 'keep') return 'good';
    if (priority === 'High') return 'bad';
    if (priority === 'Medium') return 'warn';
    return 'flat';
  };

  /* Counts always read against the rolling window; the clear and hold bars are
     named, never used as denominators. */
  T.WINDOW = 20;
  T.careerCount = function (state, hit, need, hold, window) {
    var w = window || T.WINDOW;
    if (state === 'Locked') return 'blocked';
    return hit + ' of ' + w;
  };

  T.careerNode = function (state, hit, need) {
    var pct = need ? Math.min(100, Math.round((hit / need) * 100)) : 0;
    var m = {
      Cleared: { tone: 'good', ring: T.good.line, mark: '✓', border: '0' },
      Revoked: { tone: 'bad', ring: 'conic-gradient(' + T.bad.line + ' 0 ' + pct + '%, var(--color-neutral-800) 0)', mark: '', border: '1px dashed ' + T.bad.line },
      'In progress': { tone: 'warn', ring: 'conic-gradient(' + T.warn.line + ' 0 ' + pct + '%, var(--color-neutral-800) 0)', mark: '', border: '0' },
      'At risk': { tone: 'warn', ring: 'conic-gradient(' + T.warn.line + ' 0 ' + pct + '%, ' + T.good.mid + ' 0)', mark: '', border: '1px dashed ' + T.warn.line },
      Locked: { tone: 'flat', ring: 'transparent', mark: '', border: '1px dashed var(--color-neutral-700)' },
    }[state] || { tone: 'flat', ring: 'transparent', mark: '', border: '0' };
    return m;
  };

  window.ReportTones = T;
})();
