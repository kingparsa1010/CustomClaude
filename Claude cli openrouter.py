#!/usr/bin/env python3
"""
Claude CLI — Multi-Endpoint Edition
─────────────────────────────────────
Interactive terminal coding agent with full local filesystem access.
Works against THREE different API wire formats:

    • openai     — OpenAI-compatible chat/completions (OpenRouter, OpenAI,
                    Azure OpenAI, Groq, Together, Ollama, vLLM, LM Studio, …)
    • anthropic  — Claude Messages API (api.anthropic.com or any
                    Anthropic-compatible proxy)
    • gemini     — Google's Generative Language API (generateContent /
                    streamGenerateContent)

Pick the API type two ways:
    1. Smart / auto  — the CLI inspects your base URL and key and guesses
                        (falls back to a tiny live probe if it can't tell).
    2. Manual        — force it with --api-type or the /apitype command.

Usage:
    python claude_cli.py                              # auto-detect, start chat
    python claude_cli.py --api-type anthropic          # force a type
    python claude_cli.py --model <id> --base-url <url> # point anywhere
    python claude_cli.py --project <path>              # open a project folder

In-session commands:
    /help                show command list
    /clear               wipe conversation history
    /model <id>          switch model
    /effort <level>      reasoning effort: none/low/medium/high/xhigh/max
    /apitype <type>      switch API type: auto/openai/anthropic/gemini
                          (clears history — wire formats aren't compatible)
    /endpoint <url>      change the base URL
    /key <key>           change the API key
    /project <path>      change working directory
    /exit                quit

Multi-line input:
    Type  \"\"\"  on its own line to open a block.
    Type  \"\"\"  again to close and submit.

Requires (install what you need; lazy-imported so you don't need all three):
    pip install openai anthropic requests
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ── Global mutable session state ──────────────────────────────────────────────
cfg = {
    "api_type": os.getenv("API_TYPE", "auto"),   # auto | openai | anthropic | gemini
    "api_key":  os.getenv("API_KEY", ""),
    "base_url": os.getenv("API_BASE_URL", ""),
    "model":    os.getenv("API_MODEL", ""),
    "effort":   os.getenv("API_EFFORT", "high"),
    "cwd":      os.getcwd(),
}

EFFORT_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

# Sensible per-type defaults, applied to whatever the user hasn't set explicitly.
TYPE_DEFAULTS = {
    "openai":    {"base_url": "https://openrouter.ai/api/v1",             "model": "deepseek/deepseek-v4-flash:free"},
    "anthropic": {"base_url": "https://api.anthropic.com",                 "model": "claude-sonnet-4-6"},
    "gemini":    {"base_url": "https://generativelanguage.googleapis.com", "model": "gemini-2.5-flash"},
}

MAX_TOKENS = 24000

# Token budgets used when translating the generic /effort level into each
# provider's native "thinking budget" parameter. Calibrated so there's always
# headroom left in MAX_TOKENS for the actual answer after reasoning.
EFFORT_TO_BUDGET = {
    "low":    2000,
    "medium": 6000,
    "high":   12000,
    "xhigh":  18000,
    "max":    18000,
}

DIVIDER = "─" * 60


# ── Tool definitions (one neutral spec, converted per-provider) ──────────────
NEUTRAL_TOOLS: list[dict] = [
    {
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
    {
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
    {
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
    {
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
    {
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
    {
        "name": "create_directory",
        "description": "Create a directory and any missing parent directories.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "delete_file",
        "description": "Permanently delete a file. Use with care.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
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
    {
        "name": "run_shell",
        "description": (
            "Run a shell command in the current working directory. "
            "Returns stdout, stderr, and the exit code. "
            "Use for build steps, tests, git, package managers, linters, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute (passed to bash -c)"},
                "timeout": {"type": "number", "description": "Max seconds to wait before killing the process (default: 30)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "search_files",
        "description": (
            "Search for a text pattern (plain text or regex) across files in a directory. "
            "Returns matching lines with filename and line number, like grep -rn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern":        {"type": "string", "description": "Text or regex pattern to search for"},
                "path":           {"type": "string", "description": "Root directory to search in (default: project cwd)"},
                "glob":           {"type": "string", "description": "File filter glob, e.g. '*.py', '*.ts' (default: all files)"},
                "case_sensitive": {"type": "boolean", "description": "Case-sensitive match (default: true)"},
                "max_results":    {"type": "integer", "description": "Maximum number of matching lines to return (default: 50)"},
            },
            "required": ["pattern"],
        },
    },
]


def to_openai_tools(specs: list[dict]) -> list[dict]:
    return [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
        for t in specs
    ]


def to_anthropic_tools(specs: list[dict]) -> list[dict]:
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
        for t in specs
    ]


def to_gemini_tools(specs: list[dict]) -> list[dict]:
    return [{
        "functionDeclarations": [
            {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
            for t in specs
        ]
    }]


# ── Tool executor (provider-agnostic — same logic regardless of API type) ────
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
                    command, shell=True, executable="/bin/bash", cwd=cfg["cwd"],
                    capture_output=True, text=True, timeout=timeout,
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
            pattern     = inputs["pattern"]
            root        = resolve(inputs.get("path", cfg["cwd"]))
            glob_pat    = inputs.get("glob", "*")
            sensitive   = inputs.get("case_sensitive", True)
            max_results = int(inputs.get("max_results", 50))

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


def print_tool_call(name: str, args_text: str) -> None:
    preview = args_text if len(args_text) <= 120 else args_text[:117] + "…"
    print(f"\n  🔧 {name}({preview})")


def print_tool_result(result: str) -> None:
    lines = result.splitlines()
    for i, line in enumerate(lines[:6]):
        prefix = "  ✔  " if i == 0 else "     "
        print(f"{prefix}{line}")
    if len(lines) > 6:
        print(f"     … ({len(lines)} lines total)")


# ── Auto-detection ("smart" mode) ─────────────────────────────────────────────
def probe_endpoint(base_url: str, api_key: str) -> str | None:
    """Best-effort live check: try a cheap GET against each provider's
    models-listing convention and see which one a server recognizes.
    Never raises — returns None if nothing conclusive comes back."""
    try:
        import requests
    except ImportError:
        return None

    base = base_url.rstrip("/")
    attempts = [
        ("openai",    f"{base}/models",                  {"Authorization": f"Bearer {api_key}"} if api_key else {}),
        ("anthropic", f"{base}/v1/models",                {"x-api-key": api_key, "anthropic-version": "2023-06-01"} if api_key else {}),
    ]
    for kind, url, headers in attempts:
        try:
            r = requests.get(url, headers=headers, timeout=4)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and ("data" in data or "models" in data):
                    return kind
        except Exception:
            continue
    return None


def detect_api_type(base_url: str, api_key: str) -> tuple[str, str]:
    """Returns (api_type, human-readable reason)."""
    b = (base_url or "").lower()
    k = api_key or ""

    url_hints = [
        ("anthropic.com",                     "anthropic", "base URL contains anthropic.com"),
        ("aerolink",                           "anthropic", "recognized Anthropic-compatible proxy (aerolink)"),
        ("generativelanguage.googleapis.com",  "gemini",    "Google Generative Language API URL"),
        ("aiplatform.googleapis.com",          "gemini",    "Google Vertex AI URL"),
        ("openrouter.ai",                      "openai",    "OpenRouter (OpenAI-compatible) URL"),
        ("azure.com",                          "openai",    "Azure OpenAI (OpenAI-compatible) URL"),
        ("openai.com",                         "openai",    "OpenAI URL"),
    ]
    for needle, kind, why in url_hints:
        if needle in b:
            return kind, why

    if k.startswith("sk-ant-"):
        return "anthropic", "API key prefix 'sk-ant-'"
    if k.startswith("AIza"):
        return "gemini", "API key prefix 'AIza' (Google API key)"
    if k.startswith("sk-or-"):
        return "openai", "API key prefix 'sk-or-' (OpenRouter)"
    if k.startswith("sk-"):
        return "openai", "API key prefix 'sk-' (OpenAI-style)"

    if base_url:
        probed = probe_endpoint(base_url, api_key)
        if probed:
            return probed, "live probe of the endpoint's /models route"

    return "openai", "no signal found — defaulting to OpenAI-compatible (override with --api-type or /apitype)"


# ── Adapters ───────────────────────────────────────────────────────────────────
class BaseAdapter:
    label = "base"

    def __init__(self):
        self.history: list = []

    def clear(self) -> None:
        self.history = []

    def send(self, user_msg: str) -> None:
        raise NotImplementedError


class OpenAIAdapter(BaseAdapter):
    """OpenAI-compatible chat/completions: OpenRouter, OpenAI, Azure OpenAI,
    Groq, Together, Ollama, vLLM, LM Studio, and most third-party gateways."""
    label = "OpenAI-compatible"

    def __init__(self):
        super().__init__()
        self._effort_supported = True

    def send(self, user_msg: str) -> None:
        from openai import OpenAI

        client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
        self.history.append({"role": "user", "content": user_msg})
        tools = to_openai_tools(NEUTRAL_TOOLS)

        while True:
            print("\nClaude: ", end="", flush=True)
            full_text = ""
            tool_calls_acc: dict[int, dict] = {}

            base_kwargs = dict(
                model=cfg["model"],
                max_tokens=MAX_TOKENS,
                messages=[{"role": "system", "content": build_system()}] + self.history,
                tools=tools,
                stream=True,
                extra_headers={"HTTP-Referer": "https://github.com/", "X-Title": "Claude CLI (Multi-Endpoint)"},
            )

            stream = None
            if self._effort_supported and cfg.get("effort", "high") != "none":
                try:
                    stream = client.chat.completions.create(
                        **base_kwargs, extra_body={"reasoning": {"effort": cfg["effort"]}}
                    )
                except Exception:
                    self._effort_supported = False
                    print("  (note: endpoint rejected the reasoning/effort param — disabling it for this session)")
            if stream is None:
                stream = client.chat.completions.create(**base_kwargs)

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
                print()

            ordered = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]

            assistant_msg: dict = {"role": "assistant", "content": full_text or None}
            if ordered:
                assistant_msg["tool_calls"] = [
                    {"id": t["id"], "type": "function", "function": {"name": t["name"], "arguments": t["arguments"]}}
                    for t in ordered
                ]
            self.history.append(assistant_msg)

            if not ordered:
                print()
                break

            for t in ordered:
                print_tool_call(t["name"], t["arguments"])
                try:
                    parsed_args = json.loads(t["arguments"]) if t["arguments"] else {}
                except json.JSONDecodeError:
                    parsed_args = {}
                result = run_tool(t["name"], parsed_args)
                print_tool_result(result)
                self.history.append({"role": "tool", "tool_call_id": t["id"], "content": result})


class AnthropicAdapter(BaseAdapter):
    """Claude Messages API — official api.anthropic.com or any
    Anthropic-compatible proxy (e.g. a custom gateway speaking the same schema)."""
    label = "Anthropic"

    def __init__(self):
        super().__init__()
        self._thinking_supported = True

    def send(self, user_msg: str) -> None:
        from anthropic import Anthropic

        client = Anthropic(api_key=cfg["api_key"], base_url=cfg["base_url"] or None)
        self.history.append({"role": "user", "content": user_msg})
        tools = to_anthropic_tools(NEUTRAL_TOOLS)

        while True:
            print("\nClaude: ", end="", flush=True)
            had_text = False

            stream_kwargs = dict(
                model=cfg["model"],
                max_tokens=MAX_TOKENS,
                system=build_system(),
                tools=tools,
                messages=self.history,
            )

            effort = cfg.get("effort", "high")
            budget = EFFORT_TO_BUDGET.get(effort)
            final = None
            if self._thinking_supported and budget:
                try:
                    with client.messages.stream(**stream_kwargs, thinking={"type": "enabled", "budget_tokens": budget}) as s:
                        for chunk in s.text_stream:
                            print(chunk, end="", flush=True)
                            had_text = True
                        final = s.get_final_message()
                except Exception:
                    self._thinking_supported = False
                    print("  (note: endpoint rejected the thinking/budget param — disabling it for this session)")
                    had_text = False

            if final is None:
                with client.messages.stream(**stream_kwargs) as s:
                    for chunk in s.text_stream:
                        print(chunk, end="", flush=True)
                        had_text = True
                    final = s.get_final_message()

            if had_text:
                print()

            self.history.append({"role": "assistant", "content": final.content})

            tool_blocks = [b for b in final.content if b.type == "tool_use"]
            if not tool_blocks:
                print()
                break

            tool_results = []
            for block in tool_blocks:
                print_tool_call(block.name, json.dumps(block.input, ensure_ascii=False))
                result = run_tool(block.name, block.input)
                print_tool_result(result)
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})

            self.history.append({"role": "user", "content": tool_results})


class GeminiAdapter(BaseAdapter):
    """Google's public Generative Language API (generateContent /
    streamGenerateContent). Targets generativelanguage.googleapis.com — not
    the Vertex AI enterprise path, which uses a different URL shape."""
    label = "Gemini"

    def __init__(self):
        super().__init__()
        self._thinking_supported = True

    def send(self, user_msg: str) -> None:
        import requests

        self.history.append({"role": "user", "parts": [{"text": user_msg}]})
        tools = to_gemini_tools(NEUTRAL_TOOLS)
        base = cfg["base_url"].rstrip("/")
        url = f"{base}/v1beta/models/{cfg['model']}:streamGenerateContent?alt=sse"
        headers = {"Content-Type": "application/json", "x-goog-api-key": cfg["api_key"]}

        while True:
            print("\nClaude: ", end="", flush=True)
            full_text = ""
            function_calls: list[dict] = []

            body = {
                "contents": self.history,
                "systemInstruction": {"parts": [{"text": build_system()}]},
                "tools": tools,
                "generationConfig": {"maxOutputTokens": MAX_TOKENS},
            }

            effort = cfg.get("effort", "high")
            budget = EFFORT_TO_BUDGET.get(effort)
            resp = None
            if self._thinking_supported and budget:
                body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": budget}
                try:
                    resp = requests.post(url, headers=headers, json=body, stream=True, timeout=120)
                    resp.raise_for_status()
                except Exception:
                    self._thinking_supported = False
                    body["generationConfig"].pop("thinkingConfig", None)
                    print("  (note: endpoint rejected the thinkingConfig param — disabling it for this session)")
                    resp = None

            if resp is None:
                resp = requests.post(url, headers=headers, json=body, stream=True, timeout=120)
                resp.raise_for_status()

            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line or not raw_line.startswith("data:"):
                    continue
                payload = raw_line[len("data:"):].strip()
                if not payload:
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                candidates = event.get("candidates") or []
                if not candidates:
                    continue
                for part in candidates[0].get("content", {}).get("parts", []):
                    if "text" in part:
                        print(part["text"], end="", flush=True)
                        full_text += part["text"]
                    elif "functionCall" in part:
                        function_calls.append(part["functionCall"])

            if full_text:
                print()

            model_parts = []
            if full_text:
                model_parts.append({"text": full_text})
            for fc in function_calls:
                model_parts.append({"functionCall": fc})
            self.history.append({"role": "model", "parts": model_parts})

            if not function_calls:
                print()
                break

            response_parts = []
            for fc in function_calls:
                name = fc.get("name", "")
                args = fc.get("args", {}) or {}
                print_tool_call(name, json.dumps(args, ensure_ascii=False))
                result = run_tool(name, args)
                print_tool_result(result)
                response_parts.append({"functionResponse": {"name": name, "response": {"result": result}}})

            self.history.append({"role": "user", "parts": response_parts})


ADAPTERS = {"openai": OpenAIAdapter, "anthropic": AnthropicAdapter, "gemini": GeminiAdapter}


def make_adapter(api_type: str) -> BaseAdapter:
    return ADAPTERS[api_type]()


# ── Input helpers ─────────────────────────────────────────────────────────────
PROMPT = "\nYou: "
ML_TAG = '"""'


