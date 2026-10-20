#!/usr/bin/env python3
"""Command-line interface for the Universal MCU Agent runtime."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from runtime.engine import RuntimeEngine
    from runtime.artifact_resolver import ArtifactResolver
    from runtime.esp_dev_skill import EspDevSkillRouter
    from runtime.memory import ProjectMemoryStore
    from runtime.lifecycle_service import LifecycleService
    from runtime.session_bootstrap import SessionBootstrap
    from runtime.platforms.esp_idf import EspIdfAdapter
    from runtime.project_layout import create_project_layout, project_paths
    from runtime.project_contract import ProjectContractManager
    from runtime.review_budget import ReviewBudget
    from runtime.state_store import StateStore
    from runtime.transition_engine import TransitionEngine
else:
    from .engine import RuntimeEngine
    from .artifact_resolver import ArtifactResolver
    from .esp_dev_skill import EspDevSkillRouter
    from .memory import ProjectMemoryStore
    from .lifecycle_service import LifecycleService
    from .session_bootstrap import SessionBootstrap
    from .platforms.esp_idf import EspIdfAdapter
    from .project_layout import create_project_layout, project_paths
    from .project_contract import ProjectContractManager
    from .review_budget import ReviewBudget
    from .state_store import StateStore
    from .transition_engine import TransitionEngine


def _split_rooted_identifiers(values, product_root, *, trailing=0):
    """Accept canonical PRODUCT WP TASK [payload] and flag-based compatibility forms."""
    short = 2 + trailing
    if len(values) == short + 1:
        if product_root:
            raise SystemExit("product root was supplied both positionally and with --product-root")
        root = values[0]
        body = values[1:]
    elif len(values) == short:
        root = product_root or "."
        body = values
    else:
        suffix = " PAYLOAD" if trailing else ""
        raise SystemExit(f"expected PRODUCT_ROOT WP_ID TASK_ID{suffix} (or use --product-root)")
    return Path(root).resolve(), body[0], body[1], body[2:]


def _claim_workspace(product_root: Path, claim_id: str) -> Path:
    claims = StateStore(product_root).root / "claims"
    if not claims.is_dir():
        raise SystemExit(f"unknown claim ID: {claim_id}")
    for path in claims.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("claim_id") != claim_id:
            continue
        if record.get("status") not in {"ACTIVE", "FINALIZED"}:
            raise SystemExit(f"claim is not runnable: {record.get('status')}")
        workspace = record.get("workspace_root")
        if not workspace or not Path(workspace).is_dir():
            raise SystemExit("claim has no available workspace")
        return Path(workspace).resolve()
    raise SystemExit(f"unknown claim ID: {claim_id}")


def _claim_credentials(service, work_package_id, task_id, *, claim_id, session_id, claim_revision):
    if claim_id:
        reference = service.resolve_claim_reference(work_package_id, task_id, claim_id)
        if session_id and session_id != reference["session_id"]:
            raise SystemExit("--session-id does not match --claim-id")
        if claim_revision is not None and claim_revision != reference["claim_revision"]:
            raise SystemExit("--claim-revision does not match --claim-id")
        return reference["session_id"], reference["claim_revision"]
    if not session_id or claim_revision is None:
        raise SystemExit("provide canonical --claim-id (or legacy --session-id and --claim-revision)")
    return session_id, claim_revision


def main() -> int:
    # JSON results may contain non-ASCII text; Windows consoles default to
    # legacy codepages (cp1252) that cannot encode them.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="classify intent and create a gated work package")
    plan.add_argument("request")
    plan.add_argument("--mode", choices=["production", "prototype", "exploration"], default="production")
    plan.add_argument("--platform")
    plan.add_argument("--capabilities", help="JSON file containing a capabilities array")
    plan.add_argument("--config", help="project config.yaml: auto-load confirmed mcu/hardware facts as capabilities")
    plan.add_argument("--assumptions", help="JSON file containing approved task-scoped EARs")
    plan.add_argument("--product-root", help="product root; mutable state defaults to PRODUCT/.mcu-agent/state")
    plan.add_argument("--project-root", help="project root used by ID-based Task claims")
    plan.add_argument("--session-id", help="session-bootstrap ID to bind to the persisted plan")
    prepare = subparsers.add_parser("prepare-task", help="prepare a bounded model context for a Task")
    prepare.add_argument("task")
    prepare.add_argument("package")
    prepare.add_argument("project_root")
    prepare.add_argument("--request", default="")
    prepare.add_argument("--product-root")
    prepare.add_argument("--manifest")
    prepare.add_argument("--esp-skill-root")
    prepare.add_argument("--platform")
    prepare.add_argument("--capabilities", help="JSON file containing a capabilities array")
    prepare.add_argument("--config", help="project config.yaml: auto-load confirmed facts as capabilities")
    finalize = subparsers.add_parser("finalize-task", help="finalize by WP/task ID (legacy three-file form remains accepted)")
    finalize.add_argument("identifiers", nargs="+", help="PRODUCT_ROOT WP_ID TASK_ID RESULT, flag-based IDs, or legacy three-file form")
    finalize.add_argument("--product-root")
    finalize.add_argument("--project-root")
    finalize.add_argument("--session-id")
    finalize.add_argument("--claim-id", help="opaque claim_id returned by claim-task (canonical)")
    finalize.add_argument("--claim-revision", type=int, help="deprecated numeric claim credential")
    finalize.add_argument("--result", dest="result_file", help="model result JSON for ID-based finalization")
    finalize.add_argument("--manifest")
    finalize.add_argument("--output")
    finalize.add_argument("--verify", action="store_true", help="legacy compatibility; ID finalization always runs the configured verifier")
    finalize.add_argument(
        "--allow-shell-command",
        action="store_true",
        help="explicitly allow legacy toolchain.build_command for this invocation (audited)",
    )
    finalize.add_argument("--config", help="project config.yaml containing structured verification profiles")
    plan.add_argument("--state-root", help="deprecated alias for --product-root")
    claim = subparsers.add_parser("claim-task", help="atomically claim a persisted Task by IDs")
    claim.add_argument("identifiers", nargs="+", help="PRODUCT_ROOT WP_ID TASK_ID (or WP_ID TASK_ID with --product-root)")
    claim.add_argument("--session-id", required=True)
    claim.add_argument("--product-root")
    claim.add_argument("--project-root")
    claim.add_argument("--revision", type=int, help="expected Work Package revision (CAS)")
    claim.add_argument("--config")
    claim.add_argument("--workspace", action="store_true", help="create a claim-specific Git worktree")
    promote = subparsers.add_parser("promote-task", help="promote one finalized claim workspace")
    promote.add_argument("identifiers", nargs="+", help="PRODUCT_ROOT WP_ID TASK_ID (or WP_ID TASK_ID with --product-root)")
    promote.add_argument("--claim-id", required=True)
    promote.add_argument("--product-root")
    abandon = subparsers.add_parser("abandon-task", help="abandon a matching active Task claim")
    abandon.add_argument("identifiers", nargs="+", help="PRODUCT_ROOT WP_ID TASK_ID (or WP_ID TASK_ID with --product-root)")
    abandon.add_argument("--claim-id", help="opaque claim_id returned by claim-task (canonical)")
    abandon.add_argument("--session-id")
    abandon.add_argument("--claim-revision", type=int, help="deprecated numeric claim credential")
    abandon.add_argument("--reason", required=True)
    abandon.add_argument("--product-root")
    attest = subparsers.add_parser("attest-target", help="attach validated manual HIL evidence to a claim")
    attest.add_argument("identifiers", nargs="+", help="PRODUCT_ROOT WP_ID TASK_ID EVIDENCE (or WP_ID TASK_ID EVIDENCE with --product-root)")
    attest.add_argument("--claim-id", help="opaque claim_id returned by claim-task (canonical)")
    attest.add_argument("--session-id")
    attest.add_argument("--claim-revision", type=int, help="deprecated numeric claim credential")
    attest.add_argument("--product-root")
    gate = subparsers.add_parser("gate", help="re-evaluate an existing work package")
    gate.add_argument("work_package")
    gate.add_argument("capabilities")
    gate.add_argument("--assumptions")
    gate.add_argument("--output")
    transition = subparsers.add_parser("transition", help="apply a runtime-owned state transition")
    transition.add_argument("kind", choices=["phase", "work_package", "task"])
    transition.add_argument("record")
    transition.add_argument("target")
    transition.add_argument("--reopen", help="JSON file containing phase reopen authority")
    review = subparsers.add_parser("review", help="apply evidence review budget")
    review.add_argument("phase_id")
    review.add_argument("artifacts", help="JSON file containing artifact identities")
    review.add_argument("--state-root", default=".")
    review.add_argument("--max-rounds", type=int, default=2)
    review.add_argument("--conclusion", help="JSON file containing the completed review conclusion")
    artifact = subparsers.add_parser("artifact", help="register, list or resolve canonical artifacts")
    artifact_actions = artifact.add_subparsers(dest="artifact_action", required=True)
    artifact_register = artifact_actions.add_parser("register", help="register one explicit input artifact")
    artifact_register.add_argument("product_root")
    artifact_register.add_argument("--role", required=True)
    artifact_register.add_argument("--path", required=True)
    artifact_register.add_argument("--scope", default="product")
    artifact_register.add_argument("--id", dest="artifact_id")
    artifact_register.add_argument("--logical-name")
    artifact_register.add_argument("--media-type")
    artifact_register.add_argument("--revision", type=int, help="expected manifest revision (CAS)")
    artifact_list = artifact_actions.add_parser("list", help="list canonical artifacts")
    artifact_list.add_argument("product_root")
    artifact_list.add_argument("--scope")
    artifact_list.add_argument("--current", action="store_true")
    artifact_resolve = artifact_actions.add_parser("resolve", help="resolve one canonical artifact role")
    artifact_resolve.add_argument("product_root")
    artifact_resolve.add_argument("--role", required=True)
    artifact_resolve.add_argument("--scope", default="product")
    resolve = subparsers.add_parser("resolve-artifact", help="resolve one canonical artifact role (legacy alias)")
    resolve.add_argument("product_root")
    resolve.add_argument("role")
    resolve.add_argument("--scope", default="product")
    validate = subparsers.add_parser("validate-esp-idf", help="validate ESP-IDF project and component inclusion")
    validate.add_argument("project_root")
    validate.add_argument("--compile-commands")
    validate.add_argument("--build-succeeded", action="store_true")
    validate.add_argument("--expected-component", action="append", default=[])
    ioc = subparsers.add_parser("extract-ioc-facts", help="STM32: derive capability facts from a CubeMX .ioc file")
    ioc.add_argument("ioc_path")
    ioc.add_argument("--output", help="write capabilities JSON to this file (for plan --capabilities)")
    capability = subparsers.add_parser("capability", help="manage the project capability store (.mcu-agent/state/capabilities.json)")
    capability.add_argument("product_root")
    capability.add_argument("action", choices=["list", "add", "clear"])
    capability.add_argument("--input", help="JSON file with a capabilities array (or {capabilities: [...]}) to merge in")
    capability.add_argument("--revision", type=int, help="expected capability-store revision (CAS)")
    project_contract = subparsers.add_parser("project-contract", help="inspect or explicitly migrate a project contract")
    project_contract_actions = project_contract.add_subparsers(dest="project_contract_action", required=True)
    contract_inspect = project_contract_actions.add_parser("inspect")
    contract_inspect.add_argument("product_root")
    contract_propose = project_contract_actions.add_parser("propose-migration")
    contract_propose.add_argument("product_root")
    contract_propose.add_argument("project_name")
    contract_propose.add_argument("--output")
    contract_apply = project_contract_actions.add_parser("apply-migration")
    contract_apply.add_argument("product_root")
    contract_apply.add_argument("proposal")
    contract_apply.add_argument("--approve", action="store_true")
    layout = subparsers.add_parser("project-layout", help="show or create the canonical project layout")
    layout.add_argument("product_root")
    layout.add_argument("project_name")
    layout.add_argument("--create", action="store_true")
    layout.add_argument("--config", help="project config.yaml: read directories.* for layout paths")
    layout.add_argument("--platform", help="platform family recorded in the tracked project contract")
    layout.add_argument("--chip", help="exact MCU chip recorded in the tracked project contract")
    esp_skill = subparsers.add_parser("route-esp-skill", help="route an optional esp-dev-skill knowledge package")
    esp_skill.add_argument("request")
    esp_skill.add_argument("--product-root")
    esp_skill.add_argument("--skill-root")
    esp_skill.add_argument("--platform")
    update_config = subparsers.add_parser("update-config", help="update facts in the project config.yaml (preserves comments)")
    update_config.add_argument("config_path", help="path to the project config.yaml")
    update_config.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="e.g. --set mcu.chip=esp32s31 --set hardware.camera=OV3660")
    verify_repo = subparsers.add_parser("verify-repository", help="independently verify repository governance for CI")
    verify_repo.add_argument("product_root")
    verify_repo.add_argument("--strict", action="store_true")
    verify_repo.add_argument("--base", required=True)
    verify_repo.add_argument("--head", required=True)
    host_run = subparsers.add_parser("host-run", help="launch a supported host with the pinned MCU Skill")
    host_run.add_argument("--agent", choices=["pi"], required=True)
    host_run.add_argument("--product-root", default=".")
    host_run.add_argument("--request", required=True)
    host_run.add_argument("--claim-id")
    bootstrap = subparsers.add_parser("session-bootstrap", help="acknowledge the host runtime entry for a session")
    bootstrap.add_argument("product_root_pos", nargs="?", help="legacy positional product root")
    bootstrap.add_argument("--product-root", dest="product_root_opt")
    bootstrap.add_argument("--project-root")
    bootstrap.add_argument("--agent", choices=["auto", "claude-code", "codex", "opencode", "cursor", "pi"], default="auto")
    bootstrap.add_argument("--mode", choices=["production", "prototype", "exploration"], default="production")
    memory_capture = subparsers.add_parser("memory-capture", help="capture a project-scoped memory draft")
    memory_capture.add_argument("product_root")
    memory_capture.add_argument("summary")
    memory_capture.add_argument("--problem", default="")
    memory_capture.add_argument("--solution", default="")
    memory_capture.add_argument("--task", default="")
    memory_capture.add_argument("--work-package", default="")
    memory_capture.add_argument("--platform", default="")
    memory_capture.add_argument("--chip", default="")
    memory_capture.add_argument("--source-file", action="append", default=[])
    memory_capture.add_argument("--source-artifact", action="append", default=[])
    memory_capture.add_argument("--verification", default="")
    memory_capture.add_argument("--confidence", choices=["verified", "draft", "experimental"], default="draft")
    memory_capture.add_argument("--tag", action="append", default=[])
    memory_search = subparsers.add_parser("memory-search", help="search project-scoped memories")
    memory_search.add_argument("product_root")
    memory_search.add_argument("query")
    memory_search.add_argument("--platform")
    memory_search.add_argument("--limit", type=int, default=5)
    memory_approve = subparsers.add_parser("memory-approve", help="approve a project memory")
    memory_approve.add_argument("product_root")
    memory_approve.add_argument("memory_id")
    memory_approve.add_argument("--reviewer", required=True)
    memory_approve.add_argument("--notes", default="")
    memory_promote = subparsers.add_parser("memory-promote", help="promote an approved memory to project public knowledge")
    memory_promote.add_argument("product_root")
    memory_promote.add_argument("memory_id")
    memory_promote.add_argument("--target", choices=["common", "platform"], required=True)
    memory_promote.add_argument("--reviewer", required=True)
    args = parser.parse_args()

    if args.command == "plan":
        product_root = Path(args.product_root or args.state_root or ".").resolve()
        project_root = Path(args.project_root).resolve() if args.project_root else product_root
        config_path = args.config or _default_config(product_root)
        capabilities = []
        if args.capabilities:
            payload = json.loads(Path(args.capabilities).read_text(encoding="utf-8-sig"))
            capabilities = payload.get("capabilities", payload)
        assumptions = []
        if args.assumptions:
            payload = json.loads(Path(args.assumptions).read_text(encoding="utf-8-sig"))
            assumptions = payload.get("assumptions", payload)
        result = RuntimeEngine().plan(
            args.request,
            mode=args.mode,
            platform=args.platform,
            capabilities=capabilities,
            assumptions=assumptions,
            config_path=config_path,
            capability_store=str(StateStore(product_root).root / "capabilities.json"),
            project_root=str(project_root),
            product_root=str(product_root),
        )
        effective_project_root = Path(result.get("project_root") or project_root).resolve()
        result = LifecycleService(product_root, config_path=config_path).persist_plan(
            result, project_root=effective_project_root, session_id=args.session_id
        )
    elif args.command == "prepare-task":
        print("warning: file-oriented prepare-task is deprecated; use claim-task by IDs", file=sys.stderr)
        from runtime.task_executor import TaskExecutor
        task = json.loads(Path(args.task).read_text(encoding="utf-8-sig"))
        package = json.loads(Path(args.package).read_text(encoding="utf-8-sig"))
        intent_request = args.request or package.get("intent_request", package.get("target", ""))
        product = args.product_root or args.project_root
        resolver = None
        if args.manifest:
            resolver = ArtifactResolver(product, args.manifest)
        elif (Path(product) / ".mcu-agent" / "artifact-manifest.yaml").is_file():
            resolver = ArtifactResolver(product)
        capabilities = []
        if args.capabilities:
            payload = json.loads(Path(args.capabilities).read_text(encoding="utf-8-sig"))
            capabilities = payload.get("capabilities", payload)
        elif args.config:
            from runtime.config_loader import load_capabilities
            capabilities = load_capabilities(args.config)
        from runtime.config_loader import validate_capabilities
        validate_capabilities(capabilities)
        result = TaskExecutor().prepare(
            task,
            request=intent_request,
            mode=package.get("mode", "production"),
            project_root=args.project_root,
            product_root=args.product_root,
            manifest=resolver,
            skill_root=args.esp_skill_root,
            platform=args.platform,
            capabilities=capabilities,
        )
    elif args.command == "finalize-task":
        from runtime.task_executor import TaskExecutor
        legacy_files = (
            len(args.identifiers) == 3
            and not args.claim_id
            and not args.session_id
            and args.claim_revision is None
            and not args.result_file
        )
        if legacy_files:
            # Deprecated file-oriented compatibility path retained for v0.7.x.
            print("warning: file-oriented finalize-task is deprecated; use product/WP/task IDs", file=sys.stderr)
            task_path, context_path, model_path = args.identifiers
            task = json.loads(Path(task_path).read_text(encoding="utf-8-sig"))
            context = json.loads(Path(context_path).read_text(encoding="utf-8-sig"))
            governed_product = Path(context.get("product_root") or args.product_root or ".").resolve()
            if (governed_product / ".mcu-agent" / "project-contract.json").is_file():
                raise SystemExit("legacy file-oriented finalize is disabled for governed v0.8 products")
            model_result = json.loads(Path(model_path).read_text(encoding="utf-8-sig"))
            resolver = ArtifactResolver(args.product_root, args.manifest) if args.manifest else None
            build_verifier = None
            if args.verify:
                from runtime.verifier import BuildVerifier
                build_verifier = BuildVerifier(
                    config_path=args.config,
                    allow_shell_command=args.allow_shell_command,
                )
            result = TaskExecutor().execute(
                task, context=context, model_runner=lambda _context: model_result,
                manifest=resolver, build_verifier=build_verifier,
            )
        else:
            if args.result_file:
                product_root, work_package_id, task_id, tail = _split_rooted_identifiers(
                    args.identifiers, args.product_root
                )
                result_file = args.result_file
            else:
                product_root, work_package_id, task_id, tail = _split_rooted_identifiers(
                    args.identifiers, args.product_root, trailing=1
                )
                result_file = tail[0]
            model_result = json.loads(Path(result_file).read_text(encoding="utf-8-sig"))
            config_path = args.config or _default_config(product_root)
            service = LifecycleService(product_root, config_path=config_path)
            session_id, claim_revision = _claim_credentials(
                service, work_package_id, task_id,
                claim_id=args.claim_id, session_id=args.session_id,
                claim_revision=args.claim_revision,
            )
            from runtime.verifier import BuildVerifier
            build_verifier = BuildVerifier(
                config_path=config_path,
                allow_shell_command=args.allow_shell_command,
            )
            resolver = ArtifactResolver(product_root, args.manifest) if args.manifest else None
            result = service.finalize_task(
                work_package_id, task_id, session_id=session_id,
                claim_revision=claim_revision, model_result=model_result,
                verifier=build_verifier, manifest=resolver,
            )
        if args.output:
            StateStore._atomic_replace(Path(args.output), (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    elif args.command == "claim-task":
        product_root, work_package_id, task_id, _ = _split_rooted_identifiers(
            args.identifiers, args.product_root
        )
        config_path = args.config or _default_config(product_root)
        result = LifecycleService(product_root, config_path=config_path).claim_task(
            work_package_id, task_id, session_id=args.session_id,
            project_root=args.project_root, expected_revision=args.revision,
            workspace=args.workspace,
        )
    elif args.command == "promote-task":
        product_root, work_package_id, task_id, _ = _split_rooted_identifiers(
            args.identifiers, args.product_root
        )
        result = LifecycleService(product_root).promote_task(
            work_package_id, task_id, claim_id=args.claim_id,
        )
    elif args.command == "abandon-task":
        product_root, work_package_id, task_id, _ = _split_rooted_identifiers(
            args.identifiers, args.product_root
        )
        service = LifecycleService(product_root)
        session_id, claim_revision = _claim_credentials(
            service, work_package_id, task_id,
            claim_id=args.claim_id, session_id=args.session_id,
            claim_revision=args.claim_revision,
        )
        result = service.abandon_task(
            work_package_id, task_id, session_id=session_id,
            claim_revision=claim_revision, reason=args.reason,
        )
    elif args.command == "attest-target":
        product_root, work_package_id, task_id, tail = _split_rooted_identifiers(
            args.identifiers, args.product_root, trailing=1
        )
        evidence = json.loads(Path(tail[0]).read_text(encoding="utf-8-sig"))
        service = LifecycleService(product_root)
        session_id, claim_revision = _claim_credentials(
            service, work_package_id, task_id,
            claim_id=args.claim_id, session_id=args.session_id,
            claim_revision=args.claim_revision,
        )
        result = service.attest_target(
            work_package_id, task_id, session_id=session_id,
            claim_revision=claim_revision, evidence=evidence,
        )
    elif args.command == "capability":
        from runtime.config_loader import load_capability_store, merge_capabilities, validate_capabilities
        state = StateStore(args.product_root)
        store_path = state.root / "capabilities.json"
        current_payload = state.read("capabilities.json", {"capabilities": []})
        current = load_capability_store(store_path)
        current_revision = int(current_payload.get("revision", 0)) if isinstance(current_payload, dict) else 0
        if args.action == "list":
            result = {
                "path": str(store_path),
                "revision": current_revision,
                "count": len(current),
                "capabilities": current,
            }
        else:
            if args.action == "add":
                if not args.input:
                    raise SystemExit("capability add requires --input")
                payload = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
                incoming = payload.get("capabilities", payload) if isinstance(payload, dict) else payload
            else:
                incoming = []

            def mutate(existing):
                existing_capabilities = (
                    existing.get("capabilities", []) if isinstance(existing, dict) else existing or []
                )
                values = [] if args.action == "clear" else merge_capabilities(existing_capabilities, incoming)
                validate_capabilities(values)
                return {"schema_version": "2.0", "capabilities": values}

            updated = state.update(
                "capabilities.json",
                mutate,
                default={"schema_version": "2.0", "capabilities": []},
                expected_revision=args.revision,
            )
            result = {
                "path": str(store_path),
                "revision": updated["revision"],
                "count": len(updated["capabilities"]),
                "capabilities": updated["capabilities"],
            }
    elif args.command == "extract-ioc-facts":
        from runtime.platforms.stm32 import extract_facts
        result = extract_facts(args.ioc_path)
        if args.output:
            Path(args.output).write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    elif args.command == "update-config":
        from runtime.config_loader import update_config
        updates = {}
        for item in args.set:
            if "=" not in item:
                raise SystemExit(f"invalid --set (expected KEY=VALUE): {item}")
            key, value = item.split("=", 1)
            updates[key.strip()] = value.strip()
        changed = update_config(args.config_path, updates)
        result = {"changed": changed, "config": args.config_path}
    elif args.command == "gate":
        package = json.loads(Path(args.work_package).read_text(encoding="utf-8-sig"))
        from runtime.contract_validator import validate_contract
        validate_contract(package, "work-package.schema.json", label="Work Package")
        payload = json.loads(Path(args.capabilities).read_text(encoding="utf-8-sig"))
        capabilities = payload.get("capabilities", payload)
        assumptions = []
        if args.assumptions:
            payload = json.loads(Path(args.assumptions).read_text(encoding="utf-8-sig"))
            assumptions = payload.get("assumptions", payload)
        result = RuntimeEngine().regate(package, capabilities, assumptions)
        validate_contract(result, "work-package.schema.json", label="Work Package")
        if args.output:
            Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif args.command == "transition":
        record_path = Path(args.record)
        record = json.loads(record_path.read_text(encoding="utf-8-sig"))
        reopen = json.loads(Path(args.reopen).read_text(encoding="utf-8-sig")) if args.reopen else None
        result = TransitionEngine().transition(args.kind, record, args.target, reopen=reopen)
        record_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif args.command == "review":
        store = StateStore(args.state_root)
        payload = json.loads(Path(args.artifacts).read_text(encoding="utf-8-sig"))
        artifacts = payload.get("artifacts", payload)
        conclusion = json.loads(Path(args.conclusion).read_text(encoding="utf-8-sig")) if args.conclusion else None
        outcome = {}

        def apply_review(ledger):
            budget = ReviewBudget(ledger)
            outcome["result"] = budget.request(
                args.phase_id,
                artifacts,
                max_rounds=args.max_rounds,
                previous_conclusion=conclusion,
            )
            from runtime.contract_validator import validate_contract
            validate_contract(budget.ledger, "execution-ledger.schema.json", label="Execution ledger")
            return budget.ledger

        store.update(
            "execution-ledger.json",
            apply_review,
            default={"schema_version": "2.0", "reviews": {}},
        )
        result = outcome["result"]
    elif args.command == "artifact":
        resolver = ArtifactResolver(args.product_root)
        if args.artifact_action == "register":
            media_type = args.media_type or mimetypes.guess_type(args.path)[0] or "application/octet-stream"
            artifact_record = resolver.register_input(
                role=args.role,
                path=args.path,
                scope=args.scope,
                artifact_id=args.artifact_id,
                logical_name=args.logical_name,
                media_type=media_type,
                expected_revision=args.revision,
            )
            result = {
                "manifest_revision": resolver.manifest["revision"],
                "artifact": artifact_record,
            }
        elif args.artifact_action == "list":
            values = resolver.list(scope=args.scope, current_only=args.current)
            result = {"manifest_revision": resolver.manifest["revision"], "count": len(values), "artifacts": values}
        else:
            result = resolver.resolve(args.role, scope=args.scope)
    elif args.command == "resolve-artifact":
        result = ArtifactResolver(args.product_root).resolve(args.role, scope=args.scope)
    elif args.command == "validate-esp-idf":
        compile_commands = Path(args.compile_commands) if args.compile_commands else None
        result = EspIdfAdapter().validate(
            Path(args.project_root),
            compile_commands=compile_commands,
            build_succeeded=True if args.build_succeeded else None,
            expected_components=args.expected_component,
        )
    elif args.command == "route-esp-skill":
        result = EspDevSkillRouter(args.skill_root, product_root=args.product_root).route(
            args.request, platform=args.platform
        )
    elif args.command == "verify-repository":
        from runtime.repository_verifier import RepositoryVerifier
        result = RepositoryVerifier(args.product_root).verify(
            base=args.base, head=args.head, strict=args.strict,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 2
    elif args.command == "host-run":
        product_root = Path(args.product_root).resolve()
        skill = product_root / ".agents" / "skills" / "mcu-agent" / "SKILL.md"
        if not skill.is_file():
            raise SystemExit(f"managed MCU Skill is not installed: {skill}")
        host = shutil.which("pi")
        if not host:
            raise SystemExit("pi executable was not found")
        cwd = _claim_workspace(product_root, args.claim_id) if args.claim_id else product_root
        environment = os.environ.copy()
        environment["MCU_AGENT_PRODUCT_ROOT"] = str(product_root)
        if args.claim_id:
            environment["MCU_AGENT_CLAIM_ID"] = args.claim_id
        return subprocess.call(
            [host, "--approve", "--skill", str(skill), args.request],
            cwd=cwd, env=environment,
        )
    elif args.command == "session-bootstrap":
        product_root = args.product_root_opt or args.product_root_pos or "."
        result = SessionBootstrap().start(
            product_root, agent=args.agent, mode=args.mode, project_root=args.project_root
        )
    elif args.command == "memory-capture":
        verification = json.loads(args.verification) if args.verification else None
        result = ProjectMemoryStore(args.product_root).capture(
            summary=args.summary,
            problem=args.problem,
            solution=args.solution,
            source_task=args.task,
            work_package=args.work_package,
            platform=args.platform,
            chip=args.chip,
            source_files=args.source_file,
            source_artifacts=args.source_artifact,
            verification=verification,
            confidence=args.confidence,
            tags=args.tag,
        )
    elif args.command == "memory-search":
        result = ProjectMemoryStore(args.product_root).search(args.query, platform=args.platform, limit=args.limit)
    elif args.command == "memory-approve":
        result = ProjectMemoryStore(args.product_root).approve(args.memory_id, reviewer=args.reviewer, notes=args.notes)
    elif args.command == "memory-promote":
        result = ProjectMemoryStore(args.product_root).promote(
            args.memory_id, target=args.target, reviewer=args.reviewer
        )
    elif args.command == "project-contract":
        manager = ProjectContractManager(args.product_root)
        if args.project_contract_action == "inspect":
            contract = manager.load()
            result = {
                "contract": contract,
                "contract_digest": manager.digest(contract) if contract else None,
                "project_status": manager.evaluate(contract),
            }
        elif args.project_contract_action == "propose-migration":
            from runtime.project_migration import ProjectMigrationService
            result = ProjectMigrationService(args.product_root).propose(args.project_name)
            if args.output:
                StateStore._atomic_replace(
                    Path(args.output),
                    (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                )
        else:
            from runtime.project_migration import ProjectMigrationService
            proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8-sig"))
            result = ProjectMigrationService(args.product_root).apply(proposal, approved=args.approve)
    elif args.command == "project-layout":
        if args.create:
            paths = create_project_layout(args.product_root, args.project_name, config_path=args.config)
            manager = ProjectContractManager(args.product_root)
            contract = manager.create(
                project_name=args.project_name,
                paths=paths,
                platform=args.platform,
                chip=args.chip,
            )
            status = manager.persist_status(manager.evaluate(contract))
            result = {
                "paths": {key: str(value) for key, value in paths.items()},
                "contract": contract,
                "contract_digest": manager.digest(contract),
                "project_status": status,
            }
        else:
            paths = project_paths(args.product_root, args.project_name, config_path=args.config)
            manager = ProjectContractManager(args.product_root)
            contract = manager.load()
            result = {
                "paths": {key: str(value) for key, value in paths.items()},
                "contract": contract,
                "contract_digest": manager.digest(contract) if contract else None,
                "project_status": manager.evaluate(contract),
            }
    else:
        raise SystemExit(f"unsupported command: {args.command}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _default_config(product_root: Path) -> str | None:
    for candidate in (product_root / ".mcu-agent" / "config.yaml", product_root / "config.yaml"):
        if candidate.is_file():
            return str(candidate)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
