"""
core/tools.py — Tool definitions for JARVIS autonomous agent.

These schemas are sent to the LLM so it can decide which actions to take.
Execution happens on the laptop via laptop_agent.py over WebSocket.
"""
from __future__ import annotations

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "shell_execute",
            "description": (
                "Execute a PowerShell command on the user's laptop. "
                "Use for file operations, running programs, system info, etc. "
                "Always prefer safe, reversible commands."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "PowerShell command to run",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open a URL in the browser on the user's laptop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL including https://"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click an element on the current browser page using a CSS selector or visible text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector, or use 'text=버튼이름' to click by visible text",
                    }
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "Type text into an input field on the current browser page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector of the input field"},
                    "text": {"type": "string", "description": "Text to type"},
                },
                "required": ["selector", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_get_text",
            "description": "Get the visible text content of the current browser page.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "Take a screenshot of the current browser page and return description of what's visible.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the content of a file on the laptop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative file path"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file on the laptop (creates or overwrites).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and folders in a directory on the laptop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (default: Desktop)"},
                },
                "required": [],
            },
        },
    },
]

TOOL_NAMES = {t["function"]["name"] for t in TOOLS}
