// Career spans every ranked game and reads a fixed 20-game window, so it follows
// neither the queue filter nor the game-window filter. It is still delivered inside
// each queue/window slice of the payload, and reports generated before that was
// true only carry a ladder in the all-ranked views. Resolving it here means a
// report on disk from either era renders the same, with no regeneration.

const EMPTY = {
  has_career: false,
  blocks: [],
  widget: [],
  rules: [],
  legend: [],
  congrats: null,
};

// Checked in this order so the all-ranked views -- the only ones an older report
// put a ladder in -- win over a stale copy in a queue-filtered slice.
const QUEUE_PREFERENCE = ['all', 'solo', 'flex'];

/** The Career ladder to render, whichever slice the reader is looking at. */
export function resolveCareerView(payload, sliceCareer) {
  if (sliceCareer && sliceCareer.has_career) return sliceCareer;

  const views = (payload && payload.report_views) || {};
  const queues = [
    ...QUEUE_PREFERENCE.filter((key) => key in views),
    ...Object.keys(views).filter((key) => !QUEUE_PREFERENCE.includes(key)),
  ];
  for (const queue of queues) {
    const windows = (views[queue] && views[queue].windows) || {};
    for (const key of Object.keys(windows)) {
      const career = windows[key] && windows[key].career;
      if (career && career.has_career) return career;
    }
  }

  if (sliceCareer && sliceCareer.has_career === false) return sliceCareer;
  return (payload && payload.career) || EMPTY;
}
