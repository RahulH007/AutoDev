from __future__ import annotations

import copy
from typing import Any

from agents import attribution
from agents.base import run_stage, workspace_for, write_document
from core import manifest as manifest_util
from core.config import Purpose, get_settings
from core.contracts import ArchitectureContract
from core.logging import bind, get_logger
from core.paths import RunWorkspace, UnsafePathError, slugify
from llm import registry
from llm.budget import estimate_tokens
from llm.routing import RouteSignal
from prompts.developer_json_prompt import build_failure_evidence, get_developer_prompt
from prompts.developer_pdf_prompt import get_developer_doc_prompt
from prompts.projections import architecture_services
from schema.developer_schema import DeveloperSchema
from state.state import AgentStatus, MultiAgent, Stage
from utils.json_utils import load_json, save_json
from utils.status_tracker import log_status, mark

logger = get_logger(__name__)

ARTIFACT_JSON = "developer.json"
ARTIFACT_PDF = "developer.pdf"


async def developer_agent(state: MultiAgent) -> dict[str, Any]:
    """Generate source code one architecture service at a time.

    The whole project in a single response needs roughly 8,200 output tokens,
    which cannot fit an 8,000 token-per-minute window at any prompt size. One
    service needs three to four thousand and does fit, so the stage makes one
    call per service and writes each one's files as it arrives.

    A rework pass rebuilds only the services the evidence implicates. Everything
    that passed is left exactly as it is — not regenerated and not re-read, so a
    healthy service is byte-for-byte what it was. `_targets` decides.

    A pass that fails partway keeps what it produced: the files are already on
    disk, the manifest records them, and the next pass targets what is still
    outstanding. `retry_count` still counts passes over the project, which is why
    `service_attempts` exists to count what happened to each service.
    """
    stage = Stage.DEVELOPER
    attempt = state.get("retry_count", 0) + 1

    # Progress lives out here so a failure mid-loop is still recorded: run_stage
    # turns an exception into a status update that would otherwise carry none of it.
    generated: list[str] = []
    preserved: list[str] = []
    failed: list[str] = []
    # Carried forward rather than recomputed: a service that was upgraded stays
    # upgraded for the rest of the run, and the count is what stops it climbing
    # again past the configured cap.
    escalations: dict[str, int] = dict(state.get("service_escalations") or {})
    attempts: dict[str, int] = dict(state.get("service_attempts") or {})
    manifest: dict[str, Any] = copy.deepcopy(state.get("code_manifest") or {})
    # Seeded from what the project already is, not from nothing. `developer.json`
    # has to keep meaning "the whole delivered project": a surgical pass rebuilds
    # one service, and an artifact rebuilt from that alone would silently drop
    # every service that was fine.
    combined: dict[str, Any] = {"project_name": "", "services": []}

    async def body() -> dict[str, Any]:
        workspace = workspace_for(state)

        log_status({**state, "retry_count": attempt, "status": mark(state, stage, AgentStatus.IN_PROGRESS)})

        static_report = state.get("static_report") or None
        qa_report = state.get("qa_report") or None
        verification_report = state.get("verification_report") or None

        project_evidence = build_failure_evidence(qa_report, static_report, verification_report)

        # The lens attribution reads file paths through. Snapshotted before the
        # loop because `_write_code` mutates the live manifest as each service
        # lands, and evidence describing the *previous* pass must be read against
        # the manifest as it stood when that pass ended -- otherwise the answer
        # for service two would depend on what service one happened to write.
        evidence_manifest = copy.deepcopy(state.get("code_manifest") or {})

        services = architecture_services(state.get("architecture") or {})
        if not services:
            raise ValueError("The architecture names no services to build.")

        slugs = [slugify(service.get("name") or "") for service in services]
        targeted = _targets(
            slugs,
            done=set(state.get("generated_services") or []),
            static_report=static_report,
            verification_report=verification_report,
            qa_report=qa_report,
            manifest=evidence_manifest,
            has_evidence=bool(project_evidence),
        )

        # Everything the project already is. A preserved service contributes its
        # previous entry unchanged; a regenerated one replaces its own.
        _seed(combined, workspace, slugs, targeted)

        if attempt > 1:
            logger.info(
                "Developer pass %d: targeting %s; preserving %s",
                attempt,
                ", ".join(targeted) or "nothing",
                ", ".join(slug for slug in slugs if slug not in targeted) or "nothing",
            )

        # The architecture contract, which already joins each service to the
        # files and declarations the architect asked it for. That is the only
        # deterministic thing known about the size of the answer before the call.
        contract = _parse_contract(state.get("contract"))

        for index, (service, slug) in enumerate(zip(services, slugs, strict=True), start=1):
            if slug not in targeted:
                logger.info("Service %d/%d %s preserved", index, len(services), slug)
                preserved.append(slug)
                generated.append(slug)
                continue

            logger.info("Service %d/%d: %s", index, len(services), service.get("name"))
            attempts[slug] = attempts.get(slug, 0) + 1

            # Only this service's share of the failures, plus whatever could not
            # be attributed to any service -- see `_evidence_for`.
            static_for, verification_for, qa_for = _evidence_for(
                slug,
                static_report=static_report,
                verification_report=verification_report,
                qa_report=qa_report,
                manifest=evidence_manifest,
                project_evidence=project_evidence,
            )

            # Decided here, from evidence the gates have already attributed to
            # this service, and only on a pass that carries any. Escalation is a
            # response to what verification found, never to how a call failed.
            reason = _escalation_reason(
                slug, static_report, verification_report, qa_report, evidence_manifest
            )
            if reason and escalations.get(slug, 0) < get_settings().max_model_escalations:
                escalations[slug] = escalations.get(slug, 0) + 1
                logger.info("Escalating %s to a stronger model: %s", slug, reason)

            # Sized per service: the ceiling is resolved from what this service
            # was asked to produce, not from the largest service in the project.
            # Built inside the loop for that reason; the client is cached by its
            # resolved ceiling, so this costs nothing beyond the ceilings that
            # genuinely differ.
            model = registry.get_structured_llm(
                DeveloperSchema,
                Purpose.HEAVY,
                demand=_output_demand(contract, slug),
                signal=_route_signal(contract, slug, escalations.get(slug, 0), reason or ""),
            )

            try:
                # Each call is metered by the shared budget, so the pacing that
                # makes one call fit also spaces the calls apart.
                response = await model.ainvoke(
                    get_developer_prompt(
                        state["user_requirements"],
                        state.get("prd") or {},
                        state.get("architecture") or {},
                        service=service,
                        qa_report=qa_for,
                        static_report=static_for,
                        verification_report=verification_for,
                    )
                )
            except Exception as exc:
                # Carry on: the remaining services are independent, and every one
                # that lands is one the retry does not have to pay for again.
                logger.error("Service %s failed to generate: %s", slug, exc)
                failed.append(slug)
                continue

            output = response.model_dump(mode="json")

            # Checked before anything on disk is touched. An answer with no
            # usable file in it must not be allowed to replace working code with
            # nothing -- and since reconciliation deletes, the order matters:
            # validate, then reconcile, then write.
            if not _has_usable_files(output, slug):
                logger.error("Service %s returned no usable files; keeping the previous one", slug)
                failed.append(slug)
                continue

            # Only a full regeneration describes a whole service. A fix prompt
            # asks for the changed files alone, so its response is partial by
            # construction and its silence about a file is not a request to
            # delete it. See `_reconcile`.
            if not build_failure_evidence(qa_for, static_for, verification_for):
                _reconcile(output, workspace, manifest, slug)

            _merge_output(combined, output)
            save_json(combined, workspace.artifacts / ARTIFACT_JSON)
            _write_code(output, workspace, manifest)
            generated.append(slug)

        # A service that failed leaves the rest of the project standing. It is
        # recorded in `failed_services`, its previous files are untouched, and the
        # next pass targets it again -- which is worth more than throwing away the
        # services that did land. Only a pass that produced nothing at all is a
        # failure of the stage, because then there is no project to verify.
        if failed:
            logger.warning(
                "%d of %d service(s) could not be generated: %s",
                len(failed), len(services), ", ".join(failed),
            )
        if failed and not generated:
            raise RuntimeError(
                f"No service could be generated: {', '.join(failed)}"
            )

        await write_document(
            get_developer_doc_prompt(state["user_requirements"], combined), workspace, ARTIFACT_PDF
        )

        logger.info(
            "Wrote %d file(s) across %d service(s); %d regenerated, %d preserved",
            manifest_util.file_count(manifest),
            len(manifest_util.services(manifest)),
            len(generated) - len(preserved),
            len(preserved),
        )
        return {
            "code_manifest": manifest,
            "retry_count": attempt,
            # Stale evidence must be cleared so the routers judge only the fresh code.
            "static_report": {},
            "verification_report": {},
            # And the attribution derived from it, which the gates rebuild from
            # the fresh reports moments later. Nothing routes on this; clearing it
            # only stops the console showing last pass's failures against code
            # that has just been rewritten.
            "service_failures": {},
        }

    with bind(run_id=state.get("run_id"), stage=stage.value):
        update = await run_stage(state, stage, body)
        # Even on failure the attempt has been spent; recording it keeps the retry
        # cap honest instead of letting a failing node loop forever.
        update.setdefault("retry_count", attempt)
        # And partial progress survives, so the next pass resumes rather than restarts.
        update.setdefault("code_manifest", manifest)
        update["generated_services"] = generated
        update["failed_services"] = failed
        # Recorded on both paths for the same reason `retry_count` is: an upgrade
        # that has been spent must not be forgotten because the pass it happened
        # in went on to fail, or the next pass would buy it again. The same holds
        # for an attempt already paid for.
        update["service_escalations"] = escalations
        update["service_attempts"] = attempts
        return update


