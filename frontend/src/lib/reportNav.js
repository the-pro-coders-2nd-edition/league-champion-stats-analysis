import { tick } from 'svelte';

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

export function createReportNav(selectCategory) {
  return {
    async scrollToSection(sectionId) {
      const category = categoryForSection(sectionId);
      if (category) selectCategory(category);
      await tick();
      const el = document.getElementById(sectionId);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    },
  };
}
