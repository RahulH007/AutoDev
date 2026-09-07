"""The architecture, restated as something a filesystem can answer.

An approved architecture document says what will be built: which services exist,
which folders and files each one must have, which endpoints it exposes, which
data models it owns, which other services it leans on. Until now that was a
description read by a language model and checked by nobody. The developer agent
was handed it, the QA agent formed an opinion about it, and whether the delivered
code matched the design that a human actually approved was never established.

A contract is that document reduced to the claims a machine can settle. It is
derived — never generated: :func:`derive_contract` is a pure function of the
architecture JSON, adds no model call, and invents no requirement the architect
did not write down. If the architecture does not say a service needs a file, the
contract does not either.

What is checked, and what is deliberately not
--------------------------------------------

:func:`check_contract` reports only what a path lookup can decide: a directory
exists, a named file exists, a named service is one the architecture actually
declares. Every one of those is a literal string in the architecture and a yes or
no on disk, with nothing in between to interpret.

Endpoints and data models are carried in the contract but **not** validated, and
that is a deliberate limit rather than an omission. Deciding whether
``POST /api/v1/expenses`` was implemented means searching free-form source for a
path that a router prefix may legitimately have split in two, that a parameter
name may legitimately have respelled, and that a frontend may legitimately only
call rather than serve. Every one of those is an ordinary, correct way to write
the code, and each would be reported as a violation. A gate that cries wolf costs
a developer pass and the tokens to pay for it, so the rule here is the same one
the static gate already follows: report what can be established, and stay silent
about the rest. The data is recorded so a later phase can check it properly.

Violations come back as :class:`~schema.verification_schema.StaticCheck` objects,
the shape the static gate already produces, so they flow into ``static_report``,
into `build_failure_evidence`, and into `agents/attribution.py` — which reads
``StaticCheck.service`` — without any of those three learning a new format.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, Field

from core.logging import get_logger
from core.manifest import Manifest
from core.paths import RunWorkspace, UnsafePathError, safe_join, slugify
from schema.verification_schema import StaticCheck

logger = get_logger(__name__)

CHECK_NAME = "contract"

# A key file whose stem is one of these is where an application is conventionally
# started from. Naming it separately makes a missing entrypoint say so, rather
# than arriving as one indistinguishable missing file among several.
_ENTRYPOINT_STEMS = frozenset({"main", "app", "index", "server"})
_ENTRYPOINT_SUFFIXES = frozenset({".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".go", ".rb"})

# Directories that belong to tooling rather than to the delivered project.
_IGNORED = frozenset({".venv", "venv", "node_modules", "__pycache__", ".git", ".pytest_cache"})


# ── Representation ───────────────────────────────────────────────


class EndpointContract(BaseModel):
    """An endpoint the architecture declares. Recorded, not yet checkable."""

    method: str = ""
    path: str = ""


class ServiceContract(BaseModel):
    """What one service promised, in the architecture's own words.

    ``slug`` is the canonical identity — the same one the manifest is keyed by
    and the same one the workspace names a directory with — so a contract, a
    generated directory and an attributed failure all agree without a second
    identity scheme.
    """

    slug: str
    name: str
    folders: list[str] = Field(default_factory=list)
    key_files: list[str] = Field(default_factory=list)
    # One of key_files, singled out because a missing entrypoint is worth saying
    # plainly. None when the architecture named no file that looks like one.
    entrypoint: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    # Carried for a later phase. See the module docstring for why neither of
    # these is validated here.
    endpoints: list[EndpointContract] = Field(default_factory=list)
    data_models: list[str] = Field(default_factory=list)


class ArchitectureContract(BaseModel):
    """Every service contract in the architecture, in the architecture's order."""

    architecture_style: str = ""
    services: list[ServiceContract] = Field(default_factory=list)

    def service(self, slug: str) -> ServiceContract | None:
        for contract in self.services:
            if contract.slug == slug:
                return contract
        return None

    @property
    def slugs(self) -> list[str]:
        return [contract.slug for contract in self.services]


