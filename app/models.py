"""Pydantic models for program requirements schema."""
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
    repeatable: bool | None = None
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
