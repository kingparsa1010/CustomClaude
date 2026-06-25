#!/usr/bin/env python3
"""
Claude CLI — OpenRouter Edition
────────────────────────────────
Same interactive terminal agent as the original, but talks to OpenRouter's
OpenAI-compatible API instead of Anthropic's Messages API. Default model is
DeepSeek V4 Flash (free tier on OpenRouter).

Usage:
    python claude_cli_openrouter.py                          # start chat
    python claude_cli_openrouter.py --model <id>             # pick a model
    python claude_cli_openrouter.py --project <path>         # open a project folder

In-session commands:
    /help                show command list
    /clear               wipe conversation history
    /model <id>          switch model mid-session
    /effort <level>      set reasoning effort (low/medium/high/xhigh/max/none)
    /project <path>      change working directory
    /exit                quit

Multi-line input:
    Type  \"\"\"  on its own line to open a block.
    Type  \"\"\"  again to close and submit.

Requires:
    pip install openai
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from openai import OpenAI

# ── Default config ────────────────────────────────────────────────────────────
# API key always comes from the environment — never hardcode it here,
# especially before pushing this file to a public repo.
DEFAULT_API_KEY  = os.getenv("OPENROUTER_API_KEY", "")
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL    = "deepseek/deepseek-v4-flash:free"   # free tier — check openrouter.ai/models if it's gone
MAX_TOKENS       = 8096

# ── Mutable session state (plain dict so helpers can mutate it) ───────────────
cfg = {
    "api_key":  os.getenv("OPENROUTER_API_KEY", DEFAULT_API_KEY),
    "base_url": os.getenv("OPENROUTER_BASE_URL", DEFAULT_BASE_URL),
    "model":    os.getenv("OPENROUTER_MODEL",     DEFAULT_MODEL),
    "effort":   "high",
    "cwd":      os.getcwd(),
}


def make_client() -> OpenAI:
    return OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])


# ── Tool definitions (OpenAI / OpenRouter function-calling format) ────────────
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full text contents of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file (absolute or relative to cwd)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write (or overwrite) a file with new content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string"},
                    "content": {"type": "string", "description": "Full content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a brand-new file. Fails if the file already exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string"},
                    "content": {"type": "string", "description": "Initial file content (may be empty string)"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Edit a file by replacing the first occurrence of old_str with new_str. "
                "old_str must match the file content exactly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string"},
                    "old_str": {"type": "string", "description": "Exact text to find"},
                    "new_str": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and folders inside a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":      {"type": "string", "description": "Directory to list (default: current project dir)"},
                    "recursive": {"type": "boolean", "description": "Recurse into sub-folders (default false)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": "Create a directory and any missing parent directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Permanently delete a file. Use with care.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "Move or rename a file or directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "Source path"},
                    "dst": {"type": "string", "description": "Destination path"},
                },
                "required": ["src", "dst"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "Run a shell command in the current working directory. "
                "Returns stdout, stderr, and the exit code. "
                "Use for build steps, tests, git, package managers, linters, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute (passed to bash -c)",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Max seconds to wait before killing the process (default: 30)",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "Search for a text pattern (plain text or regex) across files in a directory. "
                "Returns matching lines with filename and line number, like grep -rn."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Text or regex pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "Root directory to search in (default: project cwd)",
                    },
                    "glob": {
                        "type": "string",
                        "description": "File filter glob, e.g. '*.py', '*.ts' (default: all files)",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Case-sensitive match (default: true)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matching lines to return (default: 50)",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]


# ── Tool executor (unchanged logic from the Anthropic version) ───────────────
def resolve(raw: str) -> Path:
    """Resolve a path relative to the current project cwd."""
    p = Path(raw)
    if not p.is_absolute():
        p = Path(cfg["cwd"]) / p
    return p.resolve()


def run_tool(name: str, inputs: dict) -> str:
    try:
        if name == "read_file":
            p = resolve(inputs["path"])
            if not p.exists():
                return f"ERROR: file not found → {p}"
            return p.read_text(encoding="utf-8", errors="replace")

        elif name == "write_file":
            p = resolve(inputs["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(inputs["content"], encoding="utf-8")
            return f"OK: wrote {len(inputs['content'])} chars to {p}"

        elif name == "create_file":
            p = resolve(inputs["path"])
            if p.exists():
                return f"ERROR: file already exists → {p}"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(inputs["content"], encoding="utf-8")
            return f"OK: created {p}"

        elif name == "edit_file":
            p = resolve(inputs["path"])
            if not p.exists():
                return f"ERROR: file not found → {p}"
            text = p.read_text(encoding="utf-8", errors="replace")
            old = inputs["old_str"]
            if old not in text:
                return f"ERROR: old_str not found in {p}"
            p.write_text(text.replace(old, inputs["new_str"], 1), encoding="utf-8")
            return f"OK: edited {p}"

        elif name == "list_directory":
            p = resolve(inputs.get("path", cfg["cwd"]))
            if not p.exists():
                return f"ERROR: directory not found → {p}"
            recurse = inputs.get("recursive", False)
            glob = p.rglob("*") if recurse else p.iterdir()
            lines = []
            for entry in sorted(glob):
                rel  = entry.relative_to(p)
                icon = "📁" if entry.is_dir() else "📄"
                lines.append(f"{icon} {rel}")
            return "\n".join(lines) if lines else "(empty directory)"

        elif name == "create_directory":
            p = resolve(inputs["path"])
            p.mkdir(parents=True, exist_ok=True)
            return f"OK: directory created → {p}"

        elif name == "delete_file":
            p = resolve(inputs["path"])
            if not p.exists():
                return f"ERROR: not found → {p}"
            if p.is_dir():
                import shutil
                shutil.rmtree(p)
                return f"OK: deleted directory → {p}"
            p.unlink()
            return f"OK: deleted → {p}"

        elif name == "move_file":
            src = resolve(inputs["src"])
            dst = resolve(inputs["dst"])
            if not src.exists():
                return f"ERROR: source not found → {src}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
            return f"OK: moved {src} → {dst}"

        elif name == "run_shell":
            command = inputs["command"]
            timeout = float(inputs.get("timeout", 30))
            try:
                proc = subprocess.run(
                    command,
                    shell=True,
                    executable="/bin/bash",
                    cwd=cfg["cwd"],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                parts: list[str] = []
                if proc.stdout.strip():
                    parts.append(f"STDOUT:\n{proc.stdout.rstrip()}")
                if proc.stderr.strip():
                    parts.append(f"STDERR:\n{proc.stderr.rstrip()}")
                parts.append(f"Exit code: {proc.returncode}")
                return "\n\n".join(parts)
            except subprocess.TimeoutExpired:
                return f"ERROR: command timed out after {timeout}s — {command!r}"
            except FileNotFoundError:
                return "ERROR: /bin/bash not found; try a simpler command"

        elif name == "search_files":
            pattern      = inputs["pattern"]
            root         = resolve(inputs.get("path", cfg["cwd"]))
            glob_pat     = inputs.get("glob", "*")
            sensitive    = inputs.get("case_sensitive", True)
            max_results  = int(inputs.get("max_results", 50))

            if not root.is_dir():
                return f"ERROR: not a directory → {root}"

            flags = 0 if sensitive else re.IGNORECASE
            try:
                regex = re.compile(pattern, flags)
            except re.error as exc:
                return f"ERROR: invalid regex — {exc}"

            hits: list[str] = []
            for fpath in sorted(root.rglob(glob_pat)):
                if not fpath.is_file():
                    continue
                try:
                    text = fpath.read_text(encoding="utf-8", errors="strict")
                except (UnicodeDecodeError, PermissionError):
                    continue
                for lineno, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        rel = fpath.relative_to(root)
                        hits.append(f"{rel}:{lineno}: {line.rstrip()}")
                        if len(hits) >= max_results:
                            hits.append(f"… (truncated — {max_results} results reached)")
                            return "\n".join(hits)

            return "\n".join(hits) if hits else "No matches found."

        else:
            return f"ERROR: unknown tool '{name}'"

    except Exception as exc:
        return f"ERROR: {exc}"


# ── Agent loop ────────────────────────────────────────────────────────────────
DIVIDER = "─" * 60


def build_system() -> str:
    return (
        f"You are a helpful coding assistant with full access to the local filesystem.\n"
        f"Current working directory: {cfg['cwd']}\n\n"
        "When working on a project:\n"
        "  1. List the directory first to understand the structure.\n"
        "  2. Read relevant files before making edits.\n"
        "  3. After making changes, briefly summarise what you did.\n\n"
        "Be precise, concise, and always confirm which files you touched."
    )


def agent_turn(history: list[dict], user_msg: str) -> list[dict]:
    """
    Send a user message and run the full agent loop with streaming, against
    OpenRouter's OpenAI-compatible chat/completions endpoint.

    Text tokens are printed to the terminal as they arrive. Tool-call deltas
    are accumulated by index across chunks (OpenAI-style streaming sends tool
    call names/arguments in fragments), executed locally once the stream
    closes, then fed back as individual `role: tool` messages — repeating
    until the model stops calling tools.
    """
    history.append({"role": "user", "content": user_msg})
    client = make_client()

    while True:
        print("\nClaude: ", end="", flush=True)
        full_text = ""
        tool_calls_acc: dict[int, dict] = {}  # index -> {id, name, arguments}

        stream = client.chat.completions.create(
            model=cfg["model"],
            max_tokens=MAX_TOKENS,
            messages=[{"role": "system", "content": build_system()}] + history,
            tools=TOOLS,
            stream=True,
            extra_body={"reasoning": {"effort": cfg.get("effort", "high")}},
            extra_headers={
                "HTTP-Referer": "https://github.com/",  # optional, for OpenRouter rankings
                "X-Title": "Claude CLI (OpenRouter)",
            },
        )

        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if getattr(delta, "content", None):
                print(delta.content, end="", flush=True)
                full_text += delta.content

            if getattr(delta, "tool_calls", None):
                for tc in delta.tool_calls:
                    slot = tool_calls_acc.setdefault(tc.index, {"id": None, "name": "", "arguments": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            slot["name"] += tc.function.name
                        if tc.function.arguments:
                            slot["arguments"] += tc.function.arguments

        if full_text:
            print()  # newline after streamed text

        ordered = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]

        # ── Append the complete assistant turn to history ─────────────
        assistant_msg: dict = {"role": "assistant", "content": full_text or None}
        if ordered:
            assistant_msg["tool_calls"] = [
                {
                    "id": t["id"],
                    "type": "function",
                    "function": {"name": t["name"], "arguments": t["arguments"]},
                }
                for t in ordered
            ]
        history.append(assistant_msg)

        if not ordered:
            print()  # blank line after reply
            break

        # ── Execute tools and feed results back ───────────────────────
        for t in ordered:
            args_preview = t["arguments"]
            if len(args_preview) > 120:
                args_preview = args_preview[:117] + "…"
            print(f"\n  🔧 {t['name']}({args_preview})")

            try:
                parsed_args = json.loads(t["arguments"]) if t["arguments"] else {}
            except json.JSONDecodeError:
                parsed_args = {}

            result = run_tool(t["name"], parsed_args)

            preview_lines = result.splitlines()
            for i, line in enumerate(preview_lines[:6]):
                prefix = "  ✔  " if i == 0 else "     "
                print(f"{prefix}{line}")
            if len(preview_lines) > 6:
                print(f"     … ({len(preview_lines)} lines total)")

            history.append({
                "role": "tool",
                "tool_call_id": t["id"],
                "content": result,
            })

        # loop for next turn so the model can react to tool results

    return history


# ── Input helpers ─────────────────────────────────────────────────────────────
PROMPT = "\nYou: "
ML_TAG = '"""'


