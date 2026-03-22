"""Advisory evaluation of program requirement trees and GE listings against taken courses.

This does not replace degree checks: grades, transfer credit, AP, and full
cross-counting rules from the catalogue are out of scope.

Course allocation: each completed course applies to at most one non-GE program
requirement block (catalogue order), except free-elective blocks are filled last
from any remaining courses. GE categories are filled only from courses not used
by those blocks. A course may count toward at most two GE letter areas when it
appears on both lists and ``overlap_policy`` allows that pair; otherwise at most
one GE area. Composition/writing blocks consume from the same pool, so a course
used there cannot also count toward GE.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from app.models import (
    AllOfNode,
    AnyOfNode,
    CourseNode,
    GeneralEducationCatalog,
    GeOverlapPolicy,
    Program,
    RequirementBlock,
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
    available: set[str],
    taken_set: frozenset[str],
) -> NodeEval:
    items = node.pool.items
    min_c = node.min_count
    min_u = node.min_units

    if not items and (min_c is None or min_c == 0) and (min_u is None or min_u <= 0):
        return NodeEval(status="satisfied", detail=None)

    item_results: list[NodeEval] = []
    for ch in items:
        trial = set(available)
        item_results.append(_evaluate_node_with_pool(ch, trial, taken_set))
    if any(r.status == "manual" for r in item_results):
        return NodeEval(
            status="manual",
            detail="This elective pool includes a rule that must be checked manually",
        )

    if min_c is not None:
        item_results = [_evaluate_node_with_pool(ch, available, taken_set) for ch in items]
    satisfied_items = sum(1 for r in item_results if r.status == "satisfied")
    has_partial_item = any(r.status == "partial" for r in item_results)

    units_sum = 0.0
    if min_u is not None and min_u > 0:
        for item in items:
            for course in _collect_courses(item):
                cid = normalize_course_id(course.course_id)
                if cid not in available or course.units is None:
                    continue
                units_sum += float(course.units)
                available.discard(cid)
                if units_sum >= min_u:
                    break
            if units_sum >= min_u:
                break

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


def _evaluate_node_with_pool(
    node: RequirementNode,
    available: set[str],
    taken_set: frozenset[str],
) -> NodeEval:
    """Evaluate a subtree; satisfied leaf courses are removed from *available* (shared pool)."""
    if isinstance(node, TextNode):
        return NodeEval(status="neutral", detail=node.text[:200] if node.text else None)

    if isinstance(node, CourseNode):
        cid = normalize_course_id(node.course_id)
        if cid in available:
            available.discard(cid)
            return NodeEval(status="satisfied", detail=cid)
        if cid in taken_set:
            return NodeEval(
                status="unsatisfied",
                detail=f"{cid} is already allocated to another requirement",
            )
        return NodeEval(status="unsatisfied", detail=f"Need {node.course_id}")

    if isinstance(node, AllOfNode):
        child_results = [_evaluate_node_with_pool(c, available, taken_set) for c in node.children]
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
        snap = set(available)
        for opt in node.options:
            trial = set(available)
            ev = _evaluate_node_with_pool(opt, trial, taken_set)
            if ev.status == "satisfied":
                available.clear()
                available.update(trial)
                return ev
        available.clear()
        available.update(snap)
        results = [_evaluate_node_with_pool(o, set(snap), taken_set) for o in node.options]
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
        return _evaluate_select_explicit(node, available, taken_set)

    return NodeEval(status="unsatisfied", detail="Unknown node type")


def _evaluate_node(node: RequirementNode, taken_set: frozenset[str]) -> NodeEval:
    """Evaluate without cross-block allocation (full *taken_set* pool)."""
    pool = set(taken_set)
    return _evaluate_node_with_pool(node, pool, taken_set)


BlockStatus = Literal["satisfied", "partial", "unsatisfied", "manual"]


class BlockEvalSummary(BaseModel):
    id: str
    title: str
    parent_id: str | None = None
    parent_title: str | None = None
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


_DEFAULT_FREE_ELECTIVE_UNITS = 4.0
_FREE_ELECTIVES_BLOCK_ID = "free_electives"


def _units_by_course_in_program(program: Program) -> dict[str, float]:
    """Map normalized course id → units from any CourseNode in the program (for free-elective estimates)."""
    out: dict[str, float] = {}
    for block in program.blocks:
        for cn in _collect_courses(block.root):
            if cn.units is not None:
                out[normalize_course_id(cn.course_id)] = float(cn.units)
    return out


def _evaluate_free_elective_block(
    block: RequirementBlock,
    available: set[str],
    taken_set: frozenset[str],
    unit_by_course: dict[str, float],
) -> NodeEval:
    """Consume remaining *available* courses toward min/max units (open USC electives)."""
    mu = block.min_units
    mx = block.max_units
    if isinstance(block.root, SelectNode):
        root = block.root
        if mu is None and root.min_units is not None:
            mu = int(root.min_units)
        if mx is None and root.max_units is not None:
            mx = int(root.max_units)

    need = float(mu) if mu is not None else 0.0
    cap = float(mx) if mx is not None else None

    if need <= 0 and (cap is None or cap <= 0):
        return NodeEval(
            status="satisfied",
            detail="No unit requirement parsed for free electives; confirm in catalogue.",
        )

    target = need if need > 0 else (cap or 0.0)
    used = 0.0
    picked: list[str] = []

    for cid in sorted(available):
        if need > 0 and used >= need:
            break
        if cap is not None and used >= cap:
            break
        u = unit_by_course.get(cid, _DEFAULT_FREE_ELECTIVE_UNITS)
        if cap is not None and used + u > cap:
            continue
        available.discard(cid)
        picked.append(cid)
        used += u
        if need > 0 and used >= need:
            break

    assumption = (
        f"Courses not listed with units elsewhere in this program are counted as "
        f"{_DEFAULT_FREE_ELECTIVE_UNITS:g} units each (advisory)."
    )
    parts = [f"{used:g}/{target:g} elective units from unused courses", assumption]
    if picked:
        parts.insert(1, "Applied: " + ", ".join(picked))

    detail = "; ".join(parts)
    if need > 0:
        if used >= need:
            return NodeEval(status="satisfied", detail=detail)
        if used > 0:
            return NodeEval(status="partial", detail=detail)
        return NodeEval(status="unsatisfied", detail=detail)

    if cap is not None:
        if used >= cap:
            return NodeEval(status="satisfied", detail=detail)
        if used > 0:
            return NodeEval(status="partial", detail=detail)
    return NodeEval(status="satisfied", detail=detail)


def _is_ge_placeholder_block(block: RequirementBlock) -> bool:
    if block.kind == "ge":
        return True
    t = (block.title or "").strip().lower()
    return t.startswith("general education")


def _category_course_sets(catalog: GeneralEducationCatalog) -> dict[str, set[str]]:
    return {cat.code: {normalize_course_id(c.course_id) for c in cat.courses} for cat in catalog.categories}


def _ge_pair_allows_double_count(code_a: str, code_b: str, policy: GeOverlapPolicy) -> bool:
    if code_a == code_b:
        return False
    for rule in policy.allowed_cross_count_rules:
        if {rule.source_category, rule.target_category} == {code_a, code_b}:
            return rule.max_shared_courses >= 1
    if policy.no_other_double_counting:
        return False
    return True


def _can_assign_course_to_ge_category(
    cid: str,
    cat_code: str,
    lists: dict[str, set[str]],
    assigned: dict[str, list[str]],
    policy: GeOverlapPolicy,
) -> bool:
    if cid not in lists.get(cat_code, set()):
        return False
    have = assigned.get(cid, [])
    if cat_code in have:
        return False
    if len(have) == 0:
        return True
    if len(have) >= 2:
        return False
    return _ge_pair_allows_double_count(have[0], cat_code, policy)


def evaluate_general_education(
    catalog: GeneralEducationCatalog,
    taken: list[str],
    *,
    program_consumed: frozenset[str] | None = None,
) -> list[GeCategoryEvalSummary]:
    """Assign GE credit from *taken* with cross-category limits and program allocation."""
    taken_set = build_taken_set(taken)
    consumed = frozenset(program_consumed) if program_consumed is not None else frozenset()
    eligible = taken_set - consumed
    lists = _category_course_sets(catalog)
    policy = catalog.overlap_policy

    assigned: dict[str, list[str]] = defaultdict(list)
    per_cat: dict[str, list[str]] = {c.code: [] for c in catalog.categories}

    sorted_cats = sorted(catalog.categories, key=lambda c: c.code)
    for cat in sorted_cats:
        rc = cat.required_count
        pool_candidates = sorted(cid for cid in eligible if cid in lists.get(cat.code, set()))
        for cid in pool_candidates:
            if len(per_cat[cat.code]) >= rc:
                break
            if _can_assign_course_to_ge_category(cid, cat.code, lists, assigned, policy):
                assigned[cid].append(cat.code)
                per_cat[cat.code].append(cid)

    rows: list[GeCategoryEvalSummary] = []
    for cat in sorted_cats:
        matched = list(per_cat[cat.code])
        any_specific_only_match = False
        for cid in matched:
            flags = [
                c.specific_students_only
                for c in cat.courses
                if normalize_course_id(c.course_id) == cid
            ]
            if flags and all(flags):
                any_specific_only_match = True

        n = len(matched)
        rc = cat.required_count
        if n >= rc:
            st: BlockStatus = "satisfied"
        elif n > 0:
            st = "partial"
        else:
            st = "unsatisfied"

        parts = [f"{n}/{rc} from this category's published list (after program allocation)"]
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
    available = set(taken_set)
    unit_hints = _units_by_course_in_program(program)
    summaries: dict[str, BlockEvalSummary] = {}

    for block in program.blocks:
        if block.id == _FREE_ELECTIVES_BLOCK_ID:
            continue
        if _is_ge_placeholder_block(block):
            trial = set(available)
            ev = _evaluate_node_with_pool(block.root, trial, taken_set)
        else:
            ev = _evaluate_node_with_pool(block.root, available, taken_set)
        summaries[block.id] = BlockEvalSummary(
            id=block.id,
            title=block.title,
            parent_id=block.parent_id,
            parent_title=block.parent_title,
            kind=block.kind,
            status=_block_status(ev),
            detail=ev.detail,
        )

    for block in program.blocks:
        if block.id != _FREE_ELECTIVES_BLOCK_ID:
            continue
        ev = _evaluate_free_elective_block(block, available, taken_set, unit_hints)
        summaries[block.id] = BlockEvalSummary(
            id=block.id,
            title=block.title,
            parent_id=block.parent_id,
            parent_title=block.parent_title,
            kind=block.kind,
            status=_block_status(ev),
            detail=ev.detail,
        )

    blocks = [summaries[b.id] for b in program.blocks]
    program_consumed = frozenset(taken_set - available)
    ge_rows: list[GeCategoryEvalSummary] = []
    ge_year = ""
    ge_warnings: list[str] = []
    if ge_catalog is not None:
        ge_rows = evaluate_general_education(
            ge_catalog, taken, program_consumed=program_consumed
        )
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
