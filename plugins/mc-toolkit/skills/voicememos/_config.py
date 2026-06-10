"""Local customization layer for the voicememos skill.

A generic public skill should hardcode nothing personal. This resolver lets every
script read a setting from, in priority order:

  1. $CLAUDE_PLUGIN_OPTION_<KEY>   — Claude Code plugin userConfig (marketplace installs)
  2. $<KEY>                        — plain environment variable
  3. <KEY>= line in ~/.claude/.env — personal dotenv (also where secrets live)
  4. config.local.json             — gitignored, next to this file (your overrides)
  5. the default passed by the caller

Non-secret prefs (output dir, language, interpreter paths, the unknown-speaker
label) belong in config.local.json or env. Secrets (HF_TOKEN, ASSEMBLYAI_API_KEY,
ELEVENLABS_API_KEY) stay in ~/.claude/.env and are read by the individual scripts.

To customize: `cp config.example.json config.local.json` and edit. config.local.json
is gitignored — your values never reach the public repo.
"""
import os
import json
import functools

HERE = os.path.dirname(os.path.abspath(__file__))


@functools.lru_cache(maxsize=1)
def _local():
    try:
        with open(os.path.join(HERE, "config.local.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


@functools.lru_cache(maxsize=1)
def _dotenv():
    out = {}
    try:
        for line in open(os.path.expanduser("~/.claude/.env")):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def cfg(key, default=None, expand=False):
    """Resolve a setting by priority. expand=True runs os.path.expanduser on it."""
    val = os.environ.get("CLAUDE_PLUGIN_OPTION_" + key)
    if val is None:
        val = os.environ.get(key)
    if val is None:
        val = _dotenv().get(key)
    if val is None:
        val = _local().get(key)
    if val is None:
        val = default
    if expand and isinstance(val, str):
        val = os.path.expanduser(val)
    return val
