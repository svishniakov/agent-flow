#!/usr/bin/env python3
"""Run the Agent Flow repository validation suite."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PRODUCT_SEARCH_PATHS = [
    "README.md",
    "README.ru.md",
    "SKILL.md",
    "docs",
    "references",
    "scripts",
    "agents",
    "registries",
]

ARCHITECTURE_DESIGN_CORE_GUARD_TERMS = [
    "Architecture Design Mode",
    "Architecture Design Brief",
    "architecture_design_brief",
    "Selected Matrix Facets",
    "Status: approved",
]
ARCHITECTURE_DESIGN_ROLE_GUARD_TERMS = [
    "Architecture Design Mode",
    "Architecture Design Brief",
    "Selected Matrix Facets",
]
ARCHITECTURE_CAPABILITY_GUARD_TERMS = [
    "Architecture Capability Router",
    "architecture_capabilities",
    "Soft Skill Binding",
    "recommended_skills",
]

REQUIRED_RUNTIME_TEXT = {
    "SKILL.md": [
        "anywhere in the latest user request",
        "AgentFlow",
        "Task Status Completion Gate",
        "task status normalization pass",
        "Evidence Records",
        "Architecture Contract Gate",
        *ARCHITECTURE_DESIGN_CORE_GUARD_TERMS,
        "Architecture Matrix",
        "architecture_context",
        "product_context",
        "application_surface",
        "architecture_pattern",
        "stack_runtime",
        "risk_gates",
        "verification_gates",
        *ARCHITECTURE_CAPABILITY_GUARD_TERMS,
        "Architecture Context Propagation",
        "matrix_facets",
        "Architecture Execution Control",
        "Architecture Approval Gate",
        "Local Best Practice auto gate",
        "regression demotion",
        "Model/reasoning upgrade is not the default fix",
        "standard` traceable runs with at least two worker lanes",
    ],
    "references/definition-of-done.md": [
        "Task Status Completion Gate",
        "Status: done",
        "Evidence Records",
        "Architecture Contract Gate",
        *ARCHITECTURE_DESIGN_CORE_GUARD_TERMS,
        "Architecture Matrix",
        "architecture_context",
        *ARCHITECTURE_CAPABILITY_GUARD_TERMS,
        "Architecture Context Propagation",
        "matrix_facets",
        "Architecture Execution Control",
        "Architecture Compliance",
        "Architecture Invariants",
        "Architecture Matrix Mismatches",
        "Contract Drift",
        "Architecture Approval Gate",
        "Local Best Practice auto gate",
        "regression demotion",
        "Model/reasoning upgrade is not the default fix",
        "at least two worker lanes",
    ],
    "references/project-memory-and-env.md": [
        "normalize stale completed sections",
        "classify it as `uncertain`",
    ],
    "references/orchestrator.md": [
        "anywhere in the latest request",
        "AgentFlow",
        "Normalize stale completed task sections",
        "Task Status Completion Gate",
        "Evidence Records",
        "Architecture Contract Gate",
        *ARCHITECTURE_DESIGN_CORE_GUARD_TERMS,
        "Architecture Matrix",
        "architecture_context",
        "product_context",
        "application_surface",
        "architecture_pattern",
        "stack_runtime",
        "risk_gates",
        "verification_gates",
        *ARCHITECTURE_CAPABILITY_GUARD_TERMS,
        "Architecture Context Propagation",
        "matrix_facets",
        "Architecture Execution Control",
        "Architecture Compliance",
        "Architecture Invariants",
        "Architecture Matrix Mismatches",
        "Contract Drift",
        "Architecture Approval Gate",
        "Local Best Practice auto gate",
        "regression demotion",
        "Model/reasoning upgrade is not the default fix",
        "two or more worker lanes",
    ],
    "references/delegation.md": [
        "Architecture Contract Gate",
        *ARCHITECTURE_DESIGN_CORE_GUARD_TERMS,
        "Architecture Matrix",
        "architecture_context",
        "product_context",
        "application_surface",
        "architecture_pattern",
        "stack_runtime",
        "risk_gates",
        "verification_gates",
        *ARCHITECTURE_CAPABILITY_GUARD_TERMS,
        "Architecture Context Propagation",
        "matrix_facets",
        "Architecture Execution Control",
        "Architecture Compliance",
        "Architecture Invariants",
        "Architecture Matrix Mismatches",
        "Contract Drift",
        "Architecture Approval Gate",
        "Local Best Practice auto gate",
        "Model/reasoning upgrade is not the default fix",
        "`budget`",
        "two or more worker lanes",
    ],
    "references/traceable-runs.md": [
        "`schema_version` is `1` or `2`",
        "`budget` is required",
        "`architecture`",
        "`architecture_contract_required`",
        *ARCHITECTURE_DESIGN_CORE_GUARD_TERMS,
        "`Selected Architecture`",
        "Architecture Matrix",
        "architecture_context",
        *ARCHITECTURE_CAPABILITY_GUARD_TERMS,
        "Architecture Context Propagation",
        "matrix_facets",
        "markdown source of truth",
        "Architecture Execution Control",
        "`architecture_compliance`",
        "Architecture Compliance",
        "Architecture Invariants",
        "Architecture Matrix Mismatches",
        "Contract Drift",
    ],
    "references/architecture-matrix.md": [
        "Architecture Matrix",
        "architecture_context",
        "product_context",
        "application_surface",
        "architecture_pattern",
        "stack_runtime",
        "risk_gates",
        "verification_gates",
        "Product Context",
        "Application Surface",
        "Architecture Pattern",
        "Stack Runtime",
        "Risk Gates",
        "Verification Gates",
        "saas-service",
        "workflow-automation",
        "crypto-payments",
        "frontend-service",
        "full-stack-service",
        "backoffice-ui",
        "landing",
        "mobile-app",
        "desktop-app",
        "browser-extension",
        "wallet-payment-surface",
        "crypto-ops-console",
        "embedded-mini-app",
        "social-community-surface",
        "iot-device-fleet",
        "monolith",
        "microservices",
        "server-rendered-web",
        "web-app-shell",
        "mobile-layered-app",
        "desktop-shell-app",
        "browser-extension-architecture",
        "hosted-mini-app",
        "social-feed-graph",
        "iot-edge-cloud",
        "event-driven-architecture",
        "event-sourcing",
        "blockchain-payment-adapter",
        "smart-contract-boundary",
        "chain-indexer-reconciliation",
        "event-messaging-runtime",
        "blockchain-runtime",
        "TON",
        "TRON",
        "Ethereum",
        "Jettons",
        "TRC-20",
        "ERC-20",
        "Kafka",
        "RabbitMQ",
        "BullMQ",
        "api-contract",
        "frontend-build",
        "full-stack-flow",
        "landing-seo-performance",
        "browser-extension-smoke",
        "module-boundary-regression",
        "service-contract",
        "event-broker-contract",
        "event-replay-projection",
        "wallet-signing-smoke",
        "testnet-transaction-lifecycle",
        "smart-contract-interface",
        "chain-reconciliation-replay",
        "custody-secrets-review",
        "fee-resource-simulation",
        "tenant-isolation",
        "subscription-entitlements",
        "auth-permissions",
        "financial-data-integrity",
        "custody-key-management",
        "chain-finality-confirmations",
        "token-contract-integrity",
        "rpc-indexer-drift",
        "crypto-fee-liquidity",
        "crypto-compliance-controls",
        "client-server-contract-drift",
        "public-web-consent",
        "browser-extension-permissions",
        "module-boundary-erosion",
        "distributed-consistency",
        "event-delivery-replay",
        "evidence-source-integrity",
        "graph-workflow-runtime",
        "browser-extension-mv3",
    ],
    "references/architecture-capability-router.md": [
        *ARCHITECTURE_CAPABILITY_GUARD_TERMS,
        "Architecture Matrix",
        "architecture_context",
        "Architecture Design Brief",
        "Architecture Contract",
        "Execution Plan",
        "Selected Architecture",
        "Architecture Matrix Mismatches",
        "Contract Drift",
        "do not add skill hints",
    ],
    "registries/architecture-capabilities.json": [
        "saas-platform-architecture",
        "go-backend-service-architecture",
        "modular-monolith-architecture",
        "event-platform-architecture",
        "crypto-payment-architecture",
        "recommended_skills",
        "matrix_facets",
        "contract_sections",
    ],
    "agents/orchestrator.md": [
        "Normalize stale completed `todo.md` sections",
        "project-memory task status",
        "Evidence Records",
        "Architecture Contract Gate",
        *ARCHITECTURE_DESIGN_CORE_GUARD_TERMS,
        "Architecture Matrix",
        "architecture_context",
        *ARCHITECTURE_CAPABILITY_GUARD_TERMS,
        "Architecture Context Propagation",
        "matrix_facets",
        "Architecture Execution Control",
        "Architecture Compliance",
        "architecture drift",
        "Architecture Invariants",
        "Architecture Matrix Mismatches",
        "Contract Drift",
        "Architecture Approval Gate",
        "Local Best Practice auto gate",
        "regression demotion",
        "Model/reasoning upgrade is not the default fix",
        "two or more worker lanes",
    ],
    "agents/architect.md": [
        "Architecture Approval Gate",
        "Architecture Attempt",
        "Architecture Failure",
        "Model/reasoning upgrade is not the default fix",
        "Selected Architecture",
        *ARCHITECTURE_DESIGN_CORE_GUARD_TERMS,
        "Architecture Matrix",
        "architecture_context",
        *ARCHITECTURE_CAPABILITY_GUARD_TERMS,
        "Architecture Context Propagation",
        "Architecture Execution Control",
        "architecture drift",
        "architect re-check",
    ],
    "agents/reviewer.md": [
        "Evidence Records",
        "Architecture Contract Gate",
        "Architecture Design Mode",
        "Architecture Design Brief",
        "Architecture Execution Control",
        "architecture_context",
        *ARCHITECTURE_CAPABILITY_GUARD_TERMS,
        "Architecture Context Propagation",
        "Report architecture contract mismatches explicitly",
        "Architecture Matrix Mismatches",
        "Contract Drift",
    ],
    "agents/qa-verifier.md": [
        "Architecture Contract Gate",
        "Architecture Design Mode",
        "Architecture Design Brief",
        "Architecture Execution Control",
        "architecture invariants",
        "architecture_context",
        *ARCHITECTURE_CAPABILITY_GUARD_TERMS,
        "Architecture Context Propagation",
        "Architecture Invariants",
    ],
    "agents/backend-worker.md": [
        "Architecture Compliance",
        *ARCHITECTURE_DESIGN_ROLE_GUARD_TERMS,
        "Architecture Context Propagation",
        "matrix_facets",
        "architecture drift",
    ],
    "agents/frontend-worker.md": [
        "Architecture Compliance",
        *ARCHITECTURE_DESIGN_ROLE_GUARD_TERMS,
        "Architecture Context Propagation",
        "matrix_facets",
        "architecture drift",
    ],
    "agents/typescript-worker.md": [
        "Architecture Compliance",
        *ARCHITECTURE_DESIGN_ROLE_GUARD_TERMS,
        "Architecture Context Propagation",
        "matrix_facets",
        "architecture drift",
    ],
    "agents/bun-worker.md": [
        "Architecture Compliance",
        *ARCHITECTURE_DESIGN_ROLE_GUARD_TERMS,
        "Architecture Context Propagation",
        "matrix_facets",
        "architecture drift",
    ],
    "agents/python-worker.md": [
        "Architecture Compliance",
        *ARCHITECTURE_DESIGN_ROLE_GUARD_TERMS,
        "Architecture Context Propagation",
        "matrix_facets",
        "architecture drift",
    ],
    "agents/golang-worker.md": [
        "Architecture Compliance",
        *ARCHITECTURE_DESIGN_ROLE_GUARD_TERMS,
        "Architecture Context Propagation",
        "matrix_facets",
        "architecture drift",
    ],
    "agents/ios-worker.md": [
        "Architecture Compliance",
        *ARCHITECTURE_DESIGN_ROLE_GUARD_TERMS,
        "Architecture Context Propagation",
        "matrix_facets",
        "architecture drift",
    ],
    "agents/rag-retrieval-engineer.md": [
        "Architecture Compliance",
        *ARCHITECTURE_DESIGN_ROLE_GUARD_TERMS,
        "Architecture Context Propagation",
        "matrix_facets",
        "architecture drift",
    ],
    "scripts/validate-run.py": [
        "architecture_compliance",
        "architecture_design_brief",
        "Architecture Design Brief",
        "architecture_capabilities",
        "Architecture Capability Router",
        "Selected Matrix Facets",
        "Status: approved",
        "matrix_facets",
        "architecture_context",
        "ARCHITECTURE_MATRIX_PATH",
        "MATRIX_FACET_PATTERN",
        "product_context",
        "application_surface",
        "architecture_pattern",
        "stack_runtime",
        "risk_gates",
        "verification_gates",
        "Architecture Compliance",
        "Architecture Invariants",
        "Architecture Matrix Mismatches",
        "Contract Drift",
    ],
    "scripts/architecture_capabilities.py": [
        "Architecture Capability Router",
        "architecture capability registry",
        "recommended_skills",
        "matrix_facets",
        "contract_sections",
    ],
    "scripts/validate-architecture-capabilities.py": [
        "Architecture Capability Router",
        "architecture capability registry",
        "allow-partial-matrix-coverage",
    ],
}


def run_step(name: str, command: list[str]) -> int:
    print(f"==> {name}")
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode:
        print(f"FAIL {name}: exit {result.returncode}", file=sys.stderr)
        return result.returncode
    print(f"PASS {name}")
    return 0


def run_content_guard(name: str, needle: str) -> int:
    print(f"==> {name}")
    matches: list[str] = []
    for raw_path in PRODUCT_SEARCH_PATHS:
        path = ROOT / raw_path
        if path.is_dir():
            candidates = [candidate for candidate in path.rglob("*") if candidate.is_file()]
        elif path.exists():
            candidates = [path]
        else:
            candidates = []
        for candidate in candidates:
            if candidate == Path(__file__).resolve():
                continue
            if candidate.suffix not in {".md", ".py", ".json", ".yaml", ".yml"}:
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if needle in text:
                matches.append(str(candidate.relative_to(ROOT)))
    if matches:
        print(f"FAIL {name}: found forbidden text '{needle}'", file=sys.stderr)
        for match in matches:
            print(f"- {match}", file=sys.stderr)
        return 1
    print(f"PASS {name}")
    return 0


def run_readme_markdown_guard() -> int:
    print("==> README Markdown-only guard")
    forbidden = [
        "<p",
        "<picture",
        "<source",
        "<img",
        "<div",
        "<h1",
        "<h2",
        "<h3",
        "</",
        ".svg",
        "img.shields.io",
    ]
    failures: list[str] = []
    for name in ["README.md", "README.ru.md"]:
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in text:
                failures.append(f"{name}: {needle}")
    if failures:
        print("FAIL README Markdown-only guard", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("PASS README Markdown-only guard")
    return 0


def run_required_runtime_text_guard() -> int:
    print("==> task status completion guard")
    failures: list[str] = []
    for raw_path, needles in REQUIRED_RUNTIME_TEXT.items():
        path = ROOT / raw_path
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle not in text:
                failures.append(f"{raw_path}: missing {needle!r}")
    if failures:
        print("FAIL task status completion guard", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("PASS task status completion guard")
    return 0


def main() -> int:
    python_files = sorted(str(path.relative_to(ROOT)) for path in SCRIPTS.glob("*.py"))
    command_steps = [
        ("py_compile scripts", [sys.executable, "-m", "py_compile", *python_files]),
        ("agent config fixtures", [sys.executable, "scripts/test-agent-config.py"]),
        ("validate-agent-config fixtures", [sys.executable, "scripts/test-validate-agent-config.py"]),
        ("role catalog fixtures", [sys.executable, "scripts/test-validate-role-catalog.py"]),
        ("updater fixtures", [sys.executable, "scripts/test-update-agent-flow-skill.py"]),
        ("agent config validation", [sys.executable, "scripts/validate-agent-config.py"]),
        ("role catalog validation", [sys.executable, "scripts/validate-role-catalog.py"]),
        ("agent skill registry validation", [sys.executable, "scripts/validate-agent-skill-registry.py"]),
        ("architecture capability registry fixtures", [sys.executable, "scripts/test-validate-architecture-capabilities.py"]),
        ("architecture capability registry validation", [sys.executable, "scripts/validate-architecture-capabilities.py"]),
        ("validate-run CLI", [sys.executable, "scripts/validate-run.py", "--help"]),
        ("evidence record analyzer fixtures", [sys.executable, "scripts/test-analyze-evidence-records.py"]),
        ("lane fixture tests", [sys.executable, "scripts/test-validate-run-lanes.py"]),
        ("git diff hygiene", ["git", "diff", "--check"]),
    ]
    content_steps = [
        ("personal path guard", "/Users/" + "ucnlejumper"),
        ("old README/docs check block guard", "python3 -m py_compile " + "scripts/*.py"),
        ("old architecture profile term guard", "Architecture " + "Profiles"),
        ("old architecture profile file guard", "architecture-" + "profiles"),
        ("project crm profile guard", "profile" + "-" + "crm"),
        ("project agentflow profile guard", "profile" + "-" + "agentflow"),
        ("project child profile guard", "profile" + "-" + "child"),
        ("project local browser profile guard", "profile" + "-" + "local-browser"),
        ("old admin backoffice surface guard", "admin-" + "backoffice-ui"),
        ("old landing waitlist surface guard", "landing-" + "waitlist"),
        ("old browser extension dashboard surface guard", "browser-extension-" + "local-dashboard"),
        ("old ios app surface guard", "`ios-" + "app`"),
        ("old macos utility surface guard", "`macos-" + "utility`"),
    ]

    failures = 0
    for name, command in command_steps:
        if run_step(name, command):
            failures += 1
    for name, needle in content_steps:
        if run_content_guard(name, needle):
            failures += 1
    if run_readme_markdown_guard():
        failures += 1
    if run_required_runtime_text_guard():
        failures += 1

    if failures:
        print(f"FAILED {failures} check(s)", file=sys.stderr)
        return 1
    print("PASS all Agent Flow checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
