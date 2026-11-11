"""Netlify build: copy the app and pull pending clips from the clip-store release.

Stdlib only. Env: GITHUB_REPO (owner/name), optional GITHUB_TOKEN for private repos.
"""
import json
import os
import pathlib
import shutil
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
REPO = os.environ.get("GITHUB_REPO") or os.environ.get("REPOSITORY_URL", "").replace("https://github.com/", "")
TOKEN = os.environ.get("GITHUB_TOKEN")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def api(url, accept="application/vnd.github+json"):
    h = {"Accept": accept, "User-Agent": "clipline-build"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return urllib.request.Request(url, headers=h)


def download_asset(asset, dest):
    """Asset API URL -> redirect to signed storage URL (fetched without auth)."""
    try:
        urllib.request.build_opener(NoRedirect).open(api(asset["url"], "application/octet-stream"))
        raise RuntimeError("expected redirect")
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        loc = e.headers["Location"]
    with urllib.request.urlopen(urllib.request.Request(loc, headers={"User-Agent": "clipline-build"})) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def main():
    shutil.rmtree(DIST, ignore_errors=True)
    shutil.copytree(ROOT / "site", DIST)
    (DIST / "clips").mkdir()

    queue = json.loads((ROOT / "data" / "queue.json").read_text())
    decisions = json.loads((ROOT / "data" / "decisions.json").read_text())
    live = [q for q in queue if q.get("stored") and q.get("status") == "pending"]

    assets = {}
    if live and REPO:
        try:
            with urllib.request.urlopen(api(f"https://api.github.com/repos/{REPO}/releases/tags/clip-store")) as r:
                assets = {a["name"]: a for a in json.load(r)["assets"]}
        except urllib.error.HTTPError as e:
            print("could not read clip-store release:", e)

    out = []
    for q in sorted(live, key=lambda q: q["added_at"], reverse=True):
        if q["video"] not in assets:
            continue
        try:
            download_asset(assets[q["video"]], DIST / "clips" / q["video"])
            if q["thumb"] in assets:
                download_asset(assets[q["thumb"]], DIST / "clips" / q["thumb"])
        except Exception as e:  # noqa: BLE001
            print("skip", q["id"], e)
            continue
        q = {k: v for k, v in q.items() if k != "transcript"}
        q["decision"] = decisions.get(q["id"], {}).get("action")
        out.append(q)

    (DIST / "clips.json").write_text(json.dumps({"repo": REPO, "clips": out}))
    print(f"built site with {len(out)} clips")


if __name__ == "__main__":
    main()