def _targets(
    slugs: list[str],
    *,
    done: set[str],
    static_report: dict[str, Any] | None,
    verification_report: dict[str, Any] | None,
    qa_report: dict[str, Any] | None,
    manifest: dict[str, Any],
    has_evidence: bool,
) -> set[str]:
    """Which services this pass should rebuild.

    A rework pass used to regenerate the whole project because one service failed
    to compile. Attribution has known which service each failure belongs to since
    Phase 1; this is where that finally decides who gets rebuilt.

    A service is targeted when, and only when:

    1. it was never successfully generated — there is nothing to preserve; or
    2. the evidence implicates it, by `implicated_services`.

    Everything else is preserved untouched. In particular a *dependent* of a
    failing service is not rebuilt: the architecture's ``depends_on`` establishes
    that a coupling exists, not what it is, so "the backend changed" cannot tell
    us whether the frontend's generated code is now wrong. Rebuilding it anyway
    would be guessing at the cost of the work it destroys, and the rule when
    impact cannot be established is to preserve.

    One fallback, and it exists to guarantee progress. If there is failure
    evidence but none of it could be attributed to any service, targeting nothing
    would leave the pass with no work, the code unchanged and the same failure
    next time round — a loop that ends only at the retry cap. So an unattributable
    failure falls back to the old behaviour and rebuilds everything. Evidence that
    *can* be placed is trusted, and anything unattributable alongside it rides
    along to the services already targeted, as Phase 2 already arranges.
    """
    missing = {slug for slug in slugs if slug and slug not in done}
    implicated = set(
        attribution.implicated_services(
            static_report=static_report,
            verification_report=verification_report,
            qa_report=qa_report,
            manifest=manifest,
        )
    )

    targeted = missing | (implicated & set(slugs))

    if has_evidence and not targeted:
        logger.info("No failure could be attributed to a service; rebuilding all of them")
        return {slug for slug in slugs if slug}

    return targeted


