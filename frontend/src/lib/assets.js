/** Map on-disk ``../assets/...`` hrefs to ``/out/assets/...`` for the SPA router. */
export function rewriteAssetHref(href) {
  if (typeof href !== 'string' || !href.includes('assets/')) return href;
  if (href.startsWith('/out/')) return href;
  const marker = 'assets/';
  const index = href.indexOf(marker);
  if (index === -1) return href;
  const prefix = href.slice(0, index);
  if (prefix && !prefix.split('/').filter(Boolean).every((part) => part === '..')) {
    return href;
  }
  return `/out/${href.slice(index)}`;
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