# ── Derivation ───────────────────────────────────────────────────


def derive_contract(architecture: dict[str, Any] | None) -> ArchitectureContract:
    """Reduce an architecture document to the claims that can be checked.

    Pure, deterministic and read-only: identical input gives an identical
    contract, and the architecture itself is never touched. Every field is
    optional as far as this is concerned — an architecture that names no project
    structure simply yields a contract with nothing to check for that service,
    which is the correct answer rather than a reason to fail.
    """
    document = architecture or {}
    services = document.get("services") or []

    structures = _structures_by_slug(document.get("project_structure") or [])
    declared = {slugify(str(service.get("name") or ""), fallback=""): True for service in services}

    contracts: list[ServiceContract] = []
    for service in services:
        name = str(service.get("name") or "").strip()
        slug = slugify(name, fallback="")
        if not slug:
            # A service the architecture did not name cannot own a directory, so
            # there is nothing here that could be checked against one.
            continue

        structure = structures.get(slug, {})
        key_files = _clean_paths(structure.get("key_files"))

        contracts.append(
            ServiceContract(
                slug=slug,
                name=name,
                folders=_clean_paths(structure.get("folders")),
                key_files=key_files,
                entrypoint=_entrypoint(key_files),
                depends_on=_dependency_slugs(service.get("dependencies"), declared),
                endpoints=_endpoints(service.get("api_endpoints")),
                data_models=_model_names(service.get("data_models")),
            )
        )

    return ArchitectureContract(
        architecture_style=str(document.get("architecture_style") or ""),
        services=contracts,
    )


def _structures_by_slug(structures: list[Any]) -> dict[str, dict[str, Any]]:
    """Index ``project_structure`` by slug so it meets its service.

    The architect names the service twice — once in ``services`` and once in
    ``project_structure`` — and does not always spell it the same way. Slugging
    both sides is what makes them agree, exactly as it does for the manifest and
    the directories on disk.
    """
    indexed: dict[str, dict[str, Any]] = {}
    for structure in structures:
        if not isinstance(structure, dict):
            continue
        slug = slugify(str(structure.get("service_name") or ""), fallback="")
        if slug and slug not in indexed:
            indexed[slug] = structure
    return indexed


def _clean_paths(values: Any) -> list[str]:
    """Relative POSIX paths, de-duplicated, in the order the architect gave them.

    A path that tries to leave its service is dropped rather than recorded: the
    architecture is model-written, and a contract is not a way to smuggle one
    past `safe_join`.
    """
    cleaned: list[str] = []
    for value in values or []:
        path = str(value).replace("\\", "/").strip().strip("/")
        while path.startswith("./"):
            path = path[2:]
        if not path or ".." in PurePosixPath(path).parts:
            continue
        if path not in cleaned:
            cleaned.append(path)
    return cleaned


def _entrypoint(key_files: list[str]) -> str | None:
    """The key file an application would conventionally start from, if named."""
    for path in key_files:
        candidate = PurePosixPath(path)
        if candidate.stem.lower() in _ENTRYPOINT_STEMS and candidate.suffix in _ENTRYPOINT_SUFFIXES:
            return path
    return None


def _dependency_slugs(dependencies: Any, declared: dict[str, bool]) -> list[str]:
    """Slugs of the services this one says it depends on.

    Kept even when the named service is not declared anywhere — that dangling
    reference is precisely the thing worth reporting later, so dropping it here
    would throw away the finding.
    """
    slugs: list[str] = []
    for dependency in dependencies or []:
        slug = slugify(str(dependency), fallback="")
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


def _endpoints(endpoints: Any) -> list[EndpointContract]:
    recorded: list[EndpointContract] = []
    for endpoint in endpoints or []:
        if not isinstance(endpoint, dict):
            continue
        recorded.append(
            EndpointContract(
                method=str(endpoint.get("method") or "").upper(),
                path=str(endpoint.get("path") or ""),
            )
        )
    return recorded


