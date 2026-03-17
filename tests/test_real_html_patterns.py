"""Semantic pattern tests against saved USC catalogue HTML pages.

These tests are optional: they are skipped if the local HTML directory is absent.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.models import AnyOfNode, SelectNode
from app.scraper import parse_program_html


def _external_fixture_dir() -> Path:
    env = os.environ.get("USC_FIXTURE_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Downloads" / "USC Course Catalogues"


def _load_external(name: str) -> str:
    base = _external_fixture_dir()
    path = base / name
    if not path.exists():
        pytest.skip(f"missing external fixture: {path}")
    return path.read_text(encoding="utf-8")


def _find_block(program, title_contains: str):
    needle = title_contains.lower()
    for b in program.blocks:
        if needle in b.title.lower():
            return b
    raise AssertionError(f"block not found containing: {title_contains!r}")


def test_applied_math_upper_division_math_electives_is_select_3():
    html = _load_external("Applied and Computational Mathematics.html")
    program = parse_program_html(html, catoid=21, poid=0, slug=None)
    block = _find_block(program, "Upper-division Math Electives")
    assert isinstance(block.root, SelectNode)
    assert block.root.min_count == 3
    assert block.root.max_count == 3
    assert block.root.pool.items, "expected non-empty pool for elective list"


def test_applied_math_four_electives_lists_a_b_is_select_and_preserves_constraints_text():
    html = _load_external("Applied and Computational Mathematics.html")
    program = parse_program_html(html, catoid=21, poid=0, slug=None)
    block = _find_block(program, "Four Electives with Significant Quantitative Content")
    assert isinstance(block.root, SelectNode)
    # Should be “at least 4” (max may be None).
    assert block.root.min_count in (4, None)
    # Multi-pool shape: pool contains Select nodes for List A / List B.
    assert any(isinstance(n, SelectNode) and (n.label or "").lower().startswith("list ") for n in block.root.pool.items)
    # Always preserve raw constraint text when present.
    if block.root.constraints:
        assert any(c.raw_text for c in block.root.constraints)


def test_lifespan_health_gerontology_electives_units_subject_pool():
    html = _load_external("Lifespan Health.html")
    program = parse_program_html(html, catoid=21, poid=0, slug=None)
    block = _find_block(program, "Gerontology Electives")
    assert isinstance(block.root, SelectNode)
    assert block.root.min_units in (12.0, 12)
    assert block.root.pool.kind in ("subject", "explicit")
    if block.root.pool.kind == "subject":
        assert block.root.pool.subject == "GERO"


def test_computer_science_basic_science_is_track_choice_like_anyof():
    html = _load_external("Computer Science.html")
    program = parse_program_html(html, catoid=21, poid=0, slug=None)
    block = _find_block(program, "Basic Science")
    # Depending on catalogue phrasing this might be AnyOf or Select; accept either but enforce it is not plain AllOf.
    assert isinstance(block.root, (AnyOfNode, SelectNode))

