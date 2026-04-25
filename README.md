# Privacy-First Discord Voice Transcriber

Self-hosted Discord bot that auto-transcribes voice messages only in channels where you enable it with `/transcribe on`.

## What it does

- Watches for Discord voice-message attachments in enabled channels
- Downloads audio only when needed
- Converts audio in memory with `ffmpeg`
- Transcribes with `faster-whisper`
- Replies to the original message with `Transcription attached as file`
- Uploads a `.txt` transcript file
- Avoids saving raw audio or transcript text to local disk
- Adds local-only assistant commands for summaries, idea capture, server search, Q&A, daily rollups, and reminders

## Privacy model

- Audio is fetched over Discord HTTPS and processed in memory
- Raw audio and transcript bodies are not stored locally
- Logs contain only operational metadata
- Local persistent storage is limited to bot configuration in SQLite

This is privacy-preserving, but not true end-to-end encryption because the bot must access the audio in order to transcribe it.

## Requirements

- Python 3.11+
- `ffmpeg` installed and available on `PATH`
- A Discord bot token in `DISCORD_BOT_TOKEN`
- Discord bot app with:
  - `MESSAGE CONTENT INTENT` enabled in the Developer Portal
  - bot installed in your server with slash-command support

## Install

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Environment

```powershell
$env:DISCORD_BOT_TOKEN="your-token-here"
$env:TRANSCRIBE_MODEL="large-v3-turbo"
$env:FFMPEG_PATH="ffmpeg"
$env:BOT_DB_PATH="bot_state.sqlite3"
$env:MAX_AUDIO_SECONDS="240"
$env:MAX_ATTACHMENT_MB="25"
$env:GLOBAL_CONCURRENCY="1"
$env:LOG_LEVEL="INFO"
$env:HF_HOME=".hf-cache"
$env:REMINDER_POLL_SECONDS="30"
$env:SEARCH_HISTORY_LIMIT_PER_CHANNEL="600"
$env:MAX_TEXT_ATTACHMENT_KB="256"
$env:LOCAL_TIMEZONE="America/Los_Angeles"
```

## Run

```powershell
python -m src.transcriber_bot
```

Or use the helper launcher, which prompts for the token without saving it:

```powershell
.\run_bot.ps1
```

## Slash commands

- `/transcribe on`
- `/transcribe off`
- `/transcribe status`
- `/summarize`
- `/search`
- `/ask`
- `/daily`
- `/remind`
- `/idea add`
- `/idea search`
- `/idea mark`

## Assistant command notes

- `/idea add category content marker`
  - Save an idea into buckets like `business`, `life`, `music`, or anything else you want.
  - Markers are `open`, `starred`, `parked`, and `done`.
- `/idea search category`
  - Find ideas by category, and optionally filter by keyword or marker.
- `/idea mark idea_id marker`
  - Change how an idea is flagged later.
- `/search query`
  - Scans readable server channels plus text attachments like transcript `.txt` files.
- `/ask question`
  - Finds the most relevant notes/transcripts and gives an extractive answer with evidence.
- `/summarize`
  - Summarizes recent channel notes, or summarizes a specific topic across the server.
- `/daily`
  - Summarizes what you added since midnight Pacific time.
- `/remind content hours`
  - Schedules a reminder and tags you when it is due.

## Discord setup checklist

1. Create an application at the Discord Developer Portal.
2. Add a bot user for that application.
3. Under `Bot`, enable `MESSAGE CONTENT INTENT`.
4. Under `OAuth2 > URL Generator`, include:
   - `bot`
   - `applications.commands`
5. Give the bot these permissions at minimum:
   - `View Channels`
   - `Send Messages`
   - `Read Message History`
   - `Attach Files`
6. Open the generated invite URL and add the bot to your server.
7. Start the bot locally with `.\run_bot.ps1`.
8. In your target Discord channel, run `/transcribe on`.

## Notes on quality

- The default model is `large-v3-turbo`, which is practical on CPU and still strong for English transcription.
- If CPU latency is too high, move this same bot to a GPU machine before downgrading the UX.
- If you mostly transcribe one language, a future improvement is a per-channel language override.

## Wispbyte / hosted bot panel

This project can run on a regular Discord bot hosting panel as long as the host supports Python processes, environment variables, and enough RAM/CPU for `faster-whisper`.

Recommended hosted settings:

- Startup command: `python -m src.transcriber_bot`
- Python version: `3.10+`
- Environment variables:
  - `DISCORD_BOT_TOKEN=...`
  - `TRANSCRIBE_MODEL=large-v3-turbo`
  - `FFMPEG_PATH=ffmpeg`
  - `BOT_DB_PATH=bot_state.sqlite3`
  - `MAX_AUDIO_SECONDS=240`
  - `MAX_ATTACHMENT_MB=25`
  - `GLOBAL_CONCURRENCY=1`
  - `LOG_LEVEL=INFO`
  - `HF_HOME=.hf-cache`
  - `REMINDER_POLL_SECONDS=30`
  - `SEARCH_HISTORY_LIMIT_PER_CHANNEL=600`
  - `MAX_TEXT_ATTACHMENT_KB=256`
  - `LOCAL_TIMEZONE=America/Los_Angeles`

Hosted deployment notes:

- The first transcription may be slow because the Whisper model downloads on first use.
- If the host is too weak for `large-v3-turbo`, try `medium` before giving up on the platform.
- If the panel offers a GitHub import, point it at this repo and use the startup command above.
- If the panel offers a package-install step, run `pip install -r requirements.txt`.
- The assistant commands do not use an LLM API key. They rely on local keyword search, extractive summaries, and transcript/file scanning.
