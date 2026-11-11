# Clipline

Twitch top clips → vertical 9:16 Shorts with burned-in captions → review queue on your phone → share sheet to TikTok / Shorts / Reels.

```
GitHub Actions (every 4h)                     Netlify (phone app)
  Twitch Get Clips (top by views)               clips.json + mp4s pulled at build
  yt-dlp download                               Post ↗ = iOS share sheet (caption copied)
  faster-whisper word captions                  Skip / Undo / Run now
  Claude score + title (optional)    ──queue──▶ /api/decide  ──▶ data/decisions.json
  ffmpeg render → clip-store release
```

## Setup (all doable from a phone browser)

1. **Twitch keys:** dev.twitch.tv/console → Register Your Application (OAuth redirect `http://localhost`, category Other, client type Confidential) → copy the Client ID and generate a Client Secret.
2. **GitHub secrets** (repo → Settings → Secrets and variables → Actions):
   `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, and optionally `ANTHROPIC_API_KEY` (scores and titles; without it, clips use the Twitch title and nothing is filtered).
3. **GitHub token for the app:** Settings → Developer settings → Fine-grained tokens → repo `clipline` only → Contents: Read and write, Actions: Read and write.
4. **Netlify:** Add new site → Import from GitHub → `clipline` (build settings come from `netlify.toml`). Environment variables:
   `GITHUB_TOKEN` (step 3), `GITHUB_REPO` = `thomasleegh/clipline`, `APP_PIN` = any PIN you choose.
5. **Streamers:** edit `config.yml` → set the campaign streamer's Twitch login. Layout `blur` works for any stream; `stack` needs the facecam box.
6. Open the Netlify URL on your phone → Share → Add to Home Screen → enter PIN → **Run now**.

## Daily loop
Open app → watch → edit caption if needed → **Post ↗** → pick TikTok / YouTube → paste caption (it's already copied) → submit the post link to the Whop campaign.

## Notes
- Each run takes about 5–15 min. Every 4h it uses ~1,500 of the 2,000 free Actions minutes a month on a private repo. Change the cron in `.github/workflows/worker.yml` if needed.
- Skipped clip files are deleted on the next run; posted ones after `keep_days`.
- Read each Whop campaign's rules (watermarks, required tags, allowed platforms) and put the required hashtags in `config.yml`.
