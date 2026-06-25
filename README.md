# Claude CLI — OpenRouter Edition

A terminal-based AI coding agent with full local filesystem access — read, write, edit, and search files, run shell commands, and manage projects, all from a chat-style CLI. This edition talks to [OpenRouter](https://openrouter.ai), giving you access to DeepSeek V4, Claude, GPT, Gemini, and 300+ other models through a single OpenAI-compatible API.

Default model: **`deepseek/deepseek-v4-flash:free`** — DeepSeek's free-tier model on OpenRouter.

> ⚠️ Free-tier pricing and availability change frequently. Check [openrouter.ai/models](https://openrouter.ai/models) (filter by "Free") for what's currently free before relying on it.

## Features

- 🗂️ **Filesystem tools** — read, write, create, edit, move, delete files; list and search directories
- 🖥️ **Shell access** — run build steps, tests, git commands, linters directly from chat
- 🔁 **Multi-turn agent loop** — the model can chain multiple tool calls per turn until the task is done
- 📡 **Streaming output** — responses print token-by-token as they're generated
- 🧠 **Adjustable reasoning effort** — tune how much the model "thinks" before answering
- 📁 **Project switching** — point the agent at any folder mid-session
- ✍️ **Multi-line input** — paste or write longer prompts with a simple `"""` block

## Requirements

- Python 3.10+
- An [OpenRouter](https://openrouter.ai) account and API key

## Installation

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
pip install -r requirements.txt
```

`requirements.txt`:
```
openai>=1.0
```

## Configuration

The CLI reads everything from environment variables — **nothing is hardcoded in source**.

| Variable               | Required | Default                              | Description                                  |
|-------------------------|----------|---------------------------------------|-----------------------------------------------|
| `OPENROUTER_API_KEY`   | ✅ Yes   | —                                      | Your OpenRouter API key                       |
| `OPENROUTER_MODEL`     | No       | `deepseek/deepseek-v4-flash:free`     | Model slug to use                             |
| `OPENROUTER_BASE_URL`  | No       | `https://openrouter.ai/api/v1`        | API base URL (only change for proxies/mirrors)|

### Setup

1. Get a key at [openrouter.ai/keys](https://openrouter.ai/keys)
2. Copy the example env file and fill it in:
   ```bash
   cp .env.example .env
   ```
3. Load it into your shell before running:
   ```bash
   export $(grep -v '^#' .env | xargs)
   ```
   or just `export OPENROUTER_API_KEY="sk-or-v1-..."` directly.

## Usage

```bash
python claude_cli_openrouter.py
python claude_cli_openrouter.py --model deepseek/deepseek-v4-pro
python claude_cli_openrouter.py --project ~/code/my-app
python claude_cli_openrouter.py --effort high
```

| Flag          | Description                                  |
|---------------|-----------------------------------------------|
| `--model`     | Override the model ID for this session        |
| `--project`   | Open a project directory on startup           |
| `--api-key`   | Override the API key for this session         |
| `--base-url`  | Override the base URL for this session        |
| `--effort`    | Reasoning effort: `none/low/medium/high/xhigh/max` |

### In-session commands

| Command            | Description                                  |
|----------------------|-----------------------------------------------|
| `/help`             | Show the command list                         |
| `/clear`            | Wipe conversation history                     |
| `/model <id>`       | Switch model mid-session                      |
| `/effort <level>`   | Change reasoning effort                       |
| `/project <path>`   | Change working directory                      |
| `/exit`             | Quit                                          |

### Multi-line input

Type `"""` on its own line to open a block, write as many lines as you want, then type `"""` again to submit.

### Available tools (used automatically by the model)

`read_file` · `write_file` · `create_file` · `edit_file` · `list_directory` · `create_directory` · `delete_file` · `move_file` · `run_shell` · `search_files`

## Security notes

- **Never commit `.env` or any file containing a real API key.** This repo's `.gitignore` already excludes `.env`.
- If a key is ever pasted into a chat, doc, or commit by mistake, **rotate it immediately** from your [OpenRouter dashboard](https://openrouter.ai/keys) — leaked keys can be scraped and used within minutes.
- `run_shell` and `delete_file` give the model real, unsandboxed access to your machine. Only point `--project` at directories you're comfortable letting an LLM modify, and review tool calls as they print before trusting the output blindly.

## Before publishing this repo

- [ ] Confirm no API key appears anywhere in the code or git history (`git log -p | grep -i "sk-or-v1"`)
- [ ] Add a `LICENSE` file (MIT is a common default for tools like this)
- [ ] Fill in the repo's **About** section on GitHub: short description + topics (e.g. `cli`, `llm-agent`, `openrouter`, `deepseek`, `ai-coding-assistant`)
- [ ] Enable **Settings → Code security → Secret scanning** and **Push protection** on the repo
- [ ] Enable **Dependabot alerts** (Settings → Code security)
- [ ] Double-check `.gitignore` covers `.env`, `__pycache__/`, `*.pyc`, and your virtualenv folder

## License

MIT — see `LICENSE`.

## Contributing

Issues and PRs welcome. Please don't include real API keys or `.env` files in any PR diffs or screenshots.
