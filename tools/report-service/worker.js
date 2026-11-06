// hamdash problem-report upload service - a Cloudflare Worker.
//
// POST /report with a hamdash report zip as the body; headers say what it
// is (X-Hamdash-Version, -Platform, -Radio, and an optional -Note, URL-
// encoded). The zip is streamed, untouched, to a release asset in the
// private GitHub repository REPO, and an issue is opened there linking it.
// The reply is {"ok": true, "reference": "HD-7F3KQ"}.
//
// Streamed, not committed: a commit through the contents API needs the
// file base64-encoded in JSON, and the free plan's 10 ms of CPU a request
// is not enough to encode a large report. Passing the body through to
// uploads.github.com costs almost none.
//
// The app holds no secret - anything shipped in a download can be pulled
// out of it - so the guards are the size cap, the zip check, the rate
// limit and the repository being private. GITHUB_TOKEN (a fine-grained
// token for REPO alone: contents and issues) lives only in Cloudflare.
//
// Once a day, `scheduled` deletes report zips older than KEEP_DAYS.
// Setup: SETUP.md beside this file.

export const MAX_BYTES = 25 * 1024 * 1024;
export const KEEP_DAYS = 90;
const API = "https://api.github.com";
const UPLOADS = "https://uploads.github.com";
const ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ";   // no 0/O, 1/I/L, U

export function reference(random = crypto.getRandomValues(new Uint8Array(5))) {
  let out = "";
  for (const b of random) out += ALPHABET[b % ALPHABET.length];
  return `HD-${out}`;
}

export function releaseTag(now = new Date()) {
  return `reports-${now.toISOString().slice(0, 7)}`;
}

// A header value from the app, cleaned for a title or a body: decoded,
// control characters out, cut to a length.
export function field(raw, max) {
  let s = String(raw || "");
  try { s = decodeURIComponent(s); } catch { /* keep it as sent */ }
  return s.replace(/[\u0000-\u001f\u007f]+/g, " ").trim().slice(0, max);
}

export function issueText(ref, info, assetUrl, size, now = new Date()) {
  const title = [ref, info.version && `hamdash ${info.version}`,
                 info.platform, info.radio].filter(Boolean).join(" · ");
  const lines = [
    `**Reference:** ${ref}`,
    `**Received:** ${now.toISOString().replace(/\.\d+Z$/, "Z")}`,
    `**hamdash:** ${info.version || "?"} on ${info.platform || "?"}`,
    `**Radio:** ${info.radio || "none selected"}`,
    `**Report:** [${ref}.zip](${assetUrl}) (${Math.round(size / 1024)} kB)`,
    "",
    "**What went wrong, in their words:**",
    "",
    info.note ? info.note.split("\n").map((l) => `> ${l}`).join("\n")
              : "_(nothing written)_",
    "",
    `<sub>Download: \`gh release download ${info.tag} -R ${info.repo} -p ${ref}.zip\`. ` +
    `The zip is deleted after ${KEEP_DAYS} days.</sub>`,
  ];
  return { title: title.slice(0, 250), body: lines.join("\n") };
}

export function expired(assets, now = new Date(), keepDays = KEEP_DAYS) {
  const cutoff = now.getTime() - keepDays * 86400 * 1000;
  return assets.filter((a) => Date.parse(a.created_at) < cutoff);
}

function reply(status, body) {
  return new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  });
}

function gh(env, path, init = {}, base = API) {
  return fetch(base + path, {
    ...init,
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "hamdash-report-service",
      ...(init.headers || {}),
    },
  });
}

async function releaseFor(env, tag) {
  const got = await gh(env, `/repos/${env.REPO}/releases/tags/${tag}`);
  if (got.ok) return got.json();
  if (got.status !== 404) throw new Error(`release lookup: ${got.status}`);
  const made = await gh(env, `/repos/${env.REPO}/releases`, {
    method: "POST",
    body: JSON.stringify({ tag_name: tag, name: `Problem reports ${tag.slice(8)}`,
                           body: "Problem reports sent from hamdash.",
                           prerelease: true }),
  });
  if (!made.ok) throw new Error(`release create: ${made.status} ${await made.text()}`);
  return made.json();
}

