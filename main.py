"""Command-line entry point.

A thin client of :class:`~server.service.RunService`, so the CLI and the HTTP API
drive exactly the same pipeline against the same database.

    python main.py "Build an expense tracker with login and monthly reports."
    python main.py --file requirement.txt --yes
    python main.py --list
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from core.config import shadowed_env_keys
from core.logging import configure_logging, get_logger
from graph.checkpoint import open_checkpointer
from server.db import open_database
from server.models import RunRecord, RunStatus
from server.service import RunService

logger = get_logger(__name__)

RULE = "-" * 68


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentforge",
        description="Generate a project from a plain-language requirement.",
    )
    parser.add_argument("requirement", nargs="?", help="What to build.")
    parser.add_argument("-f", "--file", type=Path, help="Read the requirement from a file.")
    parser.add_argument("-n", "--name", help="A name for the run. Defaults to the product name.")
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Approve the PRD and architecture without stopping to review them.",
    )
    parser.add_argument("--list", action="store_true", help="List previous runs and exit.")
    return parser


def read_requirement(args: argparse.Namespace) -> str:
    if args.file:
        return args.file.read_text(encoding="utf-8").strip()
    if args.requirement:
        return args.requirement.strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()

    print("Describe what you want built. Finish with an empty line.\n")
    lines: list[str] = []
    while (line := input("> ").rstrip()) or not lines:
        if not line:
            break
        lines.append(line)
    return "\n".join(lines).strip()


# ── Display ──────────────────────────────────────────────────────


def show_prd(prd: dict) -> None:
    print(f"\n{RULE}\n  PRD: {prd.get('product_name', '(unnamed)')}\n{RULE}")
    print(f"  {(prd.get('product_summary') or '')[:200]}")
    print(f"  Complexity: {prd.get('complexity_estimate', 'unknown')}")

    features = prd.get("features") or []
    print(f"\n  Features ({len(features)}):")
    for feature in features[:8]:
        mvp = " [MVP]" if feature.get("is_mvp") else ""
        print(f"    - {feature.get('name', '?')} ({feature.get('priority', '?')}){mvp}")
    if len(features) > 8:
        print(f"    ... and {len(features) - 8} more")


def show_architecture(architecture: dict) -> None:
    print(f"\n{RULE}\n  ARCHITECTURE: {architecture.get('architecture_style', '?')}\n{RULE}")
    print(f"  {(architecture.get('system_overview') or '')[:200]}")

    services = architecture.get("services") or []
    print(f"\n  Services ({len(services)}):")
    for service in services:
        stack = ", ".join(service.get("tech_stack") or [])
        print(f"    - {service.get('name', '?')} ({stack})")

    databases = architecture.get("databases") or []
    if databases:
        print(f"\n  Databases: {', '.join(db.get('name', '?') for db in databases)}")


def show_summary(record: RunRecord, state: dict) -> None:
    print(f"\n{RULE}\n  {record.name} — {record.status.label}\n{RULE}")

    if record.error:
        print(f"  Error: {record.error}")

    static_report = state.get("static_report") or {}
    if static_report.get("ran"):
        verdict = "passed" if static_report.get("passed") else "failed"
        print(f"  Static gate : {verdict} ({len(static_report.get('failures') or [])} problem(s))")

    verification = state.get("verification_report") or {}
    if verification.get("summary"):
        print(f"  Tests       : {verification['summary']}")

    if record.qa_score is not None:
        print(f"  Code quality: {record.qa_score}/10")
    print(f"  Attempts    : {record.retry_count}")
    print(f"  Workspace   : {record.workspace}")
    if record.zip_path:
        print(f"\n  Download    : {record.zip_path}")


async def ask(stage_label: str) -> tuple[str, bool]:
    """Returns (feedback, abort). Empty feedback means approve."""
    print(f"\n  Review the {stage_label}.")
    print("    ENTER      approve and continue")
    print("    some text  send it back for revision")
    print("    exit       stop the run")

    answer = (await asyncio.to_thread(input, "  > ")).strip()
    if answer.lower() in ("exit", "quit"):
        return "", True
    return answer, False


# ── Commands ─────────────────────────────────────────────────────


async def list_runs() -> int:
    async with open_database() as database:
        records = await database.list_runs()

    if not records:
        print("No runs yet.")
        return 0

    print(f"{'RUN ID':<34} {'STATUS':<30} NAME")
    for record in records:
        print(f"{record.id:<34} {record.status.label:<30} {record.name}")
    return 0


async def execute(requirement: str, name: str | None, auto_approve: bool) -> int:
    async with open_database() as database, open_checkpointer() as checkpointer:
        service = await RunService(database, checkpointer).start()
        try:
            record = await service.create(requirement, name)
            print(f"\nRun {record.id}\n")

            await service.begin(record.id)

            while True:
                record = await service.wait(record.id)

                if record.status.is_terminal:
                    break

                if not record.status.is_awaiting_review:
                    logger.warning("Run stopped in an unexpected state: %s", record.status)
                    break

                state = await service.get_graph_state(record.id)

                if record.status is RunStatus.AWAITING_PM_REVIEW:
                    show_prd(state.get("prd") or {})
                    label = "PRD"
                else:
                    show_architecture(state.get("architecture") or {})
                    label = "architecture"

                if auto_approve:
                    await service.approve(record.id)
                    continue

                feedback, abort = await ask(label)
                if abort:
                    await service.cancel(record.id)
                    record = await service.get(record.id)
                    break

                if feedback:
                    await service.submit_feedback(record.id, feedback)
                else:
                    await service.approve(record.id)

            show_summary(record, await service.get_graph_state(record.id))
            return 0 if record.status is RunStatus.COMPLETED else 1
        finally:
            await service.stop()


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()

    for name in shadowed_env_keys():
        logger.warning(
            "%s is set in your shell and overrides the value in .env. The shell value is in use.",
            name,
        )

    if args.list:
        return await list_runs()

    requirement = read_requirement(args)
    if not requirement:
        print("Nothing to build. Pass a requirement or use --file.", file=sys.stderr)
        return 2

    return await execute(requirement, args.name, args.yes)


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        return asyncio.run(async_main(argv))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