def _seed(
    combined: dict[str, Any],
    workspace: RunWorkspace,
    slugs: list[str],
    targeted: set[str],
) -> None:
    """Start the artifact from the project that already exists.

    `developer.json` means "the whole delivered project", and a surgical pass
    touches one service. Rebuilding the artifact from this pass's responses alone
    would drop every service that was fine — so the previous artifact is read back
    and everything not being regenerated is carried over verbatim.

    Services being regenerated are left out: their entry arrives from the model
    and replaces what was there. A missing or unreadable artifact simply seeds
    nothing, which is exactly right for a first pass.
    """
    try:
        previous = load_json(workspace.artifacts / ARTIFACT_JSON)
    except (OSError, ValueError):
        return  # no previous pass, or an artifact we cannot read: seed nothing
    if not isinstance(previous, dict):
        return

    known = set(slugs)
    kept = [
        service
        for service in previous.get("services") or []
        if (slug := slugify(str(service.get("service_name") or ""), fallback="")) in known
        and slug not in targeted
    ]

    combined["services"] = copy.deepcopy(kept)
    for field in ("project_name", "readme_content"):
        if previous.get(field):
            combined[field] = previous[field]
    for field in ("setup_instructions", "dependency_files", "development_notes"):
        if previous.get(field):
            combined[field] = copy.deepcopy(previous[field])