def read_input() -> str:
    """
    Read one user turn from stdin.

    Single-line:  type your message, press Enter.
    Multi-line:   type  \"\"\"  alone on a line to open a block,
                  then  \"\"\"  again to close and submit.
    """
    try:
        first = input(PROMPT).rstrip("\n")
    except (EOFError, KeyboardInterrupt):
        raise

    if first.strip() == ML_TAG:
        print(f'  (multi-line: type {ML_TAG} on its own line to finish)\n')
        lines: list[str] = []
        while True:
            try:
                line = input("  ┆ ")
            except (EOFError, KeyboardInterrupt):
                break
            if line.strip() == ML_TAG:
                break
            lines.append(line)
        return "\n".join(lines)

    return first


# ── Slash-command handler ─────────────────────────────────────────────────────
def handle_command(raw: str, history: list[dict]) -> tuple[bool, list[dict]]:
    """
    Returns (was_command, updated_history).
    If the input is not a slash command, returns (False, history) unchanged.
    """
    stripped = raw.strip()

    if not stripped.startswith("/"):
        return False, history

    parts = stripped.split(None, 1)
    cmd   = parts[0].lower()
    arg   = parts[1] if len(parts) > 1 else ""

    if cmd in ("/exit", "/quit"):
        print("Bye!")
        sys.exit(0)

    elif cmd == "/clear":
        print("  [history cleared]")
        return True, []

    elif cmd == "/effort":
        valid = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
        if arg.strip() not in valid:
            print(f"  Valid levels: {', '.join(valid)}")
        else:
            cfg["effort"] = arg.strip()
            print(f"  [effort → {cfg['effort']}]")
        return True, history

    elif cmd == "/help":
        print(f"""
{DIVIDER}
  Commands
{DIVIDER}
  /clear              Wipe conversation history
  /model <id>         Switch model  (current: {cfg['model']})
  /effort <level>     Reasoning effort  (current: {cfg['effort']})
  /project <path>     Change working directory
  /exit               Quit

  Claude's tools
    read_file / write_file / create_file / edit_file
    list_directory / create_directory / delete_file / move_file
    run_shell      — run bash commands (npm, git, pytest, …)
    search_files   — grep across files by pattern / glob

  Multi-line input
    Type  \"\"\"  on its own line to open a block.
    Type  \"\"\"  again to close and submit.
{DIVIDER}""")
        return True, history

    elif cmd == "/model":
        if not arg:
            print(f"  Current model: {cfg['model']}")
        else:
            cfg["model"] = arg.strip()
            print(f"  [model → {cfg['model']}]")
        return True, history

    elif cmd == "/project":
        if not arg:
            print(f"  Current project: {cfg['cwd']}")
            return True, history
        p = Path(arg.strip()).expanduser().resolve()
        if not p.is_dir():
            print(f"  ERROR: not a directory → {p}")
            return True, history
        cfg["cwd"] = str(p)
        os.chdir(p)
        print(f"  [project → {p}]")
        # Auto-list the project so the model is aware of it
        history = agent_turn(history, f"I've opened the project at {p}. Please list its contents.")
        return True, history

    else:
        print(f"  Unknown command: {cmd}  (type /help for the list)")
        return True, history


