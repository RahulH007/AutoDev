"""Which service does each verification failure belong to?

The retry loop currently answers "all of them". `build_failure_evidence` gathers
the compiler output, the failing tests and the reviewer's bugs into one blob, and
every service the developer regenerates receives the whole of it — including the
services nothing was ever wrong with.

The attribution needed to do better is already in the data and needs no new
representation:

- a :class:`~schema.verification_schema.StaticCheck` names the service it ran
  over, and every failure line it produced is prefixed with that service's slug;
- a :class:`~schema.verification_schema.ServiceTestResult` names its service
  outright, already slugged by the test runner;
- a QA bug names a ``file_path``, and the code manifest says which service owns
  that file.

So this module reads those three structures and the manifest, and says which
service each failure implicates. It is deliberately the whole of the mapping and
none of the consequences: nothing here changes what is regenerated, what is
retried, or what any prompt says. That is Phase 2.

Three rules hold the mapping honest.

**Nothing is guessed.** A failure whose service cannot be established from the
data is kept under :data:`UNATTRIBUTED` rather than assigned to whichever service
seemed likely. Losing it would be worse, and inventing an owner for it worse
still.

**Nothing is mutated.** Every report and the manifest are read and never written,
because they are the run's evidence and other stages are still reading them.
Failure text is reproduced exactly as its producer wrote it; where a structured
failure has to become a line of text, original fields are joined and none is
reworded or truncated.

**Nothing is ordered by chance.** Identical inputs give an identical answer, down
to the order of the lists, so a difference in the mapping always means a
difference in the evidence.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from core import manifest as manifest_util
from core.contracts import CHECK_NAME as CONTRACT_CHECK
from core.manifest import Manifest
from core.paths import slugify

# Where a failure goes when the data does not say which service it belongs to.
#
# Safe as a key beside real slugs because `slugify` strips every character that
# is not a lowercase letter or digit, so no service name can ever produce a slug
# beginning with an underscore.
UNATTRIBUTED = "_unattributed"

# Where a failure came from, so a caller can weigh compiler output differently
# from an opinion without re-deriving which is which.
SOURCE_STATIC = "static"
SOURCE_TEST = "test"
SOURCE_RUNNER = "runner"
SOURCE_QA = "qa"


@dataclass(frozen=True)
class Failure:
    """One failure, and the service it implicates.

    ``text`` is what the producer wrote. For a static failure that is the line
    itself; for a test failure and a QA bug — which arrive structured — it is the
    original fields joined, each reproduced verbatim.

    ``service`` is a slug, or :data:`UNATTRIBUTED` when the evidence does not
    establish one.
    """

    source: str
    service: str
    text: str
    file: str = ""
    severity: str = ""
    # For a static failure, which check produced it — "compile", "pyflakes",
    # "json" or "contract". Kept because a design mismatch and a syntax error are
    # both static failures and are worth telling apart afterwards.
    check: str = ""


# ── The public mapping ───────────────────────────────────────────


def collect_failures(
    static_report: dict[str, Any] | None = None,
    verification_report: dict[str, Any] | None = None,
    qa_report: dict[str, Any] | None = None,
    manifest: Manifest | None = None,
) -> list[Failure]:
    """Every failure in the current evidence, each carrying its service.

    Ordered as `build_failure_evidence` orders its sections — compiler output,
    then real test results, then the reviewer's opinion — so the most objective
    evidence about a service is also the first thing listed for it.
    """
    index = _ManifestIndex(manifest)
    return [
        *_static_failures(static_report or {}),
        *_test_failures(verification_report or {}),
        *_qa_failures(qa_report or {}, index),
    ]


def failures_for_service(
    service: str,
    static_report: dict[str, Any] | None = None,
    verification_report: dict[str, Any] | None = None,
    qa_report: dict[str, Any] | None = None,
    manifest: Manifest | None = None,
) -> list[Failure]:
    """The failures attributable to one service, in evidence order.

    ``service`` is matched by slug, so the caller may pass a display name
    ("Backend API") or the slug itself. Pass :data:`UNATTRIBUTED` to read the
    failures no service could be established for.
    """
    wanted = UNATTRIBUTED if service == UNATTRIBUTED else _canonical(service)
    if not wanted:
        return []

    return _deduplicated(
        failure
        for failure in collect_failures(static_report, verification_report, qa_report, manifest)
        if failure.service == wanted
    )


def implicated_services(
    static_report: dict[str, Any] | None = None,
    verification_report: dict[str, Any] | None = None,
    qa_report: dict[str, Any] | None = None,
    manifest: Manifest | None = None,
) -> list[str]:
    """The slugs of every service the current evidence implicates, sorted.

    :data:`UNATTRIBUTED` is not a service and never appears here. Read those
    failures with :func:`failures_for_service`, which is where a caller that
    needs to show all of the evidence will find the remainder.
    """
    return sorted(
        {
            failure.service
            for failure in collect_failures(
                static_report, verification_report, qa_report, manifest
            )
            if failure.service != UNATTRIBUTED
        }
    )


def attribution_map(
    static_report: dict[str, Any] | None = None,
    verification_report: dict[str, Any] | None = None,
    qa_report: dict[str, Any] | None = None,
    manifest: Manifest | None = None,
) -> dict[str, list[str]]:
    """The whole mapping as plain data, for graph state and the API.

    Keys are service slugs plus, when there is anything to put there,
    :data:`UNATTRIBUTED`. Keys are sorted and duplicate lines within a service
    are dropped, so the same evidence always serialises identically.
    """
    grouped: dict[str, list[Failure]] = {}
    for failure in collect_failures(static_report, verification_report, qa_report, manifest):
        grouped.setdefault(failure.service, []).append(failure)

    return {
        service: [failure.text for failure in _deduplicated(grouped[service])]
        for service in sorted(grouped)
    }


def reports_for_service(
    service: str,
    static_report: dict[str, Any] | None = None,
    verification_report: dict[str, Any] | None = None,
    qa_report: dict[str, Any] | None = None,
    manifest: Manifest | None = None,
    *,
    include_unattributed: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The three reports narrowed to one service, as ``(static, verification, qa)``.

    Shaped this way so the narrowed reports go straight into the existing
    `prompts.developer_json_prompt.build_failure_evidence`: a per-service fix
    prompt is rendered by the same code that renders the whole-project one rather
    than by a second renderer that could drift from it. Section order, wording
    and truncation are therefore whatever that function already does — the only
    difference is that another service's failures are no longer in the input.

    ``include_unattributed`` adds the failures no service could be established
    for. The developer agent sets it, and has to: those failures reach every
    service today, and narrowing without them would be the one way this change
    could lose evidence rather than merely stop repeating it. It is off by
    default so a caller asking "what is wrong with *this* service" gets exactly
    that.

    Copies throughout — the originals belong to the run and other stages are
    still reading them. A report with nothing for this service comes back empty,
    which `build_failure_evidence` already renders as no section at all.
    """
    slug = UNATTRIBUTED if service == UNATTRIBUTED else _canonical(service)
    if not slug:
        return {}, {}, {}

    wanted = {slug}
    if include_unattributed:
        wanted.add(UNATTRIBUTED)

    index = _ManifestIndex(manifest)
    return (
        _narrow_static(static_report or {}, wanted),
        _narrow_verification(verification_report or {}, wanted),
        _narrow_qa(qa_report or {}, wanted, index),
    )


