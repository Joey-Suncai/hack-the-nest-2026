# ShowMe

**ShowMe points at the next button.** Tell it which app you're in and what you want to do. It looks at your screen, draws a glowing box around the one thing to click next, and says the step out loud. When you do the step, it checks that it worked and moves on to the next one, until you're done.

ShowMe **teaches you; it doesn't do it for you.** It's built for people who find apps confusing and don't know how to ask an AI for help, like grandparents joining their first Zoom call or kids making their first slide deck.

Hack The Nest 2026 · Team NEST-TYSS · Joey Sun · Teresa Wang (University of Chicago)

Built with **Google Gemini** (sees the screen and plans each step) and **ElevenLabs** (real-time voice conversation and spoken steps).

---

## How to use it

1. **Open the app you want to learn**, for example Zoom, PowerPoint, System Settings, or VS Code.
2. **Tell ShowMe what you want to do.** Either:
   - **Talk:** press the **right Option key** once, say your goal ("join a Zoom meeting", "make the text on my screen bigger"), then press right Option again. You can also click the mic button on the panel.
   - **Type:** write your goal in the ShowMe panel and press **Send**.
3. **Follow the pink box.** ShowMe draws a box around the next thing to click and reads the step out loud. The step also appears as a caption on screen.
4. **Do the step yourself.** You don't need to tell ShowMe you did it. It watches for your click or your typing, checks that the step worked, and shows the next box.
   - Clicked the wrong place? The box comes back, and ShowMe looks at the screen again.
   - Got ahead of it? ShowMe looks again about 1.5 seconds after you click and catches up.
5. **Ask anything along the way** with the right Option key, for example "what does this button do?", "why do I need to sign in?", or "I'm stuck". You can interrupt ShowMe while it's talking.
6. **Stop anytime** with the **Stop** button on the panel. When the task is finished, ShowMe says so.

### Tips

- **Be specific:** "join meeting 812 345 6789 in Zoom" works better than "help with Zoom".
- **Keep the app in front:** ShowMe guides the app you're looking at. If you switch apps, it follows you.
- **Use headphones,** or keep the volume low. The voice has no echo cancellation yet, so loud speakers can interrupt it.
- **Record your voice (optional):** click **Record my voice** on the panel. After that, only your voice can interrupt ShowMe, not other people talking nearby.
- **Language:** the panel's language menu switches between English and Chinese.

### What it's good at today

Everyday desktop apps where most steps are clicks:
- **Zoom:** join a meeting, mute or unmute, share the screen.
- **PowerPoint / Keynote:** new slide, theme, picture, transitions, start the slideshow.
- **System Settings:** bigger text, Wi-Fi, notifications.
- **VS Code:** clone a repo, open the terminal, install an extension.

Harder for now: dragging things on a canvas, filling in spreadsheet cells, and large web apps such as cloud consoles.

---

## Run it yourself (macOS)