def _has_usable_files(output: dict[str, Any], slug: str) -> bool:
    """Did the model actually return code for the service that was asked for?

    The guard that stops an empty or off-target answer replacing working code
    with nothing. Deliberately minimal — it establishes that the artifact is
    structurally usable, not that it is good; whether it is good is what the
    static gate, the contract and the tests are for.
    """
    for service in output.get("services") or []:
        if slugify(str(service.get("service_name") or ""), fallback="") != slug:
            continue
        if any((file.get("file_path") or "").strip() for file in service.get("files") or []):
            return True
    return False


def _reconcile(
    output: dict[str, Any],
    workspace: RunWorkspace,
    manifest: dict[str, Any],
    slug: str,
) -> list[str]:
    """Remove files this service used to have and no longer produces.

    Only ever called for a *complete* regeneration of one service. A fix response
    is asked for the changed files alone, so its silence about a file means "I did
    not touch it" and deleting on that basis would erase the service — which is
    why the caller checks first and this never guesses.

    Scoped to the one slug throughout: the old paths come from that service's
    manifest entry, the deletion goes through `RunWorkspace.delete_source_file`
    with the service's own directory as the base, and `safe_join` refuses anything
    that would reach past it. There is no whole-run equivalent and there should
    not be one.
    """
    previous = {entry["file_path"] for entry in manifest_util.files_for(manifest, slug)}
    if not previous:
        return []

    current = {
        (file.get("file_path") or "").strip()
        for service in output.get("services") or []
        if slugify(str(service.get("service_name") or ""), fallback="") == slug
        for file in service.get("files") or []
    }

    removed: list[str] = []
    for path in sorted(previous - current):
        try:
            if workspace.delete_source_file(slug, path):
                removed.append(path)
        except UnsafePathError as exc:
            logger.warning("Refused to remove a recorded path outside %s: %s", slug, exc)

    if removed:
        manifest_util.remove_files(manifest, slug, removed)
        logger.info("Removed %d stale file(s) from %s: %s", len(removed), slug, ", ".join(removed))
    return removed


def _parse_contract(contract: Any) -> ArchitectureContract | None:
    """The stored contract, or ``None`` when there is nothing usable to read.

    Only ever used to size a ceiling, so a contract that cannot be read is not a
    problem worth raising: the configured ceiling is the answer in that case, and
    it is the answer this stage has always used.
    """
    if not contract:
        return None
    try:
        return ArchitectureContract.model_validate(contract)
    except Exception:
        logger.debug("The stored contract could not be read; using configured ceilings")
        return None