# ── Is this worth a stronger model? ──────────────────────────────

# A reviewer's opinion only counts at these severities. A style note is not
# evidence that the model was too weak to do the job.
SERIOUS_SEVERITIES = frozenset({"critical", "major"})


def escalation_reason(failures: Iterable[Failure]) -> str | None:
    """Why this service's evidence justifies a stronger model, or ``None``.

    The question is narrow and worth stating precisely: does this evidence say
    the *model* was inadequate, as opposed to that something around it went
    wrong? Only four things do, and they are checked most-objective first so the
    reason names the hardest fact available:

    - code that does not compile,
    - code that does not match the architecture a human approved,
    - tests that actually ran and actually failed,
    - a bug the reviewer called critical or major.

    Everything else is deliberately not grounds for escalation. A runner error —
    a dependency that would not install, a harness that never started — says the
    code was never executed, which is no evidence about the code at all. Minor
    review notes say the model did the job and could have done it more tidily.

    Conservative on purpose: a false negative costs one more ordinary attempt,
    while a false positive spends a more expensive model on a problem it cannot
    fix.
    """
    grouped: dict[str, list[Failure]] = {}
    for failure in failures:
        kind = _escalation_kind(failure)
        if kind:
            grouped.setdefault(kind, []).append(failure)

    for kind, label in (
        ("compile", "failed to compile"),
        ("contract", "does not match the approved architecture"),
        ("test", "failing test(s)"),
        ("review", "serious bug(s) reported"),
    ):
        found = grouped.get(kind)
        if found:
            return f"{len(found)} {label}" if kind != "compile" else label

    return None


