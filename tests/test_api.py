"""Integration tests for FastAPI endpoints (using HTML fixtures, no live HTTP)."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.scraper import parse_program_html  # noqa: F401


def _load_fixture(name: str) -> str:
    base = __file__.replace("test_api.py", "fixtures")
    with open(f"{base}/{name}", encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def sample_html():
    return _load_fixture("sample_program.html")


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_get_program_by_slug_unknown():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.get("/programs/unknown-slug-xyz")
    assert r.status_code == 404
    assert "Unknown program slug" in r.json()["detail"]


@pytest.mark.asyncio
async def test_get_program_by_slug_success(monkeypatch, sample_html):
    """Test /programs/csci-bs returns program when scraper returns fixture data."""
    async def mock_fetch(catoid: int, poid: int, slug: str | None = None):
        return parse_program_html(sample_html, catoid, poid, slug)

    import app.main as main_module
    monkeypatch.setattr(main_module, "fetch_program", mock_fetch)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.get("/programs/csci-bs")
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "Computer Science (BS)"
    assert data["catalog_year"] == "2025-2026"
    assert data["total_units_required"] == 128
    assert data["id"]["catoid"] == 21
    assert data["id"]["poid"] == 29994
    assert data["id"]["slug"] == "csci-bs"
    assert len(data["blocks"]) >= 5


@pytest.mark.asyncio
async def test_get_program_summary(monkeypatch, sample_html):
    async def mock_fetch(catoid: int, poid: int, slug: str | None = None):
        return parse_program_html(sample_html, catoid, poid, slug)

    import app.main as main_module
    monkeypatch.setattr(main_module, "fetch_program", mock_fetch)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.get("/programs/csci-bs/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "Computer Science (BS)"
    assert data["slug"] == "csci-bs"
    assert data["total_units_required"] == 128
    assert "required_course_count" in data
    assert "block_count" in data
