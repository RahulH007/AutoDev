"""The code manifest: the shared index of what the developer agent wrote.

Keys are slugged service names, which is what the directories on disk are named.
Previously the manifest was keyed by whatever the model called the service that
run, so QA's file references and the actual paths could disagree.

Shape::

    {
      "backend-api": {
        "display_name": "Backend API",
        "files": [{"file_path": "app/main.py", "description": "...", "language": "python"}]
      }
    }
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from core.paths import slugify

Manifest = dict[str, dict[str, Any]]


def add_file(
    manifest: Manifest,
    service_name: str,
    file_path: str,
    *,
    description: str = "",
    language: str = "",
) -> str:
    """Record a file, replacing any earlier entry for the same path.

    Replacing rather than skipping matters on a retry: the developer regenerates a
    subset of files, and the newer description is the accurate one.
    """
    slug = slugify(service_name)
    service = manifest.setdefault(slug, {"display_name": service_name, "files": []})
    service["display_name"] = service_name

    entry = {"file_path": file_path, "description": description, "language": language}
    files: list[dict[str, Any]] = service["files"]

    for index, existing in enumerate(files):
        if existing.get("file_path") == file_path:
            files[index] = entry
            return slug

    files.append(entry)
    return slug


def services(manifest: Manifest) -> list[str]:
    return sorted(manifest.keys())


def display_name(manifest: Manifest, slug: str) -> str:
    return manifest.get(slug, {}).get("display_name", slug)


def files_for(manifest: Manifest, slug: str) -> list[dict[str, Any]]:
    return list(manifest.get(slug, {}).get("files", []))


def iter_files(manifest: Manifest) -> Iterator[tuple[str, dict[str, Any]]]:
    for slug in services(manifest):
        for entry in files_for(manifest, slug):
            yield slug, entry


def qualified_paths(manifest: Manifest) -> list[str]:
    """``service-slug/file/path.py`` identifiers, the form QA triage returns."""
    return [f"{slug}/{entry['file_path']}" for slug, entry in iter_files(manifest)]


def file_count(manifest: Manifest) -> int:
    return sum(1 for _ in iter_files(manifest))


def summarise(manifest: Manifest) -> str:
    """Compact text rendering for prompts."""
    lines: list[str] = []
    for slug in services(manifest):
        lines.append(f"SERVICE: {display_name(manifest, slug)} (directory: {slug})")
        for entry in files_for(manifest, slug):
            description = entry.get("description", "")
            lines.append(f"  - {slug}/{entry['file_path']}: {description}")
    return "\n".join(lines)
