# mc-toolkit

Open-source [Claude Code](https://code.claude.com) skills — the public cut of
[Maciej Cielecki](https://github.com/cielecki)'s personal toolkit. Generic, reusable
tools with a **local customization layer** so you can adapt them to your machine and
language without forking the code.

Packaged as a Claude Code plugin: every skill invokes as `mc-toolkit:<name>`.

## Skills

| Skill | What it does |
|-------|--------------|
| **voicememos** | Sync Apple Voice Memos into local, speaker-labeled transcripts — fully on-device (mlx-whisper + silero-VAD + pyannote diarization + voiceprint identification), with optional cloud engines for messy audio. |

_(More moving over from the private toolkit as they're generalized.)_

## Install

**As a user (marketplace):**

```bash
claude plugin marketplace add cielecki/mc-toolkit
claude plugin install mc-toolkit@mc-toolkit
```

Marketplace installs are **cached and version-pinned** — to pull a new release, run
`claude plugin update mc-toolkit` (editing a cached copy does nothing).

**For local development (live-edit):** clone the repo and symlink the plugin folder
into your skills dir — it's then discovered in place, no caching, edits are live next
session:

```bash
git clone https://github.com/cielecki/mc-toolkit.git
ln -sfn "$PWD/mc-toolkit/plugins/mc-toolkit" ~/.claude/skills/mc-toolkit
```

## Customization — without forking

Skills read every personal setting (paths, language, interpreter locations, labels)
through a small resolver, in priority order:

1. `$CLAUDE_PLUGIN_OPTION_<KEY>` — Claude Code plugin `userConfig`
2. `$<KEY>` — environment variable
3. a `<KEY>=` line in `~/.claude/.env` — also where API keys/secrets live
4. `config.local.json` next to the skill — **gitignored**, your overrides
5. the skill's built-in default

So the published code stays generic. To adapt a skill, copy its `config.example.json`
to `config.local.json` and edit:

```bash
cd ~/.claude/skills/mc-toolkit/skills/voicememos
cp config.example.json config.local.json   # then edit your paths/language
```

`config.local.json` is gitignored — your values never reach the repo. Secrets
(`HF_TOKEN`, `ASSEMBLYAI_API_KEY`, …) go in `~/.claude/.env`, never in the repo.

## Privacy & safety

These skills touch personal data (recordings, voiceprints) but the repo ships **none**
of it. The `.gitignore` hard-blocks runtime output, snapshots, enrolled voiceprints
(`*.npy` biometric data), audio, and any `.env`/token files. Sensitive audio is
processed **fully on-device** by default; cloud engines are opt-in and clearly marked.

## License

MIT © Maciej Cielecki
