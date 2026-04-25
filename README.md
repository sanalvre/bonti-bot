# Bonti Bot

Personal Discord bot for voice transcriptions, saved ideas, reminders, search, and daily summaries.

## Functionality

- Auto-transcribes voice messages in channels where `/transcribe on` is enabled
- Replies with a `.txt` transcript file
- Saves ideas by category with markers like `open`, `starred`, `parked`, and `done`
- Searches messages and text attachments across the server
- Answers questions by pulling from your notes and transcripts
- Summarizes recent notes or a specific topic
- Builds a daily summary from everything you added since midnight Pacific time
- Sends reminders after a set number of hours

## Requirements

- Python `3.10+`
- `ffmpeg` on `PATH`
- Discord bot token
- Discord bot with:
  - `MESSAGE CONTENT INTENT` enabled
  - `bot` and `applications.commands` scopes
  - permissions for `View Channels`, `Send Messages`, `Read Message History`, and `Attach Files`

## Privacy

- Audio is processed in memory
- Transcript text is not saved locally as files by the bot
- Raw audio is not saved locally by the bot
- Local SQLite is only used for bot state like enabled channels, ideas, and reminders
- The final transcript file is still stored by Discord if the bot posts it there
- This is privacy-conscious, but not end-to-end encrypted, because the bot has to access the message to process it

## Environment

Install:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Set env vars:

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

Run locally:

```powershell
python -m src.transcriber_bot
```

Or:

```powershell
.\run_bot.ps1
```

For Wispbyte:

- startup command: `python -m src.transcriber_bot`
- install command: `pip install -r requirements.txt`
- if `large-v3-turbo` is too heavy, switch `TRANSCRIBE_MODEL` to `medium`

For Docker-style hosts:

- build from the included `Dockerfile`
- the image already installs `ffmpeg`
- keep using the same environment variables
- default container command is already `python -m src.transcriber_bot`

## Commands

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

## Command Notes

- `/idea add`
  - Save an idea into a bucket like `business`, `life`, `music`, or whatever else you want
  - Marker options: `open`, `starred`, `parked`, `done`
- `/idea search`
  - Pull back saved ideas by category
  - You can also filter by keyword or marker
- `/idea mark`
  - Change the marker on a saved idea later
- `/search`
  - Scans readable server channels plus text attachments like transcript files
- `/ask`
  - Gives a local extractive answer based on messages and transcript files
- `/summarize`
  - Summarizes recent notes or a specific topic
- `/daily`
  - Summarizes everything you added since midnight Pacific time
- `/remind`
  - Format is basically `/remind [content] [hours]`
  - Tags you when the reminder is due
