"""Run the API with a reloader that ignores its own output.

``uvicorn --reload`` watches the whole working tree, and the developer agent
writes generated code into ``runs/``. The watcher sees those files appear, calls
it a source change, and restarts the server -- killing the run that produced
them, which then gets marked as failed on the next startup sweep. The pipeline
cannot finish a single run under a naive reloader.

Watching only the packages that hold our own source fixes it: generated code,
databases, artifacts and caches all live outside them.

Usage::

    python scripts/dev.py             # port 8000
    python scripts/dev.py 9000        # or set API_PORT
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent.parent

# Everything we hand-write. Notably absent: runs/, data/, memory/, outputs/.
SOURCE_DIRS = (
    "agents",
    "core",
    "graph",
    "llm",
    "prompts",
    "schema",
    "server",
    "state",
    "utils",
    "verification",
)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.getenv("API_PORT", "8000"))
    watched = [str(ROOT / name) for name in SOURCE_DIRS if (ROOT / name).is_dir()]

    # Running this file puts scripts/ on sys.path, not the root, so "server.app"
    # would not import. The env var carries the fix into the reloader's child too.
    sys.path.insert(0, str(ROOT))
    os.environ["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(ROOT), os.environ.get("PYTHONPATH")])
    )

    uvicorn.run(
        "server.app:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=port,
        reload=True,
        reload_dirs=watched,
    )


if __name__ == "__main__":
    main()
