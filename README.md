# Clipline

Twitch top clips → vertical 9:16 Shorts with burned-in captions → review queue on your phone → share sheet to TikTok / Shorts / Reels.

Everything runs on GitHub: Actions does the work every 4 hours and publishes the review app to GitHub Pages. $0/month.

```
GitHub Actions (every 4h)                        GitHub Pages (phone app)
  Twitch Get Clips (top by views)                  Post ↗ = iOS share sheet (caption copied)
  yt-dlp download → faster-whisper captions        Skip / Undo / Run now
  Claude score + title (optional)                  writes data/decisions.json via GitHub API
  ffmpeg render → clip-store release  ──deploy──▶  (token saved on your phone only)
```

## Setup
1. **Repo secrets** (Settings → Secrets and variables → Actions): `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, optional `ANTHROPIC_API_KEY`.
2. **Phone token:** github.com/settings/personal-access-tokens/new → repo `clipline` only → Contents: Read and write, Actions: Read and write.
3. **Streamer:** edit `config.yml` → the Whop campaign streamer's Twitch login.
4. Open `https://thomasleegh.github.io/clipline/` → Share → Add to Home Screen → paste the token → **Run now**.

## Daily loop
Open app → watch → edit caption → **Post ↗** → TikTok / YouTube → paste caption → submit the post link on Whop.

## Notes
- Each run takes about 5–15 min. Change the schedule in `.github/workflows/worker.yml`.
- Skipped clip files are deleted on the next run; posted ones after `keep_days`.
- Put any hashtags a Whop campaign requires in `config.yml`.
