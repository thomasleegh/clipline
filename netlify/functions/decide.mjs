// PIN-protected API for the review app.
//   GET  /api/decide            -> current decisions {id: {action, at}}
//   POST /api/decide {id, action: "posted"|"skipped"|"undo"}
//   POST /api/decide {action: "run"}  -> trigger the worker now
// Env: APP_PIN, GITHUB_TOKEN (fine-grained: Contents + Actions read/write), GITHUB_REPO, GITHUB_BRANCH?

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });

export default async (req) => {
  const { APP_PIN, GITHUB_TOKEN, GITHUB_REPO } = process.env;
  const branch = process.env.GITHUB_BRANCH || "main";
  if (!APP_PIN || req.headers.get("x-pin") !== APP_PIN) return json({ error: "bad pin" }, 401);

  const gh = (path, opts = {}) =>
    fetch(`https://api.github.com/repos/${GITHUB_REPO}${path}`, {
      ...opts,
      headers: {
        Authorization: `Bearer ${GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "clipline",
        ...(opts.headers || {}),
      },
    });

  const file = `/contents/data/decisions.json`;
  const read = async () => {
    const r = await gh(`${file}?ref=${branch}`);
    if (r.status === 404) return { data: {}, sha: undefined };
    if (!r.ok) throw new Error(`github read ${r.status}`);
    const j = await r.json();
    return { data: JSON.parse(Buffer.from(j.content, "base64").toString("utf8") || "{}"), sha: j.sha };
  };

  try {
    if (req.method === "GET") return json((await read()).data);

    const body = await req.json();
    if (body.action === "run") {
      const r = await gh(`/actions/workflows/worker.yml/dispatches`, {
        method: "POST",
        body: JSON.stringify({ ref: branch }),
      });
      return r.ok ? json({ ok: true }) : json({ error: `dispatch ${r.status}` }, 502);
    }

    const { id, action } = body;
    if (!id || !["posted", "skipped", "undo"].includes(action)) return json({ error: "bad request" }, 400);

    for (let attempt = 0; attempt < 4; attempt++) {
      const { data, sha } = await read();
      if (action === "undo") delete data[id];
      else data[id] = { action, at: new Date().toISOString() };
      const r = await gh(file, {
        method: "PUT",
        body: JSON.stringify({
          message: `review: ${action} ${id} [skip netlify]`,
          content: Buffer.from(JSON.stringify(data, null, 1) + "\n").toString("base64"),
          sha,
          branch,
        }),
      });
      if (r.ok) return json({ ok: true });
      if (r.status !== 409 && r.status !== 422) return json({ error: `github write ${r.status}` }, 502);
    }
    return json({ error: "conflict, try again" }, 409);
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
};

export const config = { path: "/api/decide" };
