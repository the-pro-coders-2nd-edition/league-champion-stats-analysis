/**
 * Map on-disk ``../assets/...`` hrefs to web URLs for the SPA router.
 *
 * The DDragon icon cache lives in its own volume/mount (`/ddragon`), separate
 * from `/out` (report artifacts under `output_dir`) -- mirrors
 * `report_json._rewrite_asset_href`. Brand assets (`assets/brand/...`) are
 * the one exception: they stay under `/out`, where they have always lived,
 * since they are unrelated to the Data-Dragon download cache this split is
 * about.
 */
export function rewriteAssetHref(href) {
  if (typeof href !== 'string' || !href.includes('assets/')) return href;
  if (href.startsWith('/out/') || href.startsWith('/ddragon/')) return href;
  const marker = 'assets/';
  const index = href.indexOf(marker);
  if (index === -1) return href;
  const prefix = href.slice(0, index);
  if (prefix && !prefix.split('/').filter(Boolean).every((part) => part === '..')) {
    return href;
  }
  const rest = href.slice(index + marker.length);
  if (rest.startsWith('brand/')) {
    return `/out/${href.slice(index)}`;
  }
  return `/ddragon/${rest}`;
}

/** Recursively rewrite report JSON asset paths (mirrors report_json.rewrite_web_asset_hrefs). */
export function rewriteWebAssetHrefs(value) {
  if (Array.isArray(value)) {
    return value.map(rewriteWebAssetHrefs);
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, rewriteWebAssetHrefs(item)]),
    );
  }
  if (typeof value === 'string' && value.includes('assets/')) {
    return rewriteAssetHref(value);
  }
  return value;
}