def read_input() -> str:
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
def handle_command(raw: str, adapter: BaseAdapter) -> tuple[bool, BaseAdapter]:
    """Returns (was_command, current_adapter) — adapter may be a brand-new
    instance if /apitype switched the wire format."""
    stripped = raw.strip()
    if not stripped.startswith("/"):
        return False, adapter

    parts = stripped.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/exit", "/quit"):
        print("Bye!")
        sys.exit(0)

    elif cmd == "/clear":
        adapter.clear()
        print("  [history cleared]")
        return True, adapter

    elif cmd == "/effort":
        if arg.strip() not in EFFORT_LEVELS:
            print(f"  Valid levels: {', '.join(EFFORT_LEVELS)}")
        else:
            cfg["effort"] = arg.strip()
            print(f"  [effort → {cfg['effort']}]")
        return True, adapter

    elif cmd == "/apitype":
        choice = arg.strip().lower()
        if choice == "auto" or not choice:
            resolved, reason = detect_api_type(cfg["base_url"], cfg["api_key"])
            print(f"  [auto-detected → {resolved}  ({reason})]")
            choice = resolved
        if choice not in ADAPTERS:
            print(f"  Valid types: auto, {', '.join(ADAPTERS)}")
            return True, adapter
        cfg["api_type"] = choice
        defaults = TYPE_DEFAULTS[choice]
        if not cfg["base_url"]:
            cfg["base_url"] = defaults["base_url"]
        if not cfg["model"]:
            cfg["model"] = defaults["model"]
        print(f"  [api type → {choice} — history cleared]")
        return True, make_adapter(choice)

    elif cmd == "/endpoint":
        if not arg:
            print(f"  Current endpoint: {cfg['base_url']}")
        else:
            cfg["base_url"] = arg.strip()
            print(f"  [endpoint → {cfg['base_url']}]")
        return True, adapter

    elif cmd == "/key":
        if not arg:
            print("  API key is set." if cfg["api_key"] else "  No API key set.")
        else:
            cfg["api_key"] = arg.strip()
            print("  [API key updated]")
        return True, adapter

    elif cmd == "/help":
        print(f"""
{DIVIDER}
  Commands
{DIVIDER}
  /clear              Wipe conversation history
  /model <id>         Switch model            (current: {cfg['model']})
  /effort <level>     Reasoning effort         (current: {cfg['effort']})
  /apitype <type>     auto/openai/anthropic/gemini (current: {cfg['api_type']})
  /endpoint <url>     Change base URL          (current: {cfg['base_url']})
  /key <key>          Change API key
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
        return True, adapter

    elif cmd == "/model":
        if not arg:
            print(f"  Current model: {cfg['model']}")
        else:
            cfg["model"] = arg.strip()
            print(f"  [model → {cfg['model']}]")
        return True, adapter

    elif cmd == "/project":
        if not arg:
            print(f"  Current project: {cfg['cwd']}")
            return True, adapter
        p = Path(arg.strip()).expanduser().resolve()
        if not p.is_dir():
            print(f"  ERROR: not a directory → {p}")
            return True, adapter
        cfg["cwd"] = str(p)
        os.chdir(p)
        print(f"  [project → {p}]")
        adapter.send(f"I've opened the project at {p}. Please list its contents.")
        return True, adapter

    else:
        print(f"  Unknown command: {cmd}  (type /help for the list)")
        return True, adapter


# ── Banner ────────────────────────────────────────────────────────────────────
def print_banner(adapter: BaseAdapter) -> None:
    key_preview = (cfg["api_key"][:10] + "…") if cfg["api_key"] else "(none)"
    print(f"""
{DIVIDER}
  Claude CLI — Multi-Endpoint Edition
  API type: {cfg['api_type']}  →  {adapter.label}
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
        description="Claude CLI — interactive terminal agent with filesystem access, works against OpenAI-compatible, Anthropic, or Gemini endpoints",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--api-type", default=None, choices=["auto", "openai", "anthropic", "gemini"],
                         help="Which API wire format to speak (default: auto-detect)")
    parser.add_argument("--model",    default=None, help="Model ID")
    parser.add_argument("--project",  default=None, help="Project directory to open on start")
    parser.add_argument("--api-key",  default=None, help="API key")
    parser.add_argument("--base-url", default=None, help="API base URL")
    parser.add_argument("--effort",   default=None, choices=list(EFFORT_LEVELS), help="Reasoning effort")
    args = parser.parse_args()

    if args.api_type:
        cfg["api_type"] = args.api_type
    if args.api_key:
        cfg["api_key"] = args.api_key
    if args.base_url:
        cfg["base_url"] = args.base_url
    if args.model:
        cfg["model"] = args.model
    if args.effort:
        cfg["effort"] = args.effort
    if args.project:
        p = Path(args.project).expanduser().resolve()
        if p.is_dir():
            cfg["cwd"] = str(p)
            os.chdir(p)
        else:
            print(f"WARNING: --project path not found: {p}")

    # Resolve "auto" into a concrete api_type before anything else needs it.
    if cfg["api_type"] == "auto":
        resolved, reason = detect_api_type(cfg["base_url"], cfg["api_key"])
        print(f"[smart-detect] → {resolved}  ({reason})")
        cfg["api_type"] = resolved

    if cfg["api_type"] not in ADAPTERS:
        print(f"ERROR: unknown api type '{cfg['api_type']}'. Valid: auto, {', '.join(ADAPTERS)}")
        sys.exit(1)

    # Fill in anything the user didn't set with that type's defaults.
    defaults = TYPE_DEFAULTS[cfg["api_type"]]
    if not cfg["base_url"]:
        cfg["base_url"] = defaults["base_url"]
    if not cfg["model"]:
        cfg["model"] = defaults["model"]

    if not cfg["api_key"]:
        print("ERROR: No API key found.")
        print("Set it with:  export API_KEY=\"...\"   (or --api-key)")
        sys.exit(1)

    adapter = make_adapter(cfg["api_type"])
    print_banner(adapter)

    while True:
        try:
            raw = read_input()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            sys.exit(0)

        if not raw.strip():
            continue

        was_cmd, adapter = handle_command(raw, adapter)
        if was_cmd:
            continue

        try:
            adapter.send(raw)
        except Exception as exc:
            print(f"\n  API error: {exc}\n")


if __name__ == "__main__":
    main()