# ── Banner ────────────────────────────────────────────────────────────────────
def print_banner() -> None:
    key_preview = cfg["api_key"][:12] + "…" if cfg["api_key"] else "(none)"
    print(f"""
{DIVIDER}
  Claude CLI — OpenRouter Edition
  Model   : {cfg['model']}
  Effort  : {cfg['effort']}
  Endpoint: {cfg['base_url']}
  API key : {key_preview}
  CWD     : {cfg['cwd']}
{DIVIDER}
  /help for commands  |  \"\"\"  for multi-line input
{DIVIDER}""")


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Claude CLI (OpenRouter Edition) — interactive terminal agent with filesystem access",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--model",   default=None, help="Model ID (e.g. deepseek/deepseek-v4-flash:free)")
    parser.add_argument("--project", default=None, help="Project directory to open on start")
    parser.add_argument("--api-key", default=None, help="Override API key")
    parser.add_argument("--base-url",default=None, help="Override API base URL")
    parser.add_argument("--effort",  default=None, help="Reasoning effort: low/medium/high/xhigh/max/none")
    args = parser.parse_args()

    if args.model:
        cfg["model"] = args.model
    if args.api_key:
        cfg["api_key"] = args.api_key
    if args.base_url:
        cfg["base_url"] = args.base_url
    if args.effort:
        cfg["effort"] = args.effort
    if args.project:
        p = Path(args.project).expanduser().resolve()
        if p.is_dir():
            cfg["cwd"] = str(p)
            os.chdir(p)
        else:
            print(f"WARNING: --project path not found: {p}")

    if not cfg["api_key"]:
        print("ERROR: No API key found.")
        print("Set it with:  export OPENROUTER_API_KEY=\"sk-or-v1-...\"")
        print("Get a key at: https://openrouter.ai/keys")
        sys.exit(1)

    print_banner()

    history: list[dict] = []

    while True:
        try:
            raw = read_input()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            sys.exit(0)

        if not raw.strip():
            continue

        was_cmd, history = handle_command(raw, history)
        if was_cmd:
            continue

        try:
            history = agent_turn(history, raw)
        except Exception as exc:
            print(f"\n  API error: {exc}\n")


if __name__ == "__main__":
    main()
