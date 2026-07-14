"""Architecture Capability Router registry helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE_CAPABILITY_REGISTRY_PATH = ROOT / "registries" / "architecture-capabilities.json"
ARCHITECTURE_MATRIX_PATH = ROOT / "references" / "architecture-matrix.md"
AGENT_SKILLS_REGISTRY_PATH = ROOT / "registries" / "agent-skills.json"
CAPABILITY_REGISTRY_VERSION = 1
ARCHITECTURE_CONTEXT_AXES = {
    "product_context": "Product Context",
    "application_surface": "Application Surface",
    "architecture_pattern": "Architecture Pattern",
    "stack_runtime": "Stack Runtime",
    "risk_gates": "Risk Gates",
    "verification_gates": "Verification Gates",
}
ARCHITECTURE_CONTRACT_SECTIONS = {
    "Selected Architecture",
    "Rejected Alternatives",
    "Module Boundaries",
    "Data And State Flow",
    "Public Contracts",
    "Worker Ownership",
    "Forbidden Changes",
    "QA Gates",
    "Reviewer Checklist",
    "Stop Conditions",
}
KEBAB_CASE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
MARKDOWN_HEADING_PATTERN = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
MATRIX_FACET_PATTERN = re.compile(r"^\s*-\s+`([^`]+)`\s*:")


def load_json_object(path: Path, display_name: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "null")
    except FileNotFoundError:
        return None, [f"{display_name} not found: {path}"]
    except json.JSONDecodeError as exc:
        return None, [f"{display_name} invalid JSON: {exc}"]
    if not isinstance(data, dict):
        return None, [f"{display_name} must be a JSON object"]
    return data, []


def load_matrix_facets(path: Path = ARCHITECTURE_MATRIX_PATH) -> tuple[dict[str, set[str]], list[str]]:
    facets = {axis: set() for axis in ARCHITECTURE_CONTEXT_AXES}
    if not path.exists():
        return facets, [f"Architecture Matrix not found: {path}"]

    heading_to_axis = {heading: axis for axis, heading in ARCHITECTURE_CONTEXT_AXES.items()}
    current_axis: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        heading_match = MARKDOWN_HEADING_PATTERN.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            heading = heading_match.group(2).strip()
            current_axis = heading_to_axis.get(heading) if level == 3 else None
            continue
        if current_axis is None:
            continue
        facet_match = MATRIX_FACET_PATTERN.match(line)
        if facet_match:
            facets[current_axis].add(facet_match.group(1))

    errors = []
    for axis, heading in ARCHITECTURE_CONTEXT_AXES.items():
        if not facets[axis]:
            errors.append(f"Architecture Matrix section '{heading}' has no facet ids")
    return facets, errors


def load_agent_skill_ids(path: Path = AGENT_SKILLS_REGISTRY_PATH) -> tuple[set[str], list[str]]:
    registry, errors = load_json_object(path, "agent skill registry")
    if errors or registry is None:
        return set(), errors
    skills = registry.get("skills")
    if not isinstance(skills, list):
        return set(), ["agent skill registry field 'skills' must be an array"]
    skill_ids: set[str] = set()
    for index, row in enumerate(skills):
        if not isinstance(row, dict):
            return set(), [f"agent skill registry skills[{index}] must be an object"]
        name = row.get("name")
        if isinstance(name, str) and name:
            skill_ids.add(name)
    return skill_ids, []


def validate_string_array(
    value: Any,
    *,
    field_name: str,
    allow_empty: bool,
) -> tuple[list[str], list[str]]:
    if not isinstance(value, list) or (not value and not allow_empty):
        return [], [f"{field_name} must be a {'possibly empty' if allow_empty else 'non-empty'} array"]
    values: list[str] = []
    errors: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{field_name}[{index}] must be a non-empty string")
            continue
        values.append(item)
    return values, errors


def validate_architecture_capability_registry(
    registry_path: Path = ARCHITECTURE_CAPABILITY_REGISTRY_PATH,
    *,
    matrix_path: Path = ARCHITECTURE_MATRIX_PATH,
    agent_skills_path: Path = AGENT_SKILLS_REGISTRY_PATH,
    validate_skills: bool = True,
    require_full_matrix_coverage: bool = True,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    registry, errors = load_json_object(registry_path, "architecture capability registry")
    if registry is None:
        return {}, errors

    if registry.get("version") != CAPABILITY_REGISTRY_VERSION:
        errors.append(f"architecture capability registry version must be {CAPABILITY_REGISTRY_VERSION}")

    matrix_by_axis, matrix_errors = load_matrix_facets(matrix_path)
    errors.extend(matrix_errors)
    known_matrix_facets = {facet for facets in matrix_by_axis.values() for facet in facets}

    known_skill_ids: set[str] = set()
    if validate_skills:
        known_skill_ids, skill_errors = load_agent_skill_ids(agent_skills_path)
        errors.extend(skill_errors)

    rows = registry.get("capabilities")
    if not isinstance(rows, list):
        return {}, errors + ["architecture capability registry field 'capabilities' must be an array"]

    capabilities: dict[str, dict[str, Any]] = {}
    covered_matrix_facets: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"capabilities[{index}] must be an object")
            continue

        capability_id = row.get("id")
        label = capability_id if isinstance(capability_id, str) and capability_id else f"capabilities[{index}]"
        if not isinstance(capability_id, str) or not capability_id:
            errors.append(f"capabilities[{index}].id must be a non-empty string")
        elif not KEBAB_CASE_PATTERN.fullmatch(capability_id):
            errors.append(f"invalid architecture capability id: {capability_id}")
        elif capability_id in capabilities:
            errors.append(f"duplicate architecture capability id: {capability_id}")
        else:
            capabilities[capability_id] = row

        matrix_facets, facet_errors = validate_string_array(
            row.get("matrix_facets"),
            field_name=f"{label}.matrix_facets",
            allow_empty=False,
        )
        errors.extend(facet_errors)
        for facet in matrix_facets:
            if facet not in known_matrix_facets:
                errors.append(f"{label}: unknown Architecture Matrix facet: {facet}")
            else:
                covered_matrix_facets.add(facet)

        recommended_skills, skill_shape_errors = validate_string_array(
            row.get("recommended_skills"),
            field_name=f"{label}.recommended_skills",
            allow_empty=True,
        )
        errors.extend(skill_shape_errors)
        if validate_skills:
            for skill in recommended_skills:
                if skill not in known_skill_ids:
                    errors.append(f"{label}: unknown recommended skill: {skill}")

        contract_sections, section_errors = validate_string_array(
            row.get("contract_sections"),
            field_name=f"{label}.contract_sections",
            allow_empty=False,
        )
        errors.extend(section_errors)
        for section in contract_sections:
            if section not in ARCHITECTURE_CONTRACT_SECTIONS:
                errors.append(f"{label}: unknown architecture contract section: {section}")

    if require_full_matrix_coverage and known_matrix_facets:
        for facet in sorted(known_matrix_facets - covered_matrix_facets):
            errors.append(f"architecture capability registry missing Matrix facet coverage: {facet}")

    return capabilities, errors


def selected_capability_matrix_facets(
    capabilities_by_id: dict[str, dict[str, Any]],
    selected_capabilities: list[str],
) -> set[str]:
    covered: set[str] = set()
    for capability_id in selected_capabilities:
        row = capabilities_by_id.get(capability_id)
        if not isinstance(row, dict):
            continue
        matrix_facets = row.get("matrix_facets")
        if isinstance(matrix_facets, list):
            covered.update(facet for facet in matrix_facets if isinstance(facet, str))
    return covered


def validate_architecture_capabilities_shape(
    data: dict[str, Any],
    capabilities_by_id: dict[str, dict[str, Any]],
    selected_matrix_facets: list[str],
    *,
    required: bool,
) -> tuple[list[str], list[str]]:
    raw_capabilities = data.get("architecture_capabilities")
    if raw_capabilities is None:
        if required:
            return [], ["lane-map.json field 'architecture_capabilities' is required"]
        return [], []
    if not isinstance(raw_capabilities, dict):
        return [], ["lane-map.json field 'architecture_capabilities' must be an object"]

    errors: list[str] = []
    selected, selected_errors = validate_string_array(
        raw_capabilities.get("selected"),
        field_name="lane-map.json architecture_capabilities.selected",
        allow_empty=False,
    )
    errors.extend(selected_errors)
    for capability_id in selected:
        if not KEBAB_CASE_PATTERN.fullmatch(capability_id):
            errors.append(f"invalid architecture capability id: {capability_id}")
        elif capability_id not in capabilities_by_id:
            errors.append(f"unknown architecture capability: {capability_id}")

    notes = raw_capabilities.get("notes")
    if not isinstance(notes, str) or not notes.strip():
        errors.append("lane-map.json architecture_capabilities.notes must be a non-empty string")

    if selected:
        covered_matrix_facets = selected_capability_matrix_facets(capabilities_by_id, selected)
        for facet in selected_matrix_facets:
            if facet not in covered_matrix_facets:
                errors.append(
                    "lane-map.json architecture_capabilities do not cover "
                    f"Architecture Matrix facet: {facet}"
                )

    return selected, errors
