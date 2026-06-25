# Claude CLI — Multi-Endpoint Edition

A terminal-based AI coding agent with full local filesystem access — read, write, edit, and search files, run shell commands, and manage projects, all from a chat-style CLI.

This edition speaks **three different API wire formats** out of the box, so it's not locked to one provider:

| API type     | Works with                                                                 |
|--------------|------------------------------------------------------------------------------|
| `openai`     | Any OpenAI-compatible `chat/completions` endpoint — OpenRouter, OpenAI, Azure OpenAI, Groq, Together, Ollama, vLLM, LM Studio, and most third-party gateways |
| `anthropic`  | Claude's Messages API — official `api.anthropic.com` or any Anthropic-compatible proxy |
| `gemini`     | Google's public Generative Language API (`generateContent` / `streamGenerateContent`) |

Default (when nothing else is configured): **`deepseek/deepseek-v4-flash:free`** via OpenRouter.

> ⚠️ Free-tier pricing and model availability change often. Check [openrouter.ai/models](https://openrouter.ai/models) (filter by "Free") before relying on any free model.

## Choosing an API type

Two ways, same as picking it manually or letting the CLI figure it out:

### 1. Smart / auto (default)

Leave `API_TYPE=auto` (or just don't set it). On startup the CLI:

1. Checks your base URL for known fingerprints (`anthropic.com`, `openrouter.ai`, `generativelanguage.googleapis.com`, …)
2. Checks your API key's prefix (`sk-ant-` → Anthropic, `AIza` → Gemini, `sk-or-`/`sk-` → OpenAI-compatible)
3. If still unsure, does a quick, harmless probe against the endpoint's `/models` route
4. Falls back to `openai` (the most broadly compatible convention) if nothing matched

It prints what it picked and why, e.g.:
```
[smart-detect] → anthropic  (base URL contains anthropic.com)
```

### 2. Manual

Force it explicitly — at launch or mid-session:

```bash
python claude_cli.py --api-type anthropic
```
```
/apitype gemini
```

Switching API type clears conversation history, since the three formats aren't interchangeable mid-conversation.

## Features

- 🌐 **Three backends, one CLI** — OpenAI-compatible, Anthropic, and Gemini, with auto-detect or manual override
- 🗂️ **Filesystem tools** — read, write, create, edit, move, delete files; list and search directories
- 🖥️ **Shell access** — run build steps, tests, git commands, linters directly from chat
- 🔁 **Multi-turn agent loop** — the model can chain multiple tool calls per turn until the task is done
- 📡 **Streaming output** — responses print token-by-token as they're generated
- 🧠 **Adjustable reasoning effort** — translated into each provider's native param (`reasoning.effort` / `thinking.budget_tokens` / `thinkingConfig`), with automatic fallback if an endpoint doesn't support it
- 📁 **Project switching** — point the agent at any folder mid-session
- ✍️ **Multi-line input** — paste or write longer prompts with a simple `"""` block

## Requirements

- Python 3.10+
- An API key for whichever provider(s) you'll use

## Installation

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
pip install -r requirements.txt
```

You only need the SDK for the type(s) you actually use (they're lazy-imported), but installing all three from `requirements.txt` is the simplest way to keep the option open.

## Configuration

The CLI reads everything from environment variables — **nothing is hardcoded in source**.

| Variable        | Required | Default                  | Description                                          |
|------------------|----------|----------------------------|--------------------------------------------------------|
| `API_TYPE`      | No       | `auto`                    | `auto` / `openai` / `anthropic` / `gemini`            |
| `API_KEY`       | ✅ Yes   | —                          | Your API key for the chosen provider                  |
| `API_BASE_URL`  | No       | per-type default (below)  | Override the endpoint URL                              |
| `API_MODEL`     | No       | per-type default (below)  | Model ID                                               |
| `API_EFFORT`    | No       | `high`                    | `none/minimal/low/medium/high/xhigh/max`               |

### Per-type defaults (used when you don't set `API_BASE_URL` / `API_MODEL`)

| Type        | Default base URL                              | Default model                       |
|-------------|--------------------------------------------------|---------------------------------------|
| `openai`    | `https://openrouter.ai/api/v1`                   | `deepseek/deepseek-v4-flash:free`    |
| `anthropic` | `https://api.anthropic.com`                      | `claude-sonnet-4-6`                  |
| `gemini`    | `https://generativelanguage.googleapis.com`      | `gemini-2.5-flash`                   |

### Setup

```bash
cp .env.example .env
# edit .env with your real key
export $(grep -v '^#' .env | xargs)
python claude_cli.py
```

## Usage

```bash
python claude_cli.py
python claude_cli.py --api-type anthropic --model claude-sonnet-4-6
python claude_cli.py --api-type gemini --model gemini-2.5-flash
python claude_cli.py --base-url https://my-proxy.example.com --api-key sk-... 
python claude_cli.py --project ~/code/my-app
```

| Flag          | Description                                          |
|---------------|--------------------------------------------------------|
| `--api-type`  | `auto` / `openai` / `anthropic` / `gemini`             |
| `--model`     | Override the model ID for this session                |
| `--project`   | Open a project directory on startup                    |
| `--api-key`   | Override the API key for this session                  |
| `--base-url`  | Override the base URL for this session                 |
| `--effort`    | Reasoning effort: `none/minimal/low/medium/high/xhigh/max` |

### In-session commands

| Command            | Description                                            |
|----------------------|------------------------------------------------------|
| `/help`             | Show the command list                                  |
| `/clear`            | Wipe conversation history                               |
| `/model <id>`       | Switch model                                            |
| `/effort <level>`   | Change reasoning effort                                 |
| `/apitype <type>`   | Switch API type (`auto`/`openai`/`anthropic`/`gemini`) — clears history |
| `/endpoint <url>`   | Change the base URL                                     |
| `/key <key>`        | Change the API key                                      |
| `/project <path>`   | Change working directory                                |
| `/exit`             | Quit                                                    |

### Multi-line input

Type `"""` on its own line to open a block, write as many lines as you want, then type `"""` again to submit.

### Available tools (used automatically by the model)

`read_file` · `write_file` · `create_file` · `edit_file` · `list_directory` · `create_directory` · `delete_file` · `move_file` · `run_shell` · `search_files`

## Compatibility notes

- **`gemini`** targets the public Generative Language API (`generativelanguage.googleapis.com`). Vertex AI's enterprise endpoint uses a different URL shape (project/location/publisher) and isn't supported directly.
- **Reasoning effort** is translated into whatever native parameter each provider uses. If an endpoint doesn't support it, the CLI catches the error once, disables it for the rest of the session, and tells you.
- Local/self-hosted OpenAI-compatible servers (Ollama, LM Studio, vLLM) generally work with `--api-type openai`, but tool-calling support varies by server and model — if tools aren't being invoked, check that your local server actually supports function calling.

## Security notes

- **Never commit `.env` or any file containing a real API key.** This repo's `.gitignore` already excludes `.env`.
- If a key is ever pasted into a chat, doc, or commit by mistake, **rotate it immediately** from your provider's dashboard — leaked keys can be scraped and used within minutes.
- `run_shell` and `delete_file` give the model real, unsandboxed access to your machine. Only point `--project` at directories you're comfortable letting an LLM modify, and review tool calls as they print before trusting the output blindly.

## Before publishing this repo

- [ ] Confirm no API key appears anywhere in the code or git history (`git log -p | grep -iE "sk-or-v1|sk-ant-|AIza"`)
- [ ] Add a `LICENSE` file (MIT is a common default for tools like this)
- [ ] Fill in the repo's **About** section on GitHub: short description + topics (e.g. `cli`, `llm-agent`, `openrouter`, `anthropic`, `gemini`, `ai-coding-assistant`, `multi-provider`)
- [ ] Enable **Settings → Code security → Secret scanning** and **Push protection** on the repo
- [ ] Enable **Dependabot alerts** (Settings → Code security)
- [ ] Double-check `.gitignore` covers `.env`, `__pycache__/`, `*.pyc`, and your virtualenv folder

## License

MIT — see `LICENSE`.

## Contributing

Issues and PRs welcome. Please don't include real API keys or `.env` files in any PR diffs or screenshots.