def _output_demand(contract: ArchitectureContract | None, slug: str) -> int | None:
    """How large this service's answer is expected to be, in output tokens.

    Read off what the architect actually asked for: a fixed allowance for the
    project-level fields of `DeveloperSchema`, one per required file, and a
    smaller one per endpoint and data model — because a service with two files
    and twenty endpoints writes far more than its file count suggests.

    ``None`` whenever the architecture says nothing measurable about this
    service, which is the honest answer and leaves the configured ceiling in
    place. Guessing here is the one way this could truncate a response and cost
    more than it saves.
    """
    service = contract.service(slug) if contract else None
    if service is None or not service.key_files:
        return None

    settings = get_settings()
    declarations = len(service.endpoints) + len(service.data_models)
    demand = (
        settings.adaptive_service_base_tokens
        + settings.adaptive_service_file_tokens * len(service.key_files)
        + settings.adaptive_service_declaration_tokens * declarations
    )

    if settings.adaptive_ceilings:
        logger.info(
            "Adaptive ceiling for %s: %d / %d",
            slug,
            settings.max_output_for(Purpose.HEAVY, demand),
            settings.max_output_for(Purpose.HEAVY),
        )
    return demand


def _escalation_reason(
    slug: str,
    static_report: dict[str, Any] | None,
    verification_report: dict[str, Any] | None,
    qa_report: dict[str, Any] | None,
    manifest: dict[str, Any],
) -> str | None:
    """Why this service should be rebuilt by a stronger model, or ``None``.

    Reads the evidence Phase 1 already attributed to *this* service, so a
    frontend is never upgraded because the backend failed to compile. The
    judgement itself belongs to `agents/attribution.py`, which owns what a
    failure means; this only asks it about the right service.

    ``None`` whenever routing is off, since there are no tiers to move between.
    """
    if not get_settings().routing_enabled:
        return None

    return attribution.escalation_reason(
        attribution.failures_for_service(
            slug,
            static_report=static_report,
            verification_report=verification_report,
            qa_report=qa_report,
            manifest=manifest,
        )
    )


def _route_signal(
    contract: ArchitectureContract | None,
    slug: str,
    escalations: int = 0,
    escalation_reason: str = "",
) -> RouteSignal | None:
    """What the architecture says about how hard this service will be.

    The same contract that sizes the ceiling also sizes the model: how many files
    it must produce, how many endpoints it exposes, how many data models it owns
    and how many other services it leans on. All four are fixed before the first
    attempt and identical on the fourth, which is what makes the route
    deterministic across a run's retries.

    ``None`` when the architecture describes nothing measurable, and the registry
    reads that as "resolve the model exactly as you always have". The agent still
    never learns what a tier is — it reports what it was given and the routing
    layer decides.
    """
    service = contract.service(slug) if contract else None
    if service is None:
        return None

    return RouteSignal(
        key_files=len(service.key_files),
        endpoints=len(service.endpoints),
        data_models=len(service.data_models),
        dependencies=len(service.depends_on),
        escalations=escalations,
        escalation_reason=escalation_reason,
    )


