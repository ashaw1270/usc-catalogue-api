"""Unit tests for advisory requirement evaluation (planner package)."""
import os

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models import (
    AllOfNode,
    AnyOfNode,
    CourseNode,
    GeCategoryRequirement,
    GeCrossCountRule,
    GeListedCourse,
    GeOverlapPolicy,
    GeneralEducationCatalog,
    Pool,
    Program,
    ProgramId,
    RequirementBlock,
    SelectNode,
)
from app.scraper import parse_general_education_html, parse_program_html
from planner.requirement_eval import (
    build_taken_set,
    evaluate_general_education,
    evaluate_program,
    normalize_course_id,
)


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _load_fixture(name: str) -> str:
    path = os.path.join(_repo_root(), "tests", "fixtures", name)
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def sample_html():
    return _load_fixture("sample_program.html")


@pytest.fixture
def sample_ge_html():
    return _load_fixture("sample_ge_program.html")


def test_normalize_course_id_variants():
    assert normalize_course_id("csci 103l") == "CSCI 103L"
    assert normalize_course_id("CSCI103L") == "CSCI 103L"
    assert normalize_course_id("  WRIT 150  ") == "WRIT 150"


def test_build_taken_set_dedupes():
    s = build_taken_set(["CSCI 103L", "csci103l", ""])
    assert s == frozenset({"CSCI 103L"})


def _minimal_program(**kwargs) -> Program:
    base = dict(
        id=ProgramId(catoid=1, poid=1),
        title="Test Major",
        catalog_year="2025-2026",
        blocks=[],
    )
    base.update(kwargs)
    return Program(**base)


def test_all_of_all_courses_satisfied():
    prog = _minimal_program(
        blocks=[
            RequirementBlock(
                id="core",
                title="Core",
                root=AllOfNode(
                    children=[
                        CourseNode(course_id="MATH 125", units=4.0),
                        CourseNode(course_id="CSCI 103L", units=4.0),
                    ],
                ),
            ),
        ],
    )
    r = evaluate_program(prog, ["MATH 125", "csci103l"])
    assert len(r.blocks) == 1
    assert r.blocks[0].status == "satisfied"


def test_all_of_partial():
    prog = _minimal_program(
        blocks=[
            RequirementBlock(
                id="core",
                title="Core",
                root=AllOfNode(
                    children=[
                        CourseNode(course_id="A 1"),
                        CourseNode(course_id="B 2"),
                    ],
                ),
            ),
        ],
    )
    r = evaluate_program(prog, ["A 1"])
    assert r.blocks[0].status == "partial"


def test_any_of_one_option_satisfies():
    prog = _minimal_program(
        blocks=[
            RequirementBlock(
                id="pick",
                title="Pick one",
                root=AnyOfNode(
                    options=[
                        CourseNode(course_id="X 100"),
                        CourseNode(course_id="Y 200"),
                    ],
                ),
            ),
        ],
    )
    r = evaluate_program(prog, ["Y 200"])
    assert r.blocks[0].status == "satisfied"


def test_select_explicit_min_count():
    prog = _minimal_program(
        blocks=[
            RequirementBlock(
                id="electives",
                title="Two from list",
                root=SelectNode(
                    min_count=2,
                    pool=Pool(
                        kind="explicit",
                        items=[
                            CourseNode(course_id="E 1", units=2.0),
                            CourseNode(course_id="E 2", units=2.0),
                            CourseNode(course_id="E 3", units=2.0),
                        ],
                    ),
                ),
            ),
        ],
    )
    r = evaluate_program(prog, ["E 1"])
    assert r.blocks[0].status == "partial"
    r2 = evaluate_program(prog, ["E 1", "E 3"])
    assert r2.blocks[0].status == "satisfied"


def test_select_subject_pool_manual():
    prog = _minimal_program(
        blocks=[
            RequirementBlock(
                id="subj",
                title="CSCI electives",
                root=SelectNode(
                    min_units=4.0,
                    pool=Pool(kind="subject", subject="CSCI", items=[]),
                ),
            ),
        ],
    )
    r = evaluate_program(prog, ["CSCI 350"])
    assert r.blocks[0].status == "manual"
    assert r.blocks[0].detail and "auto-check" in r.blocks[0].detail.lower()


def test_select_any_course_manual():
    prog = _minimal_program(
        blocks=[
            RequirementBlock(
                id="free",
                title="Free electives",
                root=SelectNode(
                    min_units=4.0,
                    pool=Pool(kind="any_course"),
                ),
            ),
        ],
    )
    r = evaluate_program(prog, ["BUAD 101"])
    assert r.blocks[0].status == "manual"