def _model_names(models: Any) -> list[str]:
    names: list[str] = []
    for model in models or []:
        name = str(model.get("name") or "").strip() if isinstance(model, dict) else ""
        if name and name not in names:
            names.append(name)
    return names


# ── Validation ───────────────────────────────────────────────────


def check_contract(
    contract: dict[str, Any] | ArchitectureContract | None,
    manifest: Manifest | None = None,
    workspace: RunWorkspace | None = None,
) -> list[StaticCheck]:
    """Compare what was approved against what was written, one service at a time.

    Deterministic and side-effect free: it reads paths and writes nothing. One
    :class:`StaticCheck` per service with something wrong, carrying the canonical
    slug so `agents/attribution.py` places the violation without being told how.

    A service whose directory is missing is reported once and then left alone —
    listing every file inside a directory that does not exist would bury the one
    fact that matters under a dozen consequences of it.
    """
    parsed = _parse(contract)
    if parsed is None or workspace is None:
        return []

    known = set(manifest or {})
    checks: list[StaticCheck] = []

    for service in parsed.services:
        violations = _violations_for(service, parsed, workspace, known)
        check = StaticCheck(name=CHECK_NAME, service=service.slug, passed=not violations)
        check.failures = violations
        checks.append(check)

    return checks


def _parse(contract: Any) -> ArchitectureContract | None:
    if isinstance(contract, ArchitectureContract):
        return contract if contract.services else None
    if not contract:
        return None
    try:
        parsed = ArchitectureContract.model_validate(contract)
    except Exception:
        # A contract that cannot be read is not a reason to fail a run: the
        # compiler and the tests are still the evidence that matters.
        logger.warning("The stored architecture contract could not be read; skipping its checks")
        return None
    return parsed if parsed.services else None


def _violations_for(
    service: ServiceContract,
    contract: ArchitectureContract,
    workspace: RunWorkspace,
    known_slugs: set[str],
) -> list[str]:
    violations: list[str] = []

    # A dangling dependency is a defect in the architecture rather than in the
    # code, but it is deterministic and the developer is the one who has to
    # reconcile it, so it is reported alongside the rest.
    for dependency in service.depends_on:
        if dependency not in contract.slugs:
            violations.append(
                f"{service.slug}: the architecture says this service depends on "
                f"{dependency!r}, which it does not declare as a service"
            )

    source = workspace.service_source(service.slug)
    if not _has_content(source):
        violations.append(
            f"{service.slug}: the architecture declares this service but no source "
            f"was generated for it"
        )
        # Everything below would only restate this one fact.
        return violations

    for folder in service.folders:
        if not _resolve(source, folder, directory=True):
            violations.append(
                f"{service.slug}/{folder}: the architecture requires this folder, "
                f"which the generated code does not have"
            )

    for path in service.key_files:
        if _resolve(source, path, directory=False):
            continue
        requirement = (
            "entrypoint" if path == service.entrypoint else "key file"
        )
        violations.append(
            f"{service.slug}/{path}: the architecture requires this {requirement}, "
            f"which the generated code does not have"
        )

    # The manifest and the disk should agree; when they do not, disk wins and the
    # disagreement is worth a line of its own rather than a silent divergence.
    if known_slugs and service.slug not in known_slugs:
        violations.append(
            f"{service.slug}: source exists on disk but the code manifest has no "
            f"record of this service"
        )

    return violations


def _resolve(base: Path, relative: str, *, directory: bool) -> bool:
    """Does ``relative`` exist under ``base``, as the kind of thing expected?

    Routed through `safe_join` like every other model-supplied path, so an
    architecture that names ``../../etc`` is answered with ``False`` rather than
    being allowed to look there.
    """
    try:
        target = safe_join(base, relative)
    except UnsafePathError:
        return False
    return target.is_dir() if directory else target.is_file()


def _has_content(source: Path) -> bool:
    """A service directory counts as generated once it holds a real file."""
    if not source.is_dir():
        return False
    return any(
        path.is_file() and not _IGNORED.intersection(path.parts) for path in source.rglob("*")
    )
