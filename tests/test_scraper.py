"""Unit tests for USC catalogue scraper."""
import pytest

from app.models import AnyOfNode
from app.scraper import parse_general_education_html, parse_program_html


FIXTURE_DIR = __file__.replace("test_scraper.py", "fixtures")


def _load_fixture(name: str) -> str:
    with open(f"{FIXTURE_DIR}/{name}", encoding="utf-8") as f:
        return f.read()


def test_parse_program_title_and_catalog_year():
    html = _load_fixture("sample_program.html")
    program = parse_program_html(html, catoid=21, poid=29994, slug="csci-bs")
    assert program.title == "Computer Science (BS)"
    assert program.catalog_year == "2025-2026"
    assert program.id.catoid == 21
    assert program.id.poid == 29994
    assert program.id.slug == "csci-bs"


def test_parse_total_units():
    html = _load_fixture("sample_program.html")
    program = parse_program_html(html, catoid=21, poid=29994)
    assert program.total_units_required == 128


def test_parse_program_notes():
    html = _load_fixture("sample_program.html")
    program = parse_program_html(html, catoid=21, poid=29994)
    assert any("128 units" in n for n in program.notes)
    assert any("grade of C" in n for n in program.notes)


def test_parse_block_titles_and_units():
    html = _load_fixture("sample_program.html")
    program = parse_program_html(html, catoid=21, poid=29994)
    titles = [b.title for b in program.blocks]
    assert "Composition/Writing Requirements (8 Units)" in titles
    assert "General Education (24 Units)" in titles
    assert "Engineering (2 units)" in titles
    assert "Computer Science (46 units)" in titles
    assert "Free Electives (4 Units)" in titles
    assert program.total_units_required == 128


def test_pre_major_and_major_group_headers_collapse_with_parent_on_subsections():
    """h2 Pre-Major / Major Requirements with no course lists collapse; children get parent_*."""
    html = """<!DOCTYPE html><html><body><td class="block_content">
    <h1 id="acalog-page-title">Computer Science (BS)</h1>
    <div class="custom_leftpad_20">
      <div class="acalog-core">
        <h2>Pre-Major Requirements (30 Units)</h2><hr>
      </div>
      <div class="acalog-core">
        <h3>Mathematics (16 units)</h3><hr>
        <ul><li class="acalog-course"><span><a href="#">MATH 125 Calculus</a> Units: 4</span></li></ul>
      </div>
      <div class="acalog-core">
        <h3>Basic Science (8 units)</h3><hr>
        <p>One of the following sequences:</p>
      </div>
      <div class="acalog-core">
        <h4>Biology:</h4><hr>
        <ul><li class="acalog-course"><span><a href="#">BISC 120</a> Units: 4</span></li></ul>
      </div>
      <div class="acalog-core">
        <h2>Major Requirements (40 Units)</h2><hr>
      </div>
      <div class="acalog-core">
        <h3>Computer Science (20 units)</h3><hr>
        <ul><li class="acalog-course"><span><a href="#">CSCI 102L</a> Units: 2</span></li></ul>
      </div>
    </div></td></body></html>"""
    program = parse_program_html(html, catoid=21, poid=1)
    titles = [b.title for b in program.blocks]
    assert "Pre-Major Requirements (30 Units)" not in titles
    assert "Major Requirements (40 Units)" not in titles
    math = next(b for b in program.blocks if "Mathematics" in b.title)
    assert math.parent_title == "Pre-Major Requirements (30 Units)"
    assert math.parent_id == "premajor_requirements_30_units"
    basic = next(b for b in program.blocks if "Basic Science" in b.title)
    assert basic.parent_title == "Pre-Major Requirements (30 Units)"
    assert isinstance(basic.root, AnyOfNode)
    cs = next(b for b in program.blocks if "Computer Science" in b.title and "20" in b.title)
    assert cs.parent_title == "Major Requirements (40 Units)"
    assert cs.parent_id == "major_requirements_40_units"

    comp_block = next(b for b in program.blocks if "Composition" in b.title)
    assert comp_block.min_units == 8
    assert comp_block.max_units == 8