**You need:** macOS, [uv](https://docs.astral.sh/uv/) (`brew install uv`), and `make`. uv downloads Python 3.12 for you.
You also need a **Gemini API key** and an **ElevenLabs API key**. Without keys, you can still try the mock mode below.

```bash
git clone https://github.com/Joey-Suncai/hack-the-nest-2026.git
cd hack-the-nest-2026
make setup              # installs backend + client, creates .env
```

Open `.env` and fill in at least:

| Variable | What it is |
|---|---|
| `GEMINI_API_KEY` | Google Gemini API key |
| `GEMINI_MODEL` | Default `gemini-3.6-flash` |
| `ELEVENLABS_API_KEY` | ElevenLabs API key (voice) |
| `ELEVENLABS_VOICE_ID` | A voice from the ElevenLabs Voice Library |
| `ELEVENLABS_AGENT_ID` | Real-time voice agent. Create it with the command below |
| `SHOWME_TOKEN` | Optional password between client and backend. Set it if the backend is reachable on your network |

Create the real-time voice agent once. This writes `ELEVENLABS_AGENT_ID` into `.env`:

```bash
cd backend && uv run python scripts/create_agent.py --write-env
```

Start everything:

```bash
make dev                # backend on http://localhost:8000 + the ShowMe client
```

Or start them separately with `make backend` and `make client`.

**No API keys?** `make mock` runs both with fake data, so you can see the panel and the box.

### macOS permissions

The first time you run it, macOS will ask for these. Allow them for the app you launch ShowMe from (Terminal, iTerm, or VS Code), then restart ShowMe:

| Permission | Why |
|---|---|
| **Screen Recording** | To see your screen |
| **Accessibility** | To read the app's buttons (exact boxes), notice your clicks, and use the right Option key from any app |
| **Microphone** | To hear you |

Find them in System Settings → Privacy & Security. ShowMe only logs the time of each click and key press, never what you type.

### Client settings (environment variables)

| Variable | Default | What it does |
|---|---|---|
| `SHOWME_API` | `http://localhost:8000` | Backend address, for example another laptop on the same Wi-Fi |
| `SHOWME_TOKEN` | unset | Must match the backend's `SHOWME_TOKEN` if one is set. The client does **not** read `.env`, so export it in your shell |
| `SHOWME_FILLER` | `0` | `1` = say "Let me look." while ShowMe thinks |
| `SHOWME_AX` | `1` | `0` = don't read the app's buttons, use only the screenshot |
| `SHOWME_LOG` | unset | `1` = detailed logs |

API keys stay on the backend. The client never sees them.

---

## How it works

```
 you ──voice / text──▶  ShowMe client (Mac)  ──screenshot + button list──▶  backend (FastAPI)  ──▶  Gemini
                           │  draws the box, reads the step                     │  plans the next step
                           │  watches clicks & typing                           └──▶  ElevenLabs (voice)
                           └─ checks the step worked, then asks for the next one
```

1. **Listen:** the ElevenLabs real-time agent handles the conversation and works out what you want.
2. **Look:** the client takes a screenshot and reads the app's real buttons through the macOS Accessibility API (name and exact position).
3. **Think:** Gemini gets the screenshot, the button list, your goal and the steps so far. It says what's on screen, what's in the way (a sign-in page, a permission prompt), and the next step. It also names a **check** for that step: something that should appear, disappear, change, or get selected.
4. **Point:** the backend matches Gemini's answer to a real button, so the box lands exactly on it. The client draws it as a click-through overlay and reads the step aloud.
5. **Check:** after you act, the client watches the check every 1.5 seconds and takes another look 1.5 seconds after each click. When the check passes, it moves on. If the screen changed some other way, it asks Gemini again.

Measured during the hackathon:
- 90% of steps correct on 10 apps it had never been tested on.
- About 2.2 seconds per step.
- 81% of steps land on an exact app button rather than a guessed box.

---

## Repository layout

| Path | What's there |
|---|---|
| `client/` | Mac app: PyQt6 panel + overlay, Accessibility reader, click/key watcher, voice ([client/showme](client/showme)) |
| `backend/` | FastAPI server. All Gemini calls go through [backend/app/gemini.py](backend/app/gemini.py); prompts are in [backend/prompts](backend/prompts) |
| `backend/eval/` | Accuracy eval on real screenshots: `cd backend && uv run python eval/eval_boxes.py` |
| `docs/API.md` | Client ↔ backend API contract |
| `pitch/` | Demo plan |

Tests: `cd backend && uv run pytest` and `cd client && uv run pytest`.

## Team docs

- Task board (Google Doc): https://docs.google.com/document/d/1zOFvMK21tq3OHowYNdMt2NikQcE9c1DU3L-RKZY8VN4/edit
- [Idea](docs/IDEA.md) · [Event info](docs/00_比赛信息.md) · [Plan](docs/01_比赛流程规划.md) · [Workflow](docs/02_团队协作工作流.md) · [API](docs/API.md) · [Tasks](docs/TASKS.md)
