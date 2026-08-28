"""Package a finished run into a single downloadable archive."""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

from core.logging import get_logger
from core.paths import RunWorkspace, slugify

logger = get_logger(__name__)

# Never ship the runner's virtualenv or caches: they are large, machine-specific,
# and not part of the deliverable.
EXCLUDED_DIRS = frozenset({".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", ".ruff_cache"})


def _includable(path: Path) -> bool:
    return path.is_file() and not EXCLUDED_DIRS.intersection(path.parts)


def zip_workspace(
    workspace: RunWorkspace,
    project_name: str | None = None,
    *,
    include_artifacts: bool = True,
) -> Path | None:
    """Bundle generated source, tests and documents. Returns the archive path,
    or None when the run produced nothing worth packaging."""
    if not workspace.source.is_dir():
        logger.warning("Nothing to package: %s does not exist", workspace.source)
        return None

    safe_name = slugify(project_name or "project", fallback="project")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = workspace.root / f"{safe_name}_{timestamp}.zip"

    sections: list[tuple[Path, str]] = [(workspace.source, safe_name)]
    if workspace.tests.is_dir():
        sections.append((workspace.tests, f"{safe_name}/tests"))
    if include_artifacts and workspace.artifacts.is_dir():
        sections.append((workspace.artifacts, f"{safe_name}/docs"))

    count = 0
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, prefix in sections:
            for path in sorted(root.rglob("*")):
                if not _includable(path):
                    continue
                zf.write(path, f"{prefix}/{path.relative_to(root).as_posix()}")
                count += 1

    if count == 0:
        archive.unlink(missing_ok=True)
        logger.warning("Nothing to package for run %s", workspace.run_id)
        return None

    logger.info("Packaged %d files into %s", count, archive.name)
    return archive
