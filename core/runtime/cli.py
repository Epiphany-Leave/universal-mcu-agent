#!/usr/bin/env python3
"""Command-line interface for the Universal MCU Agent runtime."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from runtime.engine import RuntimeEngine
    from runtime.artifact_resolver import ArtifactResolver
    from runtime.esp_dev_skill import EspDevSkillRouter
    from runtime.platforms.esp_idf import EspIdfAdapter
    from runtime.project_layout import create_project_layout, project_paths
    from runtime.review_budget import ReviewBudget
    from runtime.state_store import StateStore
    from runtime.transition_engine import TransitionEngine
else:
    from .engine import RuntimeEngine
    from .artifact_resolver import ArtifactResolver
    from .esp_dev_skill import EspDevSkillRouter
    from .platforms.esp_idf import EspIdfAdapter
    from .project_layout import create_project_layout, project_paths
    from .review_budget import ReviewBudget
    from .state_store import StateStore
    from .transition_engine import TransitionEngine


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="classify intent and create a gated work package")
    plan.add_argument("request")
    plan.add_argument("--mode", choices=["production", "prototype", "exploration"], default="production")
    plan.add_argument("--platform")
    plan.add_argument("--capabilities", help="JSON file containing a capabilities array")
    plan.add_argument("--assumptions", help="JSON file containing approved task-scoped EARs")
    prepare = subparsers.add_parser("prepare-task", help="prepare a bounded model context for a Task")
    prepare.add_argument("task")
    prepare.add_argument("package")
    prepare.add_argument("project_root")
    prepare.add_argument("--request", default="")
    prepare.add_argument("--product-root")
    prepare.add_argument("--manifest")
    prepare.add_argument("--esp-skill-root")
    prepare.add_argument("--platform")
    finalize = subparsers.add_parser("finalize-task", help="accept model output and optional verification")
    finalize.add_argument("task")
    finalize.add_argument("context")
    finalize.add_argument("model_result")
    finalize.add_argument("--product-root")
    finalize.add_argument("--manifest")
    finalize.add_argument("--output")
    plan.add_argument("--state-root", help="persist Intent and Work Package under this product root")
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
    resolve = subparsers.add_parser("resolve-artifact", help="resolve one canonical artifact role")
    resolve.add_argument("product_root")
    resolve.add_argument("role")
    resolve.add_argument("--scope", default="product")
    validate = subparsers.add_parser("validate-esp-idf", help="validate ESP-IDF project and component inclusion")
    validate.add_argument("project_root")
    validate.add_argument("--compile-commands")
    validate.add_argument("--build-succeeded", action="store_true")
    validate.add_argument("--expected-component", action="append", default=[])
    layout = subparsers.add_parser("project-layout", help="show or create the canonical project layout")
    layout.add_argument("product_root")
    layout.add_argument("project_name")
    layout.add_argument("--create", action="store_true")
    esp_skill = subparsers.add_parser("route-esp-skill", help="route an optional esp-dev-skill knowledge package")
    esp_skill.add_argument("request")
    esp_skill.add_argument("--product-root")
    esp_skill.add_argument("--skill-root")
    esp_skill.add_argument("--platform")
    args = parser.parse_args()

    if args.command == "plan":
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
        )
        if args.state_root:
            store = StateStore(args.state_root)
            intent = result["intent"]
            package = result["work_packages"][0]
            store.write(f".mcu-agent/runtime/intents/{intent['intent_id']}.json", intent)
            store.write(f".mcu-agent/runtime/work-packages/{package['work_package_id']}.json", package)
    elif args.command == "prepare-task":
        from runtime.task_executor import TaskExecutor
        task = json.loads(Path(args.task).read_text(encoding="utf-8-sig"))
        package = json.loads(Path(args.package).read_text(encoding="utf-8-sig"))
        intent_request = args.request or package.get("intent_request", package.get("target", ""))
        resolver = ArtifactResolver(args.product_root or args.project_root, args.manifest) if args.manifest else None
        result = TaskExecutor().prepare(
            task,
            request=intent_request,
            mode=package.get("mode", "production"),
            project_root=args.project_root,
            product_root=args.product_root,
            manifest=resolver,
            skill_root=args.esp_skill_root,
            platform=args.platform,
        )
    elif args.command == "finalize-task":
        from runtime.task_executor import TaskExecutor
        task = json.loads(Path(args.task).read_text(encoding="utf-8-sig"))
        context = json.loads(Path(args.context).read_text(encoding="utf-8-sig"))
        model_result = json.loads(Path(args.model_result).read_text(encoding="utf-8-sig"))
        resolver = ArtifactResolver(args.product_root, args.manifest) if args.manifest else None
        result = TaskExecutor().execute(
            task,
            context=context,
            model_runner=lambda _context: model_result,
            manifest=resolver,
        )
        if args.output:
            Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif args.command == "gate":
        package = json.loads(Path(args.work_package).read_text(encoding="utf-8-sig"))
        payload = json.loads(Path(args.capabilities).read_text(encoding="utf-8-sig"))
        capabilities = payload.get("capabilities", payload)
        assumptions = []
        if args.assumptions:
            payload = json.loads(Path(args.assumptions).read_text(encoding="utf-8-sig"))
            assumptions = payload.get("assumptions", payload)
        result = RuntimeEngine().regate(package, capabilities, assumptions)
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
        ledger = store.read(".mcu-agent/runtime/execution-ledger.json", None)
        budget = ReviewBudget(ledger)
        payload = json.loads(Path(args.artifacts).read_text(encoding="utf-8-sig"))
        artifacts = payload.get("artifacts", payload)
        conclusion = json.loads(Path(args.conclusion).read_text(encoding="utf-8-sig")) if args.conclusion else None
        result = budget.request(args.phase_id, artifacts, max_rounds=args.max_rounds, previous_conclusion=conclusion)
        store.write(".mcu-agent/runtime/execution-ledger.json", budget.ledger)
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
    else:
        paths = create_project_layout(args.product_root, args.project_name) if args.create else project_paths(args.product_root, args.project_name)
        result = {key: str(value) for key, value in paths.items()}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