def _escalation_kind(failure: Failure) -> str | None:
    """Which escalation-worthy category a failure falls in, if any."""
    if failure.source == SOURCE_STATIC:
        return "contract" if failure.check == CONTRACT_CHECK else "compile"
    if failure.source == SOURCE_TEST:
        return "test"
    if failure.source == SOURCE_QA and failure.severity.strip().lower() in SERIOUS_SEVERITIES:
        return "review"
    # SOURCE_RUNNER, and a minor review note, reach here and stay unescalated.
    return None


def justifies_escalation(exc: BaseException) -> bool:
    """Is a *failed call* itself grounds for a stronger model? Never.

    A rate limit, an exhausted account, a rejected key, a timeout, a request too
    large for the window: `llm.errors.classify` already sorts every one of these,
    and not one of its three dispositions describes generated code. They describe
    the provider. Answering a resource problem by paying for a better model would
    spend more to fail in exactly the same way.

    So escalation is driven only by verification evidence about code that was
    actually produced, and this exists to say that in one place rather than
    leaving it implicit. It also holds for a truncated or unparseable response:
    output size is `Settings.max_output_for`'s to decide, and treating a ceiling
    that was too small as proof the model was too weak would escalate the price
    of a problem Phase 4 already owns.
    """
    return False


# ── Static analysis ──────────────────────────────────────────────


def _static_failures(report: dict[str, Any]) -> Iterator[Failure]:
    """Attribute compile and lint failures.

    ``checks`` is preferred over the flattened ``failures`` list because each
    check states its own service, which needs no parsing and cannot be confused
    by a filename that happens to contain a slash. The flattened list is also
    capped at forty lines, so reading it would silently lose attribution on a
    badly broken run.

    A report that carries only the flattened list — one written by an older
    version, or a hand-built fixture — is read by its ``slug/path`` prefix
    instead, which is the shape `run_static_gate` produces.
    """
    checks = report.get("checks") or []

    if checks:
        for check in checks:
            service = _canonical(check.get("service") or "")
            name = str(check.get("name") or "")
            for text in check.get("failures") or []:
                yield Failure(
                    source=SOURCE_STATIC,
                    service=service or UNATTRIBUTED,
                    text=str(text),
                    file=_file_of_static_line(str(text)),
                    check=name,
                )
        return

    for text in report.get("failures") or []:
        line = str(text)
        prefix = line.split("/", 1)[0] if "/" in line else ""
        # A summary line such as "... and 12 more" names no service, and must not
        # be made to look as though it does.
        service = _canonical(prefix) if prefix and " " not in prefix else ""
        yield Failure(
            source=SOURCE_STATIC,
            service=service or UNATTRIBUTED,
            text=line,
            file=_file_of_static_line(line),
        )


