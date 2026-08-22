"""End-to-end smoke test.

Launches the server over stdio exactly like Cursor does, then exercises every
tool against a small throwaway folder whose names contain spaces and commas.

    python test_smoke.py
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
SERVER = HERE / "wiztree_mcp.py"

failures = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global failures
    if not condition:
        failures += 1
    print(f"{'PASS' if condition else 'FAIL'}  {label}")
    if not condition and detail:
        indented = "\n      ".join(detail.splitlines())
        print(f"      {indented}")


def make_fixture() -> str:
    base = Path(tempfile.mkdtemp(prefix="wiztree mcp test-"))
    (base / "media").mkdir()
    (base / "logs" / "old").mkdir(parents=True)
    (base / "media" / "clip.mp4").write_bytes(b"\x07" * (3 * 1024 * 1024))
    (base / "media" / "photo.jpg").write_bytes(b"\x03" * (512 * 1024))
    (base / "logs" / "app.log").write_bytes(b"\x01" * (64 * 1024))
    (base / "logs" / "old" / "app.1.log").write_bytes(b"\x01" * (128 * 1024))
    (base / "readme, with comma.txt").write_text("hello\n", encoding="utf-8")
    (base / "payload").write_bytes(b"\x09" * (256 * 1024))
    # A real duplicate of clip.mp4...
    (base / "clip copy.mp4").write_bytes(b"\x07" * (3 * 1024 * 1024))
    # ...and two decoys: identical size, different content. Size alone must not
    # be enough to call them duplicates.
    (base / "logs" / "decoy-a.bin").write_bytes(b"\x02" * (2 * 1024 * 1024))
    (base / "logs" / "old" / "decoy-b.bin").write_bytes(b"\x04" * (2 * 1024 * 1024))
    return str(base)


async def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else make_fixture()
    cache = Path(tempfile.gettempdir()) / "wiztree-mcp-smoke-cache"
    cache.mkdir(parents=True, exist_ok=True)
    print(f"fixture : {target}")
    print(f"cache   : {cache}\n")

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER)],
        env={**os.environ, "WIZTREE_MCP_CACHE": str(cache)},
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async def call(name: str, **arguments: object) -> tuple[bool, str]:
                result = await session.call_tool(name, arguments or {})
                body = "\n".join(
                    block.text if block.type == "text" else f"<{block.type}>"
                    for block in result.content
                )
                return bool(result.is_error), body

            tools = (await session.list_tools()).tools
            print("tools   : " + ", ".join(t.name for t in tools) + "\n")
            check("16 tools registered", len(tools) == 16, f"got {len(tools)}")

            is_error, body = await call("wiztree_info")
            check("wiztree_info finds the executable", not is_error and "WizTree executable" in body, body)
            print(body + "\n")

            is_error, body = await call("wiztree_list_drives")
            check("wiztree_list_drives lists C:", not is_error and "C:" in body, body)

            is_error, scan = await call("wiztree_scan", path=target, refresh=True)
            check("wiztree_scan on a path containing spaces", not is_error and "Total size" in scan, scan)
            check("wiztree_scan sees the media folder", "media" in scan, scan)
            print(scan + "\n")

            _, cached = await call("wiztree_scan", path=target, top_level_limit=3)
            check("second scan reuses the snapshot", re.search(r"cached \d+s ago", cached) is not None, cached)

            _, top = await call("wiztree_top_files", path=target, limit=5)
            # clip.mp4 and its duplicate tie at 3 MB, so only assert on size and type.
            check(
                "wiztree_top_files ranks the 3 MB mp4 first",
                re.search(r"\| 1 \| 3\.00 MB .*\.mp4 \|", top) is not None,
                top,
            )
            print(top + "\n")

            # Models routinely invent the argument name from the sentence
            # ("scan drive D") instead of reading the schema.
            is_error, aliased = await call("wiztree_scan", drive=target, top_level_limit=1)
            check(
                'a call using "drive" instead of "path" still works',
                not is_error and "Total size" in aliased,
                aliased,
            )
            is_error, aliased = await call("wiztree_top_files", folder=target, limit=1)
            check(
                'a call using "folder" instead of "path" still works',
                not is_error and ".mp4" in aliased,
                aliased,
            )
            _, schema_only = await call("wiztree_scan", path=target, top_level_limit=1)
            check(
                "the canonical name keeps working",
                "Total size" in schema_only,
                schema_only,
            )

            _, filtered = await call("wiztree_top_files", path=target, extensions=["log"], limit=10)
            check(
                "extension filter keeps only .log",
                "app.log" in filtered and "clip.mp4" not in filtered,
                filtered,
            )

            _, min_size = await call("wiztree_top_files", path=target, min_size_mb=1)
            check(
                "min_size_mb drops small files",
                "clip.mp4" in min_size and "photo.jpg" not in min_size,
                min_size,
            )

            _, no_ext = await call("wiztree_top_files", path=target, limit=10)
            check(
                "extension-less names lose WizTree's trailing dot",
                "\\payload |" in no_ext and "\\payload. |" not in no_ext,
                no_ext,
            )

            _, folders = await call("wiztree_top_folders", path=target, limit=10)
            check(
                "wiztree_top_folders lists media and logs",
                "media" in folders and "logs" in folders,
                folders,
            )
            check(
                "the scan root is not listed as one of its own folders",
                f"| {target}\\ |" not in folders,
                folders,
            )

            _, depth = await call("wiztree_top_folders", path=target, max_depth=1)
            check("max_depth=1 hides logs\\old", "logs\\old" not in depth, depth)

            _, drill = await call("wiztree_folder_breakdown", path=str(Path(target) / "logs"))
            check(
                "wiztree_folder_breakdown drills into logs",
                "app.log" in drill and "old" in drill,
                drill,
            )
            print(drill + "\n")

            _, types = await call("wiztree_file_types", path=target)
            check("wiztree_file_types puts .mp4 on top", "| 1 | .mp4 |" in types, types)
            check("extension-less files land in their own bucket", "(no extension)" in types, types)
            print(types + "\n")

            _, glob = await call("wiztree_search", path=target, pattern="*.log")
            check(
                "wiztree_search glob finds both logs",
                "app.log" in glob and "app.1.log" in glob,
                glob,
            )

            _, bare = await call("wiztree_search", path=target, pattern="photo")
            check("wiztree_search bare word is a substring match", "photo.jpg" in bare, bare)

            _, comma = await call("wiztree_search", path=target, pattern="*comma*")
            check("CSV parser survives a comma in a file name", "readme, with comma.txt" in comma, comma)

            _, rx = await call("wiztree_search", path=target, pattern=r"^app\.\d+\.log$", regex=True)
            check("wiztree_search regex mode", "app.1.log" in rx, rx)

            is_error, dups = await call("wiztree_duplicates", path=target)
            check(
                "wiztree_duplicates pairs clip.mp4 with its copy",
                not is_error and "clip copy.mp4" in dups and "clip.mp4" in dups,
                dups,
            )
            check(
                "same size but different content is not a duplicate",
                "decoy" not in dups,
                dups,
            )
            print(dups + "\n")

            _, full = await call("wiztree_duplicates", path=target, verify="full")
            check(
                "verify=full agrees with the sample pass",
                "clip copy.mp4" in full and "decoy" not in full and "[sample]" not in full,
                full,
            )

            _, big_only = await call("wiztree_duplicates", path=target, min_size_mb=100)
            check(
                "min_size_mb excludes everything below the threshold",
                "No duplicates found" in big_only,
                big_only,
            )

            is_error, treemap = await call("wiztree_treemap", path=target, width=400, height=300)
            check("wiztree_treemap writes a PNG", not is_error and ".png (" in treemap, treemap)
            print(treemap + "\n")

            is_error, image = await call(
                "wiztree_treemap", path=target, width=300, height=200, return_image=True
            )
            check("wiztree_treemap can return the image inline", not is_error and "<image>" in image, image)

            is_error, exported = await call(
                "wiztree_export_csv",
                path=target,
                output_path=str(cache / "manual-export.csv"),
                sort_by=1,
                include_folders=False,
            )
            check("wiztree_export_csv writes the file", not is_error and "Exported" in exported, exported)

            # --- live GUI coupling -------------------------------------------------
            # The user may well have WizTree open while this runs, so accept
            # either answer - just not a crash or an empty one.
            is_error, gui_before = await call("wiztree_gui_state")
            already_open = "No WizTree window is open" not in gui_before
            check(
                "wiztree_gui_state reports whether a window is open",
                not is_error
                and ("WizTree window (pid" in gui_before or "No WizTree window is open" in gui_before),
                gui_before,
            )
            if already_open:
                print("note: a WizTree window was already open, reported as:\n" + gui_before + "\n")

            is_error, opened = await call("wiztree_open_gui", path=target)
            check("wiztree_open_gui launches a window", not is_error and "Opened WizTree" in opened, opened)
            gui_pid = re.search(r"pid (\d+)", opened)
            try:
                await asyncio.sleep(6)
                _, gui_live = await call("wiztree_gui_state")
                check(
                    "wiztree_gui_state reads the target out of the live window",
                    target in gui_live,
                    gui_live,
                )
                check(
                    "wiztree_gui_state reads the drive selector",
                    "Drive selector" in gui_live or "Available" in gui_live,
                    gui_live,
                )
                print(gui_live + "\n")
            finally:
                if gui_pid:
                    subprocess.run(
                        ["taskkill", "/PID", gui_pid.group(1), "/T", "/F"], capture_output=True
                    )

            # --- importing a CSV produced outside this server ----------------------
            handover = cache / "handover.csv"
            _, _ = await call("wiztree_export_csv", path=target, output_path=str(handover))
            is_error, imported = await call("wiztree_import_csv", csv_path=str(handover))
            check(
                "wiztree_import_csv registers an external CSV",
                not is_error and "Imported" in imported and target in imported,
                imported,
            )
            _, from_import = await call("wiztree_top_files", path=target, limit=3)
            check(
                "the imported snapshot is queryable",
                ".mp4" in from_import,
                from_import,
            )

            _, scans = await call("wiztree_list_scans")
            check("wiztree_list_scans shows the snapshot", "|" in scans, scans)

            is_error, bad = await call("wiztree_top_files", path="Q:\\nope\\nowhere")
            check("missing path returns a tool error, not a crash", is_error, bad)

            _, cleared = await call("wiztree_clear_cache")
            check(
                "clearing the cache does not delete an imported CSV",
                handover.exists(),
                f"{handover} was deleted",
            )
            check(
                "wiztree_clear_cache empties the cache",
                re.search(r"Removed \d+ cached snapshot", cleared) is not None,
                cleared,
            )

    print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
