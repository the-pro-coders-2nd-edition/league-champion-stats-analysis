export async function fetchBuild(slug, buildSlug) {
  const response = await fetch(`/api/players/${slug}/builds/${buildSlug}`);
  if (!response.ok) throw new Error(`Failed to load build: ${response.status}`);
  return response.json();
}

export async function fetchPlayerStatus(slug) {
  const response = await fetch(`/api/players/${slug}`);
  if (!response.ok) throw new Error(`Failed to load player: ${response.status}`);
  return response.json();
}