def _file_of_static_line(line: str) -> str:
    """The ``service/path`` a static failure names, before the line number."""
    return line.split(":", 1)[0].strip() if ":" in line else ""


def _narrow_static(report: dict[str, Any], wanted: set[str]) -> dict[str, Any]:
    kept = [failure.text for failure in _static_failures(report) if failure.service in wanted]
    if not kept:
        return {}
    return {**_scalars(report, ("ran", "passed")), "failures": kept}


# ── Executed tests ───────────────────────────────────────────────


def _test_failures(report: dict[str, Any]) -> Iterator[Failure]:
    """Attribute real test results.

    ``ServiceTestResult.service`` is written by the test runner from the
    directory it ran in, so it is already canonical and already correct. A
    runner error — a dependency that would not install, an app that could not be
    imported — belongs to the same service, and is kept separate from a failing
    test because it says the harness never started rather than that the code is
    wrong.
    """
    for result in report.get("services") or []:
        service = _canonical(result.get("service") or "") or UNATTRIBUTED

        for failure in result.get("failures") or []:
            test = str(failure.get("test") or "").strip()
            message = str(failure.get("message") or "").strip()
            yield Failure(
                source=SOURCE_TEST,
                service=service,
                # Two original fields joined; neither is reworded or shortened.
                text=f"{test}: {message}" if test and message else (test or message),
                file=str(failure.get("file") or ""),
            )

        error = str(result.get("error") or "").strip()
        if error:
            yield Failure(source=SOURCE_RUNNER, service=service, text=error)


def _narrow_verification(report: dict[str, Any], wanted: set[str]) -> dict[str, Any]:
    kept = [
        result
        for result in report.get("services") or []
        if (_canonical(result.get("service") or "") or UNATTRIBUTED) in wanted
        and ((result.get("failures") or []) or str(result.get("error") or "").strip())
    ]
    if not kept:
        return {}
    # Deep-copied through the same helper the rest of the module uses, so the
    # caller cannot reach back into the run's own report.
    return {**_scalars(report, ("ran", "passed")), "services": [dict(r) for r in kept]}


# ── Reviewer bugs ────────────────────────────────────────────────


def _qa_failures(report: dict[str, Any], index: _ManifestIndex) -> Iterator[Failure]:
    """Attribute reviewer-reported bugs through the file they name.

    A bug carries a ``file_path`` and no service, so the manifest supplies the
    owner. The enclosing :class:`~schema.qa_schema.QAServiceReport` does name a
    service, and it is used — but only to settle a path that genuinely belongs to
    more than one service, and as the fallback when the path resolves to none.
    The path is the more specific claim, and a reviewer may legitimately point at
    a file outside the service it was reviewing.

    No service field is added to the QA data: everything needed is already there.
    """
    for service_report in report.get("service_reports") or []:
        enclosing = _canonical(service_report.get("service_name") or "")

        for bug in service_report.get("bugs") or []:
            path = str(bug.get("file_path") or "")
            for service in index.owners(path, prefer=enclosing):
                yield Failure(
                    source=SOURCE_QA,
                    service=service,
                    text=_bug_text(bug),
                    file=path,
                    severity=str(bug.get("severity") or ""),
                )


def _bug_text(bug: dict[str, Any]) -> str:
    """The bug as one line, composed of its own fields and nothing else."""
    severity = str(bug.get("severity") or "unknown").upper()
    path = str(bug.get("file_path") or "?")
    line = bug.get("line_number")
    where = f"{path} line {line}" if line not in (None, "") else path
    return f"[{severity}] {where}: {bug.get('description', '')}"


def _narrow_qa(report: dict[str, Any], wanted: set[str], index: _ManifestIndex) -> dict[str, Any]:
    kept: list[dict[str, Any]] = []

    for service_report in report.get("service_reports") or []:
        enclosing = _canonical(service_report.get("service_name") or "")
        bugs = [
            dict(bug)
            for bug in service_report.get("bugs") or []
            if not wanted.isdisjoint(index.owners(str(bug.get("file_path") or ""), prefer=enclosing))
        ]
        if bugs:
            kept.append({**{k: v for k, v in service_report.items() if k != "bugs"}, "bugs": bugs})

    if not kept:
        return {}
    return {**_scalars(report, ("critical_issues",)), "service_reports": kept}


