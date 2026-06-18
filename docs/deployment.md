# Deployment Guide

## The War Room — Deployment Options

The dashboard (`ui/dashboard.html`) is **100% static** — no backend, no API, no build step. Any static host works.

## Options Tested

### 1. GitHub Pages ❌
- **Problem:** Needs admin access on the repo to enable Pages in Settings
- **The fix:** Ask Simran to go to repo Settings → Pages → Source: `main` branch, `/docs` folder
- **Would give:** `https://vib3withsimran.github.io/The-war-room/`
- **Best option if you can get 30s of Simran's time**

### 2. Netlify CLI ⚠️
- **Problem:** `npx netlify-cli deploy --prod --dir=ui` timed out waiting for browser OAuth
- **The fix:** If someone creates a Netlify account + personal access token:
  ```bash
  netlify deploy --prod --dir=ui --auth $NETLIFY_AUTH_TOKEN
  ```
- **Would give:** `https://random-name.netlify.app`

### 3. Surge.sh ⚠️
- **Problem:** Needs email/password on first use
- **The fix:** Create a free Surge account, then:
  ```bash
  npx surge --domain the-war-room.surge.sh ./ui
  ```
- **Would give:** `https://the-war-room.surge.sh`

### 4. Local Tunnel ✅ (works now)
- **How:** `python -m http.server 8000` + `npx localtunnel --port 8000`
- **Got:** `https://late-tools-shop.loca.lt`
- **Problem:** Shows interstitial page, URL dies when terminal closes, slow

## Recommendation

**Best effort-to-impact:** Ask Simran to enable GitHub Pages. Takes 30 seconds, gives a permanent URL that stays up forever.