// The first bytes, checked, and a stream that still yields every byte.
async function peekZip(body) {
  const reader = body.getReader();
  const first = await reader.read();
  const head = first.value || new Uint8Array(0);
  const isZip = head.length >= 4 && head[0] === 0x50 && head[1] === 0x4b &&
                head[2] === 0x03 && head[3] === 0x04;
  const stream = new ReadableStream({
    start(c) { if (head.length) c.enqueue(head); if (first.done) c.close(); },
    async pull(c) {
      const { done, value } = await reader.read();
      if (done) c.close(); else c.enqueue(value);
    },
    cancel(r) { reader.cancel(r); },
  });
  return { isZip, stream };
}

async function receive(request, env) {
  const ip = request.headers.get("CF-Connecting-IP") || "?";
  if (env.LIMITER) {
    const { success } = await env.LIMITER.limit({ key: ip });
    if (!success) return reply(429, { ok: false, error: "too many reports from here; try again in a minute" });
  }
  const size = Number(request.headers.get("Content-Length") || 0);
  if (!size) return reply(411, { ok: false, error: "no report in the request" });
  if (size > MAX_BYTES) return reply(413, { ok: false, error: `report larger than ${MAX_BYTES >> 20} MB` });
  if (!env.GITHUB_TOKEN) return reply(503, { ok: false, error: "the report service is not set up yet" });

  const { isZip, stream } = await peekZip(request.body);
  if (!isZip) return reply(415, { ok: false, error: "that is not a hamdash report" });

  const ref = reference();
  const tag = releaseTag();
  const info = {
    version: field(request.headers.get("X-Hamdash-Version"), 40),
    platform: field(request.headers.get("X-Hamdash-Platform"), 60),
    radio: field(request.headers.get("X-Hamdash-Radio"), 60),
    note: field(request.headers.get("X-Hamdash-Note"), 4000),
    tag, repo: env.REPO,
  };
  try {
    const rel = await releaseFor(env, tag);
    const up = await gh(env, `/repos/${env.REPO}/releases/${rel.id}/assets?name=${ref}.zip`, {
      method: "POST", body: stream,
      headers: { "Content-Type": "application/zip", "Content-Length": String(size) },
    }, UPLOADS);
    if (!up.ok) throw new Error(`upload: ${up.status} ${await up.text()}`);
    const asset = await up.json();
    const text = issueText(ref, info, asset.browser_download_url, size);
    const issue = await gh(env, `/repos/${env.REPO}/issues`, {
      method: "POST", body: JSON.stringify(text),
    });
    if (!issue.ok) throw new Error(`issue: ${issue.status} ${await issue.text()}`);
    return reply(200, { ok: true, reference: ref });
  } catch (exc) {
    console.log("report failed", ref, String(exc));
    return reply(502, { ok: false, error: "the report could not be stored; please try again later" });
  }
}

async function prune(env) {
  const rels = await (await gh(env, `/repos/${env.REPO}/releases?per_page=100`)).json();
  for (const rel of Array.isArray(rels) ? rels : []) {
    if (!String(rel.tag_name || "").startsWith("reports-")) continue;
    for (const a of expired(rel.assets || [])) {
      await gh(env, `/repos/${env.REPO}/releases/assets/${a.id}`, { method: "DELETE" });
    }
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/report" && request.method === "POST") return receive(request, env);
    if (url.pathname === "/" && request.method === "GET") {
      return reply(200, { ok: true, service: "hamdash problem reports" });
    }
    return reply(404, { ok: false, error: "not here" });
  },
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(prune(env));
  },
};