# ── Manifest lookups ─────────────────────────────────────────────


class _ManifestIndex:
    """Which service owns which generated file.

    Built once per call so a report with many bugs does not walk the manifest
    once per bug, and so the lookup rules live in one place rather than being
    restated at every call site.
    """

    def __init__(self, manifest: Manifest | None) -> None:
        self._qualified: dict[str, str] = {}
        self._bare: dict[str, list[str]] = {}
        self._slugs: set[str] = set()

        for slug, entry in manifest_util.iter_files(manifest or {}):
            self._slugs.add(slug)
            bare = _normalise_path(entry.get("file_path", ""))
            if not bare:
                continue
            self._qualified.setdefault(f"{slug}/{bare}", slug)
            owners = self._bare.setdefault(bare, [])
            if slug not in owners:
                owners.append(slug)

        # A service with no files at all is still a service the run knows about.
        self._slugs.update(manifest_util.services(manifest or {}))

    def owners(self, raw_path: str, prefer: str = "") -> list[str]:
        """Every service a path implicates, most specific rule first.

        Returns ``[UNATTRIBUTED]`` rather than an empty list, so a bug whose file
        cannot be placed is preserved instead of silently dropped.
        """
        path = _normalise_path(raw_path)

        if path:
            # 1. The path is already qualified: "backend-api/app/main.py".
            owner = self._qualified.get(path)
            if owner:
                return [owner]

            # 2. It begins with a service this run has, even if the file itself
            #    is new — the developer may have added it since the manifest.
            head, _, rest = path.partition("/")
            if rest and head in self._slugs:
                return [head]

            # 3. A bare path such as "app/main.py". One owner is an answer; two
            #    is genuinely ambiguous, and the reviewing service settles it if
            #    it is among them. Otherwise every candidate is implicated,
            #    because the data does not say which.
            owners = self._bare.get(path)
            if owners:
                if len(owners) == 1:
                    return list(owners)
                if prefer and prefer in owners:
                    return [prefer]
                return sorted(owners)

        # 4. Nothing about the path resolved. The report the bug sits in names a
        #    service, and that is evidence rather than a guess -- but only if the
        #    run has such a service.
        if prefer and prefer in self._slugs:
            return [prefer]

        return [UNATTRIBUTED]


def _normalise_path(raw: str) -> str:
    """A model-supplied path in the form the manifest stores."""
    path = str(raw).replace("\\", "/").strip().lstrip("/")
    while path.startswith("./"):
        path = path[2:]
    return path


# ── Shared helpers ───────────────────────────────────────────────


def _canonical(name: str) -> str:
    """The slug for a service name, or ``""`` when it names nothing.

    Slugging is idempotent, so a caller may pass either "Backend API" or
    "backend-api" and reach the same key. ``"-"`` is the static gate's own
    placeholder for "no service", and must not become one.
    """
    cleaned = str(name).strip()
    if not cleaned or cleaned == "-":
        return ""
    return slugify(cleaned, fallback="")


def _deduplicated(failures: Iterator[Failure] | list[Failure]) -> list[Failure]:
    """Drop repeats while keeping the first occurrence in place.

    The same file can be flagged by two static checks, and the same line can
    reach us twice from a report assembled over several passes. Order is kept
    because it is meaningful: compiler output before opinion.
    """
    seen: set[tuple[str, str]] = set()
    unique: list[Failure] = []
    for failure in failures:
        key = (failure.source, failure.text)
        if key in seen:
            continue
        seen.add(key)
        unique.append(failure)
    return unique


def _scalars(report: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    """Copy the named top-level values a narrowed report should keep."""
    return {name: report[name] for name in names if name in report}