def _evidence_for(
    slug: str,
    *,
    static_report: dict[str, Any] | None,
    verification_report: dict[str, Any] | None,
    qa_report: dict[str, Any] | None,
    manifest: dict[str, Any],
    project_evidence: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The failure reports this service actually needs to see.

    Every service used to receive the whole project's failures, so a frontend
    being rebuilt was shown the backend's compiler errors and a service with
    nothing wrong with it was shown everything. `agents/attribution.py` already
    knows which service each failure implicates; this is where that finally
    changes what a model is asked to read.

    Narrowed, not rewritten. The reports come back in their original shape and
    go straight into the same `build_failure_evidence` as before, so the section
    order, the wording and the truncation are untouched — the only difference is
    that another service's failures are no longer in the input.

    ``include_unattributed`` matters more than it looks. A failure the evidence
    cannot place still has to reach someone, and since nothing knows whose it is,
    it goes to everyone exactly as it does today. Narrowing without it would be
    the one way this change could lose evidence rather than merely stop repeating
    it.
    """
    narrowed = attribution.reports_for_service(
        slug,
        static_report=static_report,
        verification_report=verification_report,
        qa_report=qa_report,
        manifest=manifest,
        include_unattributed=True,
    )

    if project_evidence:
        _log_evidence_reduction(slug, project_evidence, narrowed)

    return narrowed


def _log_evidence_reduction(
    slug: str, project_evidence: str, narrowed: tuple[dict[str, Any], ...]
) -> None:
    """Record how much of the project's evidence this service was spared.

    Measured with `estimate_tokens`, the same pure function the token budget
    sizes a prompt with, so the figure is comparable to the `estimated_tokens`
    the ledger records for the call itself. It is not a second counter: nothing
    is accumulated here, and the authoritative per-call figure is still the one
    `_metered` writes.

    The narrowed evidence is rendered once here and again inside
    `get_developer_prompt`. That is a string join over already-capped data, and
    it is worth it to keep the prompt builder's contract — reports in, prompt out
    — rather than threading a pre-rendered string through it.
    """
    static_for, verification_for, qa_for = narrowed
    whole = estimate_tokens(project_evidence)
    part = estimate_tokens(build_failure_evidence(qa_for, static_for, verification_for))

    logger.info(
        "Evidence for %s: ~%d tokens of ~%d project-wide (%d%% less to read)",
        slug,
        part,
        whole,
        round(100 * (whole - part) / whole) if whole else 0,
    )


def _merge_output(combined: dict[str, Any], output: dict[str, Any]) -> None:
    """Fold one service's response into the stage's single artifact.

    Replaces by slug rather than appending. Appending was right while every pass
    rebuilt everything from an empty artifact; now that the artifact is seeded
    with the services being preserved, appending a regenerated service would
    leave the project holding two copies of it — the old one and the new.

    Project-level fields are taken from whichever call supplied them, first one
    winning, because no call sees the whole project. That is the seam the
    integration call is meant to fill.
    """
    for service in output.get("services") or []:
        slug = slugify(str(service.get("service_name") or ""), fallback="")
        existing = next(
            (
                index
                for index, held in enumerate(combined["services"])
                if slugify(str(held.get("service_name") or ""), fallback="") == slug
            ),
            None,
        )
        if existing is None:
            combined["services"].append(service)
        else:
            combined["services"][existing] = service

    for field in ("project_name", "readme_content"):
        if not combined.get(field) and output.get(field):
            combined[field] = output[field]
    for field in ("setup_instructions", "dependency_files", "development_notes"):
        combined.setdefault(field, [])
        for item in output.get(field) or []:
            if item not in combined[field]:
                combined[field].append(item)


def _write_code(
    output: dict[str, Any],
    workspace: RunWorkspace,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    rejected = 0

    for service in output.get("services") or []:
        service_name = service.get("service_name") or "unnamed-service"

        for file in service.get("files") or []:
            file_path = file.get("file_path")
            if not file_path:
                continue
            try:
                workspace.write_source_file(service_name, file_path, file.get("code") or "")
            except UnsafePathError as exc:
                rejected += 1
                logger.warning("Rejected generated file path: %s", exc)
                continue

            manifest_util.add_file(
                manifest,
                service_name,
                file_path,
                description=file.get("description", ""),
                language=file.get("language", ""),
            )

    for dependency in output.get("dependency_files") or []:
        file_path = dependency.get("file_path")
        if not file_path:
            continue
        try:
            written = workspace.write_shared_source_file(file_path, dependency.get("code") or "")
        except UnsafePathError as exc:
            rejected += 1
            logger.warning("Rejected dependency file path: %s", exc)
            continue
        logger.info("Wrote dependency file %s", workspace.relative(written))

    readme = output.get("readme_content") or ""
    if readme.strip():
        workspace.write_shared_source_file("README.md", readme)

    if rejected:
        logger.warning("Discarded %d generated path(s) that tried to escape the workspace", rejected)

    return manifest
