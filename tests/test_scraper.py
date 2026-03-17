"""Unit tests for USC catalogue scraper."""
import pytest

from app.scraper import parse_program_html_v2


FIXTURE_DIR = __file__.replace("test_scraper.py", "fixtures")


def _load_fixture(name: str) -> str:
    with open(f"{FIXTURE_DIR}/{name}", encoding="utf-8") as f:
        return f.read()


def test_parse_program_title_and_catalog_year():
    html = _load_fixture("sample_program.html")
    program = parse_program_html_v2(html, catoid=21, poid=29994, slug="csci-bs")
    assert program.title == "Computer Science (BS)"
    assert program.catalog_year == "2025-2026"
    assert program.id.catoid == 21
    assert program.id.poid == 29994
    assert program.id.slug == "csci-bs"


def test_parse_total_units():
    html = _load_fixture("sample_program.html")
    program = parse_program_html_v2(html, catoid=21, poid=29994)
    assert program.total_units_required == 128


def test_parse_program_notes():
    html = _load_fixture("sample_program.html")
    program = parse_program_html_v2(html, catoid=21, poid=29994)
    assert any("128 units" in n for n in program.notes)
    assert any("grade of C" in n for n in program.notes)


def test_parse_block_titles_and_units():
    html = _load_fixture("sample_program.html")
    program = parse_program_html_v2(html, catoid=21, poid=29994)
    titles = [b.title for b in program.blocks]
    assert "Composition/Writing Requirements (8 Units)" in titles
    assert "General Education (24 Units)" in titles
    assert "Engineering (2 units)" in titles
    assert "Computer Science (46 units)" in titles
    assert "Free Electives (4 Units)" in titles
    assert program.total_units_required == 128

    comp_block = next(b for b in program.blocks if "Composition" in b.title)
    assert comp_block.min_units == 8
    assert comp_block.max_units == 8


def test_parse_course_requirements():
    html = _load_fixture("sample_program.html")
    program = parse_program_html_v2(html, catoid=21, poid=29994)
    comp_block = next(b for b in program.blocks if "Composition" in b.title)
    from app.models import AllOfNode, CourseNode

    assert isinstance(comp_block.root, AllOfNode)
    courses = [c for c in comp_block.root.children if isinstance(c, CourseNode)]
    assert len(courses) == 2
    assert courses[0].course_id == "WRIT 150"
    assert courses[0].title == "Writing and Critical Reasoning"
    assert courses[0].units == 4.0
    assert courses[1].course_id == "WRIT 340"
    assert courses[1].units == 4.0

    cs_block = next(b for b in program.blocks if "Computer Science" in b.title and "46" in b.title)
    assert isinstance(cs_block.root, AllOfNode)
    courses = [c for c in cs_block.root.children if isinstance(c, CourseNode)]
    assert len(courses) >= 2
    assert courses[0].course_id == "CSCI 102L"
    assert courses[0].units == 2.0
    assert any(c.course_id == "CSCI 103L" for c in courses)


def test_parse_free_electives_block():
    html = _load_fixture("sample_program.html")
    program = parse_program_html_v2(html, catoid=21, poid=29994)
    free_block = next(b for b in program.blocks if "Free Electives" in b.title)
    from app.models import Pool, SelectNode

    assert isinstance(free_block.root, SelectNode)
    assert free_block.root.min_units == 4.0
    assert isinstance(free_block.root.pool, Pool)
    assert free_block.root.pool.kind == "any_course"


def test_level_and_type_inference():
    html = _load_fixture("sample_program.html")
    program = parse_program_html_v2(html, catoid=21, poid=29994)
    assert program.level == "undergraduate"
    assert program.type == "major"
