"""Advisory evaluation of program requirement trees and GE listings against taken courses.

This does not replace degree checks: grades, transfer credit, AP, and full
cross-counting rules from the catalogue are out of scope. GE evaluation counts
eligible courses per letter category against the published lists; overlap_policy
between categories is not enforced here.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.models import (
    AllOfNode,
    AnyOfNode,
    CourseNode,
    GeneralEducationCatalog,
    Program,
    RequirementNode,
    SelectNode,
    TextNode,
)

NodeStatus = Literal["satisfied", "partial", "unsatisfied", "manual", "neutral"]


def normalize_course_id(raw: str) -> str:
    """Normalize user or catalogue course ids for comparison (e.g. CSCI 103L, csci103l)."""
    s = raw.strip().upper()
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    parts = s.split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1]}"
    compact = s.replace(" ", "")
    m = re.match(r"^([A-Z]{2,5})(\d[\w]*)$", compact)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return s


def build_taken_set(taken: list[str]) -> frozenset[str]:
    return frozenset(normalize_course_id(x) for x in taken if normalize_course_id(x))


class NodeEval(BaseModel):
    """Internal result for one requirement node."""

    status: NodeStatus
    detail: str | None = None


def _collect_courses(node: RequirementNode) -> list[CourseNode]:
    out: list[CourseNode] = []
    if isinstance(node, CourseNode):
        out.append(node)
    elif isinstance(node, AllOfNode):
        for c in node.children:
            out.extend(_collect_courses(c))
    elif isinstance(node, AnyOfNode):
        for o in node.options:
            out.extend(_collect_courses(o))
    elif isinstance(node, SelectNode):
        for item in node.pool.items:
            out.extend(_collect_courses(item))
    return out


def _evaluate_select_explicit(
    node: SelectNode,
    taken_set: frozenset[str],
) -> NodeEval:
    items = node.pool.items
    min_c = node.min_count
    min_u = node.min_units

    if not items and (min_c is None or min_c == 0) and (min_u is None or min_u <= 0):
        return NodeEval(status="satisfied", detail=None)

    item_results = [_evaluate_node(ch, taken_set) for ch in items]
    if any(r.status == "manual" for r in item_results):
        return NodeEval(
            status="manual",
            detail="This elective pool includes a rule that must be checked manually",
        )

    units_sum = 0.0
    for item in items:
        for course in _collect_courses(item):
            cid = normalize_course_id(course.course_id)
            if cid in taken_set and course.units is not None:
                units_sum += float(course.units)

    satisfied_items = sum(1 for r in item_results if r.status == "satisfied")
    has_partial_item = any(r.status == "partial" for r in item_results)

    detail_parts: list[str] = []
    checks: list[bool] = []
    if min_c is not None:
        checks.append(satisfied_items >= min_c)
        detail_parts.append(f"{satisfied_items}/{min_c} choices satisfied")
    if min_u is not None and min_u > 0:
        checks.append(units_sum >= min_u)
        detail_parts.append(f"{units_sum:g}/{min_u:g} units from pool")

    if min_c is None and min_u is None:
        if not items:
            return NodeEval(status="satisfied")
        if satisfied_items > 0:
            return NodeEval(status="satisfied")
        if has_partial_item:
            return NodeEval(status="partial")
        return NodeEval(status="unsatisfied")

    detail = "; ".join(detail_parts) if detail_parts else None
    if all(checks):
        return NodeEval(status="satisfied", detail=detail)

    progress = (
        satisfied_items > 0
        or has_partial_item
        or (min_u is not None and min_u > 0 and units_sum > 0)
    )
    if progress:
        return NodeEval(status="partial", detail=detail)
    return NodeEval(status="unsatisfied", detail=detail)


def _evaluate_node(node: RequirementNode, taken_set: frozenset[str]) -> NodeEval:
    if isinstance(node, TextNode):
        return NodeEval(status="neutral", detail=node.text[:200] if node.text else None)

    if isinstance(node, CourseNode):
        cid = normalize_course_id(node.course_id)
        if cid in taken_set:
            return NodeEval(status="satisfied", detail=cid)
        return NodeEval(status="unsatisfied", detail=f"Need {node.course_id}")

    if isinstance(node, AllOfNode):
        child_results = [_evaluate_node(c, taken_set) for c in node.children]
        relevant = [r for r in child_results if r.status != "neutral"]
        if not relevant:
            return NodeEval(status="satisfied")
        if any(r.status == "manual" for r in relevant):
            return NodeEval(
                status="manual",
                detail="This section includes requirements that must be checked manually",
            )
        sat = sum(1 for r in relevant if r.status == "satisfied")
        unsat = sum(1 for r in relevant if r.status == "unsatisfied")
        part = sum(1 for r in relevant if r.status == "partial")
        if unsat == 0 and part == 0:
            return NodeEval(status="satisfied")
        if sat == 0 and part == 0:
            return NodeEval(status="unsatisfied")
        return NodeEval(status="partial")

    if isinstance(node, AnyOfNode):
        if not node.options:
            return NodeEval(status="satisfied")
        results = [_evaluate_node(o, taken_set) for o in node.options]
        if any(r.status == "satisfied" for r in results):
            return NodeEval(status="satisfied")
        if any(r.status == "partial" for r in results):
            return NodeEval(status="partial")
        if any(r.status == "manual" for r in results):
            return NodeEval(status="manual", detail="One of several options; verify manually")
        return NodeEval(status="unsatisfied")

    if isinstance(node, SelectNode):
        kind = node.pool.kind
        if kind in ("subject", "any_course"):
            subj = node.pool.subject or ""
            mu = node.min_units
            label = node.label or "Elective / pool"
            bits = [label]
            if mu is not None:
                bits.append(f"{mu:g} units")
            if kind == "subject" and subj:
                bits.append(f"{subj} courses")
            elif kind == "any_course":
                bits.append("any courses")
            return NodeEval(
                status="manual",
                detail="Cannot auto-check: " + ", ".join(bits),
            )
        return _evaluate_select_explicit(node, taken_set)

    return NodeEval(status="unsatisfied", detail="Unknown node type")


BlockStatus = Literal["satisfied", "partial", "unsatisfied", "manual"]


class BlockEvalSummary(BaseModel):
    id: str
    title: str
    kind: Literal["core", "elective", "ge", "pre_major", "supporting", "other"] = "other"
    status: BlockStatus
    detail: str | None = None


class GeCategoryEvalSummary(BaseModel):
    """Per GE letter category: how many listed courses the student has taken."""

    code: str
    label: str
    status: BlockStatus
    required_count: int
    matched_count: int
    matched_courses: list[str] = Field(default_factory=list)
    detail: str | None = None


class ProgramEvaluationResult(BaseModel):
    title: str
    catalog_year: str
    program_warnings: list[str] = Field(default_factory=list)
    blocks: list[BlockEvalSummary] = Field(default_factory=list)
    general_education: list[GeCategoryEvalSummary] = Field(default_factory=list)
    ge_catalog_year: str = ""
    ge_warnings: list[str] = Field(default_factory=list)
    ge_error: str | None = None


class EvaluateBody(BaseModel):
    taken: list[str] = Field(default_factory=list)


def _block_status(ev: NodeEval) -> BlockStatus:
    if ev.status == "neutral":
        return "satisfied"
    if ev.status in ("satisfied", "partial", "unsatisfied", "manual"):
        return ev.status
    return "unsatisfied"


def evaluate_general_education(
    catalog: GeneralEducationCatalog,
    taken: list[str],
) -> list[GeCategoryEvalSummary]:
    """Count taken courses that appear on each GE category's course list."""
    taken_set = build_taken_set(taken)
    rows: list[GeCategoryEvalSummary] = []
    for cat in catalog.categories:
        matched: list[str] = []
        any_specific_only_match = False
        for t in sorted(taken_set):
            flags = [
                c.specific_students_only
                for c in cat.courses
                if normalize_course_id(c.course_id) == t
            ]
            if not flags:
                continue
            matched.append(t)
            if all(flags):
                any_specific_only_match = True

        n = len(matched)
        rc = cat.required_count
        if n >= rc:
            st: BlockStatus = "satisfied"
        elif n > 0:
            st = "partial"
        else:
            st = "unsatisfied"

        parts = [f"{n}/{rc} from this category's published list"]
        if matched:
            parts.append("Matched: " + ", ".join(matched))
        if any_specific_only_match:
            parts.append(
                "Includes a course marked for specific students only; confirm program eligibility."
            )
        rows.append(
            GeCategoryEvalSummary(
                code=cat.code,
                label=cat.label,
                status=st,
                required_count=rc,
                matched_count=n,
                matched_courses=list(matched),
                detail="; ".join(parts),
            )
        )
    return rows


def evaluate_program(
    program: Program,
    taken: list[str],
    *,
    ge_catalog: GeneralEducationCatalog | None = None,
    ge_error: str | None = None,
) -> ProgramEvaluationResult:
    taken_set = build_taken_set(taken)
    blocks: list[BlockEvalSummary] = []
    for block in program.blocks:
        ev = _evaluate_node(block.root, taken_set)
        blocks.append(
            BlockEvalSummary(
                id=block.id,
                title=block.title,
                kind=block.kind,
                status=_block_status(ev),
                detail=ev.detail,
            )
        )
    ge_rows: list[GeCategoryEvalSummary] = []
    ge_year = ""
    ge_warnings: list[str] = []
    if ge_catalog is not None:
        ge_rows = evaluate_general_education(ge_catalog, taken)
        ge_year = ge_catalog.catalog_year
        ge_warnings = list(ge_catalog.warnings)
    return ProgramEvaluationResult(
        title=program.title,
        catalog_year=program.catalog_year,
        program_warnings=list(program.warnings),
        blocks=blocks,
        general_education=ge_rows,
        ge_catalog_year=ge_year,
        ge_warnings=ge_warnings,
        ge_error=ge_error,
    )
