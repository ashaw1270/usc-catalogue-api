"""Pydantic models for program requirements schema."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class ProgramId(BaseModel):
    """Program identity (catoid, poid, optional slug)."""

    catoid: int
    poid: int
    slug: str | None = None


# --- Requirement items (union types) ---


class CourseRequirement(BaseModel):
    """A specific course requirement."""

    type: Literal["course"] = "course"
    course_id: str  # e.g. CSCI 102L
    title: str | None = None
    units: float | None = None
    required: bool = True
    notes: list[str] = Field(default_factory=list)


class CourseGroupRequirement(BaseModel):
    """A choice among courses (e.g. one of several sequences)."""

    type: Literal["course_group"] = "course_group"
    group_label: str
    # For simple groups expressed as a flat set of alternatives
    min_courses: int | None = None
    max_courses: int | None = None
    min_units: int | None = None
    max_units: int | None = None
    courses: list[CourseRequirement] = Field(default_factory=list)
    # For structured choices like (A and B) or (C and D), where each
    # inner list represents a sequence of courses taken together.
    # Exactly one sequence is usually selected when an \"or\" appears.
    options: list[list[CourseRequirement]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class FreeElectiveRequirement(BaseModel):
    """Free electives with unit bounds."""

    type: Literal["free_elective"] = "free_elective"
    min_units: int
    max_units: int | None = None
    notes: list[str] = Field(default_factory=list)


class TextNote(BaseModel):
    """Unstructured textual requirement or annotation."""

    type: Literal["text_note"] = "text_note"
    text: str


# Discriminated union for requirement items (use type field for JSON)
RequirementItem = Annotated[
    CourseRequirement | CourseGroupRequirement | FreeElectiveRequirement | TextNote,
    Field(discriminator="type"),
]

# ----------------------------
# v2 schema (nested requirement nodes)
# ----------------------------


class RequirementConfig(BaseModel):
    """Configurable semantic assumptions for parsing and downstream evaluation."""

    upper_division_course_number_min: int = 300
    subject_token_pattern: str = r"^[A-Z]{3,5}$"


class AtomicConstraint(BaseModel):
    """Machine-readable constraint atom; always retain raw text elsewhere."""

    kind: Literal[
        "subject_in",
        "subject_not_in",
        "min_from_subject",
        "max_from_subject",
        "course_number_min",
        "course_number_max",
        "min_upper_division_count",
        "each_course_units_equals",
        "min_from_pool",
        "max_from_pool",
    ]
    value: int | float | str | list[str] | None = None
    subject: str | None = None
    pool_name: str | None = None


class Constraint(BaseModel):
    raw_text: str
    parsed: list[AtomicConstraint] = Field(default_factory=list)


class Pool(BaseModel):
    """A selectable pool of items."""

    kind: Literal["explicit", "subject", "any_course"] = "explicit"
    name: str | None = None
    subject: str | None = None
    items: list["RequirementNode"] = Field(default_factory=list)


class CourseNode(BaseModel):
    type: Literal["course"] = "course"
    course_id: str
    title: str | None = None
    units: float | None = None
    bucket_id: str | None = None
    notes: list[str] = Field(default_factory=list)


class TextNode(BaseModel):
    type: Literal["text"] = "text"
    text: str


class AllOfNode(BaseModel):
    type: Literal["all_of"] = "all_of"
    children: list["RequirementNode"] = Field(default_factory=list)
    label: str | None = None


class AnyOfNode(BaseModel):
    type: Literal["any_of"] = "any_of"
    options: list["RequirementNode"] = Field(default_factory=list)
    label: str | None = None


class SelectNode(BaseModel):
    type: Literal["select"] = "select"
    label: str | None = None
    min_count: int | None = None
    max_count: int | None = None
    min_units: float | None = None
    max_units: float | None = None
    pool: Pool = Field(default_factory=Pool)
    constraints: list[Constraint] = Field(default_factory=list)
    bucket_id: str | None = None


RequirementNode = Annotated[
    CourseNode | TextNode | AllOfNode | AnyOfNode | SelectNode,
    Field(discriminator="type"),
]


class RequirementBlock(BaseModel):
    """A section of requirements (e.g. Major Requirements, Pre-Major, GE)."""

    id: str
    title: str
    min_units: int | None = None
    max_units: int | None = None
    kind: Literal[
        "core", "elective", "ge", "pre_major", "supporting", "other"
    ] = "other"
    items: list[RequirementItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Program(BaseModel):
    """Full program (major/minor/certificate) with requirement blocks."""

    id: ProgramId
    title: str
    level: Literal["undergraduate", "graduate"] = "undergraduate"
    type: Literal["major", "minor", "certificate"] = "major"
    catalog_year: str = ""
    total_units_required: int | None = None
    blocks: list[RequirementBlock] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RequirementBlockV2(BaseModel):
    id: str
    title: str
    min_units: int | None = None
    max_units: int | None = None
    kind: Literal[
        "core", "elective", "ge", "pre_major", "supporting", "other"
    ] = "other"
    root: RequirementNode = Field(default_factory=AllOfNode)
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProgramV2(BaseModel):
    id: ProgramId
    title: str
    level: Literal["undergraduate", "graduate"] = "undergraduate"
    type: Literal["major", "minor", "certificate"] = "major"
    catalog_year: str = ""
    total_units_required: int | None = None
    blocks: list[RequirementBlockV2] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    config: RequirementConfig = Field(default_factory=RequirementConfig)