def test_evaluate_general_education_phil_partial_and_g_satisfied(sample_ge_html):
    ge = parse_general_education_html(sample_ge_html, 21, 29462)
    rows = evaluate_general_education(ge, ["phil 174gw"])
    by_code = {r.code: r for r in rows}
    assert by_code["GE-B"].required_count == 2
    assert by_code["GE-B"].matched_count == 1
    assert by_code["GE-B"].status == "partial"
    # PHIL is dual-listed on GE-B and GE-G, but overlap policy only allows B–H and C–G pairs.
    assert by_code["GE-G"].matched_count == 0
    assert by_code["GE-G"].status == "unsatisfied"


def test_evaluate_general_education_hist_double_listing(sample_ge_html):
    ge = parse_general_education_html(sample_ge_html, 21, 29462)
    rows = evaluate_general_education(ge, ["HIST 211GP"])
    by_code = {r.code: r for r in rows}
    assert by_code["GE-C"].matched_count == 1
    assert by_code["GE-C"].status == "partial"
    # HIST is on GE-C and GE-H; C–H is not an allowed dual-count pair.
    assert by_code["GE-H"].matched_count == 0
    assert by_code["GE-H"].status == "unsatisfied"


def test_ge_double_count_when_overlap_policy_allows_pair():
    policy = GeOverlapPolicy(
        allowed_cross_count_rules=[
            GeCrossCountRule(
                source_category="GE-B",
                target_category="GE-H",
                max_shared_courses=1,
            ),
        ],
        no_other_double_counting=True,
    )
    ge = GeneralEducationCatalog(
        catoid=1,
        poid=1,
        catalog_year="2025-2026",
        source_url="",
        categories=[
            GeCategoryRequirement(
                code="GE-B",
                label="B",
                required_count=1,
                courses=[GeListedCourse(course_id="HIST 211gp")],
            ),
            GeCategoryRequirement(
                code="GE-H",
                label="H",
                required_count=1,
                courses=[GeListedCourse(course_id="HIST 211gp")],
            ),
        ],
        overlap_policy=policy,
    )
    rows = {r.code: r for r in evaluate_general_education(ge, ["HIST 211gp"])}
    assert rows["GE-B"].matched_count == 1
    assert rows["GE-H"].matched_count == 1
    assert rows["GE-B"].status == "satisfied"
    assert rows["GE-H"].status == "satisfied"


def test_same_course_cannot_satisfy_two_program_blocks():
    prog = _minimal_program(
        blocks=[
            RequirementBlock(
                id="a",
                title="Block A",
                root=CourseNode(course_id="X 100"),
            ),
            RequirementBlock(
                id="b",
                title="Block B",
                root=CourseNode(course_id="X 100"),
            ),
        ],
    )
    r = evaluate_program(prog, ["X 100"])
    assert r.blocks[0].status == "satisfied"
    assert r.blocks[1].status == "unsatisfied"


def test_course_used_by_program_not_counted_toward_ge(sample_ge_html):
    ge = parse_general_education_html(sample_ge_html, 21, 29462)
    prog = _minimal_program(
        blocks=[
            RequirementBlock(
                id="math_ge",
                title="Quantitative",
                root=CourseNode(course_id="MATH 125g"),
            ),
        ],
    )
    r = evaluate_program(prog, ["MATH 125g"], ge_catalog=ge)
    assert r.blocks[0].status == "satisfied"
    ge_f = next(x for x in r.general_education if x.code == "GE-F")
    assert ge_f.matched_count == 0
    assert ge_f.status == "unsatisfied"


def test_evaluate_program_includes_ge_when_catalog_passed(sample_ge_html):
    ge = parse_general_education_html(sample_ge_html, 21, 29462)
    prog = _minimal_program(blocks=[])
    r = evaluate_program(prog, ["MATH 125G"], ge_catalog=ge)
    assert len(r.general_education) == 8
    ge_f = next(x for x in r.general_education if x.code == "GE-F")
    assert ge_f.status == "satisfied"
    assert r.ge_catalog_year == "2025-2026"


@pytest.mark.asyncio
async def test_post_programs_evaluate(monkeypatch, sample_html, sample_ge_html):
    async def mock_fetch(catoid: int, poid: int, slug: str | None = None):
        return parse_program_html(sample_html, catoid, poid, slug)

    async def mock_get_ge(catoid: int, poid: int, force_refresh: bool = False):
        return parse_general_education_html(sample_ge_html, catoid, poid)

    import app.main as main_module

    monkeypatch.setattr(main_module, "fetch_program", mock_fetch)
    monkeypatch.setattr(main_module, "_get_ge_catalog", mock_get_ge)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.post(
            "/programs/evaluate?catoid=99&poid=1001",
            json={"taken": ["WRIT 150", "CSCI 102L"]},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "Computer Science (BS)"
    assert any(b["title"] for b in data["blocks"])
    assert len(data["general_education"]) == 8
    assert data["ge_error"] is None
