// Serves firmware_rss.xml from GitHub as a proper RSS feed, cached at the edge.
const GITHUB_URL =
  "https://raw.githubusercontent.com/bearkevin/apple-firmware-tracker/refs/heads/main/firmware_rss.xml";
const CACHE_TTL = 1800;

// raw.githubusercontent.com always answers text/plain with a sandboxing CSP,
// nosniff and frame-deny, which breaks feed readers. Rebuild the headers.
function rssResponse(body, upstream) {
  const headers = new Headers({
    "Content-Type": "application/rss+xml; charset=utf-8",
    "Cache-Control": `public, max-age=${CACHE_TTL}`,
    "Access-Control-Allow-Origin": "*",
  });
  const etag = upstream.headers.get("ETag");
  if (etag) headers.set("ETag", etag);
  return new Response(body, { status: 200, headers });
}

async function fetchFromGitHub() {
  return fetch(GITHUB_URL, { headers: { "User-Agent": "CF-Worker" } });
}

export default {
  async fetch(request, env, ctx) {
    const cache = caches.default;
    const cacheKey = GITHUB_URL;
    const refresh = new URL(request.url).searchParams.get("refresh") === "true";

    if (!refresh) {
      const cached = await cache.match(cacheKey);
      // Re-wrap so entries cached before this fix also get the right headers.
      if (cached) return rssResponse(cached.body, cached);
    } else {
      console.log("Manual refresh triggered");
    }

    const fetched = await fetchFromGitHub();
    if (!fetched.ok) {
      return new Response(`GitHub fetch failed: ${fetched.status}`, { status: 502 });
    }

    const fresh = rssResponse(fetched.body, fetched);
    ctx.waitUntil(cache.put(cacheKey, fresh.clone()));
    return fresh;
  },
};
