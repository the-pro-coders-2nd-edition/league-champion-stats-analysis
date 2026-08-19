import { tick } from 'svelte';
import { writable } from 'svelte/store';

/** Map section element ids to report category tab values. */
const SECTION_CATEGORIES = {
  overview: 'summary',
  coaching: 'summary',
  'score-breakdown': 'summary',
  'game-review': 'games',
  career: 'career',
  'form-tracker': 'performance',
  'rank-peers': 'performance',
  matchups: 'champion',
  items: 'champion',
  runes: 'champion',
  lane: 'deepdive',
  objectives: 'deepdive',
  deaths: 'deepdive',
  vision: 'deepdive',
  economy: 'deepdive',
  teamfights: 'deepdive',
  positioning: 'deepdive',
  graphs: 'deepdive',
};

export const REPORT_NAV_KEY = 'reportNav';

export function categoryForSection(sectionId) {
  if (!sectionId) return null;
  if (SECTION_CATEGORIES[sectionId]) return SECTION_CATEGORIES[sectionId];
  if (sectionId.startsWith('coaching-tip-')) return 'summary';
  // Silently returning null here is exactly how 18 inert `.section-title--*` modifier
  // classes were produced historically — fail loudly instead of degrading quietly.
  throw new Error(`categoryForSection: unknown section id "${sectionId}"`);
}

/** Shared click handler for anchor links that scroll to a report section via
 *  the reportNav context, falling back to a plain scrollIntoView when there
 *  is no Report.svelte ancestor (e.g. a standalone render). */
export function handleNavClick(reportNav, anchor) {
  return function onClick(event) {
    event.preventDefault();
    if (reportNav) {
      reportNav.scrollToSection(anchor);
      return;
    }
    document.getElementById(anchor)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
}

export function createReportNav(selectCategory) {
  const highlightId = writable(null);
  let highlightTimer = null;

  async function setHighlight(sectionId) {
    if (highlightTimer) {
      clearTimeout(highlightTimer);
      highlightTimer = null;
    }
    if (!sectionId || !sectionId.startsWith('coaching-tip-')) {
      highlightId.set(null);
      return;
    }
    highlightId.set(null);
    await tick();
    highlightId.set(sectionId);
    highlightTimer = setTimeout(() => {
      highlightId.update((current) => (current === sectionId ? null : current));
      highlightTimer = null;
    }, 6000);
  }

  return {
    highlightId,
    async scrollToSection(sectionId) {
      const category = categoryForSection(sectionId);
      if (category) selectCategory(category);
      await setHighlight(sectionId);
      await tick();
      const el = document.getElementById(sectionId);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    },
  };
}
