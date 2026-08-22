"""Register this server with Cursor, without disturbing what is already there.

    python install_mcp.py

Merges a single "wiztree" entry into Cursor's mcp.json, keeping every other
server and every unrelated setting exactly as it was, and writes a timestamped
backup first. Re-running it is harmless: the entry is replaced, not duplicated.

For Claude Code use its own CLI instead - it does the same merge for you:

    claude mcp add wiztree --scope user -- <venv python> <this folder>\\wiztree_mcp.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

ENTRY = {
    "command": str(HERE / ".venv" / "Scripts" / "python.exe"),
    "args": [str(HERE / "wiztree_mcp.py")],
    "env": {"WIZTREE_EXE": str(HERE.parent / "WizTree64.exe")},
}

TARGETS = {
    "cursor": Path.home() / ".cursor" / "mcp.json",
}


def install(client: str) -> bool:
    config = TARGETS[client]
    print(f"\n=== {client}\n    {config}")
    if not config.parent.is_dir():
        print("    SKIP: client not installed (config folder missing)")
        return False

    if config.is_file():
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"    ABORT: existing config is not valid JSON ({exc}). Fix it by hand first.")
            return False
        backup = config.with_name(f"{config.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(config, backup)
        print(f"    backup: {backup.name}")
    else:
        data = {}
        print("    creating a new config file")

    servers = data.setdefault("mcpServers", {})
    before = sorted(servers)
    action = "updated" if "wiztree" in servers else "added"
    servers["wiztree"] = ENTRY
    config.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # Read back, so we never leave a client with a config we have not re-parsed.
    check = json.loads(config.read_text(encoding="utf-8"))
    print(f"    servers before : {before or '(none)'}")
    print(f"    servers after  : {sorted(check['mcpServers'])}   (wiztree {action})")
    print(f"    other keys kept: {sorted(k for k in check if k != 'mcpServers') or '(none)'}")
    return True


def main() -> int:
    which = (sys.argv[1] if len(sys.argv) > 1 else "cursor").lower()
    clients = list(TARGETS) if which in ("both", "all") else [which]
    unknown = [c for c in clients if c not in TARGETS]
    if unknown:
        print(f"Unknown client(s): {', '.join(unknown)}. Choose from: {', '.join(TARGETS)}, both")
        return 2

    for label, path in (
        ("interpreter", Path(ENTRY["command"])),
        ("server", Path(ENTRY["args"][0])),
        ("WizTree", Path(ENTRY["env"]["WIZTREE_EXE"])),
    ):
        state = "OK" if path.is_file() else "MISSING"
        print(f"{label:12}: {state}  {path}")
        if state == "MISSING":
            print("\nFix that first - the client would just fail to start the server.")
            return 1

    touched = sum(install(client) for client in clients)
    print(
        f"\nDone ({touched} client(s) updated). Restart the client completely for it to take "
        "effect: MCP servers are connected when the client starts, never reloaded live."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
