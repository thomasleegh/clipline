"""Clipline worker: Twitch top clips -> vertical captioned Shorts -> review queue.

Runs in GitHub Actions. Rendered files are stored as assets on the
`clip-store` GitHub release; data/queue.json tracks every clip seen.
"""
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import requests
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
QUEUE = DATA / "queue.json"
DECISIONS = DATA / "decisions.json"
RELEASE_TAG = "clip-store"
WORK = pathlib.Path(os.environ.get("CLIPLINE_WORK", "/tmp/clipline"))
W, H = 1080, 1920


def log(*a):
    print("[clipline]", *a, flush=True)


def now():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(p, default):
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(p, obj):
    p.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")


def run(cmd, **kw):
    log("$", " ".join(str(c) for c in cmd)[:200])
    return subprocess.run(cmd, check=True, **kw)


# ---------------------------------------------------------------- Twitch
class Twitch:
    def __init__(self):
        self.cid = os.environ["TWITCH_CLIENT_ID"]
        r = requests.post(
            "https://id.twitch.tv/oauth2/token",
            params={
                "client_id": self.cid,
                "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        r.raise_for_status()
        self.token = r.json()["access_token"]

    def get(self, path, **params):
        r = requests.get(
            f"https://api.twitch.tv/helix/{path}",
            headers={"Client-Id": self.cid, "Authorization": f"Bearer {self.token}"},
            params=params,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["data"]

    def user_ids(self, logins):
        return {u["login"].lower(): u["id"] for u in self.get("users", login=logins)}

    def top_clips(self, broadcaster_id, hours):
        end = now()
        return self.get(
            "clips",
            broadcaster_id=broadcaster_id,
            started_at=iso(end - dt.timedelta(hours=hours)),
            ended_at=iso(end),
            first=50,
        )


# ---------------------------------------------------------------- media
def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    j = json.loads(out)
    s = j["streams"][0]
    return int(s["width"]), int(s["height"]), float(j["format"]["duration"])


_whisper = None


def transcribe(path):
    """Word-level timestamps via faster-whisper. Returns [(start, end, word)]."""
    global _whisper
    from faster_whisper import WhisperModel

    if _whisper is None:
        _whisper = WhisperModel(os.environ.get("WHISPER_MODEL", "base"), device="cpu", compute_type="int8")
    segments, _ = _whisper.transcribe(str(path), word_timestamps=True, vad_filter=True)
    words = []
    for seg in segments:
        for w in seg.words or []:
            t = w.word.strip()
            if t:
                words.append((w.start, w.end, t))
    return words


def _ass_time(t):
    t = max(t, 0)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def chunk_words(words, max_words=3, max_gap=0.6):
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w[0] - cur[-1][1] > max_gap or re.search(r"[.!?]$", cur[-1][2])):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words, path, center_y):
    """Big bold pop-in captions, 1-3 words at a time, centred at center_y."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    chunks = chunk_words(words)
    for i, c in enumerate(chunks):
        start = c[0][0]
        end = c[-1][1] + 0.15
        if i + 1 < len(chunks):
            end = min(end, chunks[i + 1][0][0])
        text = " ".join(w[2] for w in c).upper()
        text = text.replace("{", "(").replace("}", ")").replace("\\", "")
        pop = r"{\pos(%d,%d)\fscx85\fscy85\t(0,90,\fscx100\fscy100)}" % (W // 2, center_y)
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{pop}{text}")
    path.write_text(header + "\n".join(lines) + "\n")


def render(src, ass_name, out, layout, facecam, max_dur, cwd):
    sw, sh, _ = probe(src)
    if layout == "stack" and facecam:
        x, y, fw, fh = facecam
        cam_h, game_h = 640, H - 640
        game_w_src = min(sw, int(sh * W / game_h) // 2 * 2)
        graph = (
            f"[0:v]split[a][b];"
            f"[a]crop={int(sw*fw)//2*2}:{int(sh*fh)//2*2}:{int(sw*x)}:{int(sh*y)},"
            f"scale={W}:{cam_h}:force_original_aspect_ratio=increase,crop={W}:{cam_h}[cam];"
            f"[b]crop={game_w_src}:{sh}:(iw-{game_w_src})/2:0,"
            f"scale={W}:{game_h}:force_original_aspect_ratio=increase,crop={W}:{game_h}[game];"
            f"[cam][game]vstack,setsar=1,ass={ass_name}[v]"
        )
    else:
        graph = (
            f"[0:v]split[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"boxblur=24:2,eq=brightness=-0.10[bg];"
            f"[b]scale={W}:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,ass={ass_name}[v]"
        )
    run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-filter_complex", graph,
         "-map", "[v]", "-map", "0:a?", "-t", str(max_dur), "-r", "30",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)],
        cwd=cwd,
    )


def caption_center_y(layout, facecam, src):
    if layout == "stack" and facecam:
        return 640 + 150  # just below the facecam seam
    sw, sh, _ = probe(src)
    fg_h = int(W * sh / sw)
    return min(H - 260, (H + fg_h) // 2 + 170)  # under the centred gameplay


def thumbnail(mp4, jpg):
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", str(mp4), "-frames:v", "1",
         "-vf", "scale=360:-2", "-q:v", "4", str(jpg)])


# ---------------------------------------------------------------- Claude
def review(clip, transcript, cfg):
    """Ask Claude for a score + title. Falls back to the Twitch title."""
    fallback = {"score": None, "title": clip["title"], "hashtags": cfg.get("hashtags", [])}
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return fallback
    prompt = (
        "You pick Twitch clips for a YouTube Shorts / TikTok clip channel.\n"
        f"Streamer: {clip['broadcaster_name']}\nTwitch clip title: {clip['title']}\n"
        f"Views: {clip['view_count']}\nDuration: {clip['duration']}s\n"
        f"Transcript: {transcript or '(no speech)'}\n\n"
        "Reply with JSON only: {\"score\": 1-10 (does this stand alone and hook in the first 2s "
        "for someone who doesn't know the streamer?), \"title\": short punchy title under 60 chars, "
        "no clickbait lies, \"hashtags\": 3-5 hashtags}"
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5"), "max_tokens": 300,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json()["content"])
        j = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        return {"score": int(j.get("score", 0)), "title": str(j.get("title") or clip["title"])[:90],
                "hashtags": [str(h) for h in j.get("hashtags", [])][:5] or fallback["hashtags"]}
    except Exception as e:  # noqa: BLE001
        log("claude review failed:", e)
        return fallback


# ---------------------------------------------------------------- storage
def gh(*args, check=True):
    return subprocess.run(["gh", *args], check=check, capture_output=True, text=True)


def ensure_release():
    if gh("release", "view", RELEASE_TAG, check=False).returncode != 0:
        gh("release", "create", RELEASE_TAG, "--title", "Clip store", "--latest=false",
           "--notes", "Rendered clips, managed by the Clipline worker. Don't edit by hand.")


def upload(*files):
    gh("release", "upload", RELEASE_TAG, *[str(f) for f in files], "--clobber")


def delete_asset(name):
    gh("release", "delete-asset", RELEASE_TAG, name, "-y", check=False)


def cleanup(queue, decisions, keep_days):
    t = now()
    for item in queue:
        d = decisions.get(item["id"])
        if not item.get("stored") or not d:
            continue
        age = t - dt.datetime.fromisoformat(d["at"].replace("Z", "+00:00"))
        if d["action"] == "skipped" or age > dt.timedelta(days=keep_days):
            delete_asset(item["video"])
            delete_asset(item["thumb"])
            item["stored"] = False
            log("deleted files for", item["id"], d["action"])
    cutoff = t - dt.timedelta(days=30)
    return [q for q in queue if q.get("stored") or dt.datetime.fromisoformat(q["added_at"].replace("Z", "+00:00")) > cutoff]


# ---------------------------------------------------------------- main
def main():
    cfg = yaml.safe_load((ROOT / "config.yml").read_text())
    queue = load_json(QUEUE, [])
    decisions = load_json(DECISIONS, {})
    seen = {q["id"] for q in queue}
    streamers = {s["login"].lower(): s for s in cfg["streamers"]}

    ensure_release()
    queue = cleanup(queue, decisions, cfg.get("keep_days", 7))
    save_json(QUEUE, queue)

    tw = Twitch()
    ids = tw.user_ids(list(streamers))
    for login in streamers:
        if login not in ids:
            log(f"WARNING: Twitch user '{login}' not found")

    candidates = []
    for login, bid in ids.items():
        for c in tw.top_clips(bid, cfg.get("lookback_hours", 48)):
            if c["id"] not in seen and c["view_count"] >= cfg.get("min_views", 0):
                c["_login"] = login
                candidates.append(c)
    candidates.sort(key=lambda c: c["view_count"], reverse=True)
    log(f"{len(candidates)} new candidate clips")

    added = 0
    for c in candidates:
        if added >= cfg.get("max_new_per_run", 6):
            break
        s = streamers[c["_login"]]
        wd = WORK / c["id"]
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True)
        item = {"id": c["id"], "streamer": c["broadcaster_name"], "twitch_title": c["title"],
                "views": c["view_count"], "created_at": c["created_at"], "duration": c["duration"],
                "source_url": c["url"], "added_at": iso(now()), "stored": False}
        try:
            src = wd / "src.mp4"
            run(["yt-dlp", "-q", "--no-warnings", "-f", "best[ext=mp4]/best", "-o", str(src), c["url"]])
            words = transcribe(src)
            transcript = " ".join(w[2] for w in words)
            rv = review(c, transcript, cfg)
            item.update(title=rv["title"], hashtags=rv["hashtags"], score=rv["score"], transcript=transcript[:500])
            if rv["score"] is not None and rv["score"] < cfg.get("min_score", 0):
                item["status"] = "filtered"
                log(f"filtered {c['id']} score={rv['score']}")
            else:
                layout, cam = s.get("layout", "blur"), s.get("facecam")
                build_ass(words, wd / "subs.ass", caption_center_y(layout, cam, src))
                safe = re.sub(r"[^A-Za-z0-9_-]", "", c["id"])[:80]
                out, jpg = wd / f"{safe}.mp4", wd / f"{safe}.jpg"
                render(src, "subs.ass", out, layout, cam, cfg.get("max_duration", 60), wd)
                thumbnail(out, jpg)
                upload(out, jpg)
                item.update(status="pending", video=out.name, thumb=jpg.name, stored=True)
                added += 1
                log(f"queued {c['id']} ({c['view_count']} views): {item['title']}")
        except Exception as e:  # noqa: BLE001
            item["status"] = "error"
            item["error"] = str(e)[:300]
            log(f"ERROR on {c['id']}: {e}")
        queue.append(item)
        save_json(QUEUE, queue)  # save as we go so a timeout keeps progress
        shutil.rmtree(wd, ignore_errors=True)

    log(f"DONE — {added} new clips in your queue")


if __name__ == "__main__":
    sys.exit(main())
