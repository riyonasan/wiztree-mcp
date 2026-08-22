"""Manual run against a real folder or drive, handy to eyeball output and timings.

    python test_manual.py "C:\\Users\\me\\AppData\\Local"
    python test_manual.py C:                 (slow without elevation)
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
SERVER = HERE / "wiztree_mcp.py"


async def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LOCALAPPDATA", "C:\\Windows")
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=dict(os.environ))

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=None) as session:
            await session.initialize()

            async def call(name: str, **arguments: object) -> None:
                started = time.monotonic()
                result = await session.call_tool(name, arguments)
                body = "\n".join(
                    block.text if block.type == "text" else f"<{block.type}>"
                    for block in result.content
                )
                flag = " [ERROR]" if result.is_error else ""
                print(f"\n===== {name} ({(time.monotonic() - started) * 1000:.0f} ms){flag}\n{body}")

            await call("wiztree_scan", path=target, refresh=True, top_level_limit=8)
            await call("wiztree_top_files", path=target, limit=8)
            await call("wiztree_top_folders", path=target, limit=8, max_depth=2)
            await call("wiztree_file_types", path=target, limit=8)
            await call("wiztree_search", path=target, pattern="*.dll", limit=5)
            await call("wiztree_duplicates", path=target, min_size_mb=20, limit=6)


if __name__ == "__main__":
    asyncio.run(main())