def test_parse_course_requirements():
    html = _load_fixture("sample_program.html")
    program = parse_program_html(html, catoid=21, poid=29994)
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
    program = parse_program_html(html, catoid=21, poid=29994)
    free_block = next(b for b in program.blocks if "Free Electives" in b.title)
    from app.models import Pool, SelectNode

    assert isinstance(free_block.root, SelectNode)
    assert free_block.root.min_units == 4.0
    assert isinstance(free_block.root.pool, Pool)
    assert free_block.root.pool.kind == "any_course"


def test_level_and_type_inference():
    html = _load_fixture("sample_program.html")
    program = parse_program_html(html, catoid=21, poid=29994)
    assert program.level == "undergraduate"
    assert program.type == "major"


def test_parse_general_education_categories_and_required_counts():
    html = _load_fixture("sample_ge_program.html")
    ge = parse_general_education_html(html, catoid=21, poid=29462)

    assert ge.catalog_year == "2025-2026"
    assert len(ge.categories) == 8

    by_code = {c.code: c for c in ge.categories}
    assert by_code["GE-A"].required_count == 1
    assert by_code["GE-B"].required_count == 2
    assert by_code["GE-C"].required_count == 2
    assert by_code["GE-D"].required_count == 1
    assert by_code["GE-E"].required_count == 1
    assert by_code["GE-F"].required_count == 1
    assert by_code["GE-G"].required_count == 1
    assert by_code["GE-H"].required_count == 1


def test_parse_general_education_overlap_index_and_policy():
    html = _load_fixture("sample_ge_program.html")
    ge = parse_general_education_html(html, catoid=21, poid=29462)

    assert "PHIL 174gw" in ge.course_to_categories
    assert ge.course_to_categories["PHIL 174gw"] == ["GE-B", "GE-G"]
    assert ge.course_to_categories["HIST 211gp"] == ["GE-C", "GE-H"]

    rules = {(r.source_category, r.target_category, r.max_shared_courses) for r in ge.overlap_policy.allowed_cross_count_rules}
    assert ("GE-B", "GE-H", 1) in rules
    assert ("GE-C", "GE-G", 1) in rules
    assert ge.overlap_policy.no_other_double_counting is True


def test_parse_general_education_specific_students_flag():
    html = _load_fixture("sample_ge_program.html")
    ge = parse_general_education_html(html, catoid=21, poid=29462)

    by_code = {c.code: c for c in ge.categories}
    g_courses = {c.course_id: c.specific_students_only for c in by_code["GE-G"].courses}
    assert g_courses["PHIL 174gw"] is False
    assert g_courses["CORE 104gw"] is True


def test_parse_general_education_us_catalog_h5_then_h4_course_list():
    """USC GE pages use h5 (GE-A.) for prose then h4 (GE-A:) for the real course <ul>."""
    html = """<!doctype html><html><body>
    <span class="acalog_catalog_name">USC Catalogue 2025-2026</span>
    <h5>GE-A. The Arts</h5><p>Intro text only.</p>
    <h5>GE-B. Humanistic Inquiry</h5><p>More intro.</p>
    <h4>GE-A: The Arts</h4><ul>
      <li class="acalog-course"><span><a href="#">CTCS 190g Introduction to Cinema</a> Units: 4</span></li>
      <li class="acalog-course"><span><a href="#">AHIS 120gp Foundations of Western Art</a> Units: 4</span></li>
    </ul>
    <h4>GE-B: Humanistic Inquiry</h4><ul>
      <li class="acalog-course"><span><a href="#">ENGL 170g The Monster and the Detective</a> Units: 4</span></li>
    </ul>
    </body></html>"""
    ge = parse_general_education_html(html, catoid=21, poid=29462)
    by_code = {c.code: c for c in ge.categories}
    assert [c.course_id for c in by_code["GE-A"].courses] == ["CTCS 190g", "AHIS 120gp"]
    assert all(not c.specific_students_only for c in by_code["GE-A"].courses)
    assert [c.course_id for c in by_code["GE-B"].courses] == ["ENGL 170g"]
