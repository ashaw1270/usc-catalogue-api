"""FastAPI application entrypoint."""
import httpx
from fastapi import FastAPI, HTTPException, Query

from app.cache import get_cache
from app.catalog_config import resolve_slug
from app.models import AnyOfNode, AllOfNode, CourseNode, ProgramV2, SelectNode
from app.scraper import fetch_program_v2

app = FastAPI(
    title="USC Catalogue API",
    description="API for retrieving course requirements for USC majors and minors from catalogue.usc.edu",
    version="0.1.0",
)


@app.get("/health")
async def health():
    """Simple status payload to ensure service is alive."""
    return {"status": "ok"}


async def _get_program(catoid: int, poid: int, slug: str | None, force_refresh: bool) -> ProgramV2:
    """Return program from cache or by fetching; slug only used for Program.id.slug."""
    cache = get_cache()
    if not force_refresh:
        cached = cache.get(catoid, poid, force_refresh=False)
        if cached is not None:
            return cached
    program = await fetch_program_v2(catoid, poid, slug=slug)
    cache.set(catoid, poid, program)
    return program


@app.get("/programs/by-id", response_model=ProgramV2)
async def get_program_by_id(
    catoid: int = Query(..., description="Catalog ID (e.g. 21 for 2025-2026)"),
    poid: int = Query(..., description="Program object ID"),
    force_refresh: bool = Query(False, description="Bypass cache and re-scrape"),
):
    """Fetch program requirements by catalogue id and program id."""
    try:
        return await _get_program(catoid, poid, slug=None, force_refresh=force_refresh)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Program not found")
        raise HTTPException(status_code=502, detail=f"Upstream error: {e!s}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Upstream error: {e!s}")


@app.get("/programs/{slug}", response_model=ProgramV2)
async def get_program_by_slug(
    slug: str,
    force_refresh: bool = Query(False, description="Bypass cache and re-scrape"),
):
    """Fetch program requirements by slug (e.g. csci-bs)."""
    ref = resolve_slug(slug)
    if ref is None:
        raise HTTPException(status_code=404, detail=f"Unknown program slug: {slug}")
    try:
        return await _get_program(ref.catoid, ref.poid, slug=slug, force_refresh=force_refresh)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Program not found")
        raise HTTPException(status_code=502, detail=f"Upstream error: {e!s}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Upstream error: {e!s}")


@app.get("/programs/{slug}/summary")
async def get_program_summary(
    slug: str,
    force_refresh: bool = Query(False, description="Bypass cache and re-scrape"),
):
    """Return high-level summary: total units, counts of required vs elective courses."""
    ref = resolve_slug(slug)
    if ref is None:
        raise HTTPException(status_code=404, detail=f"Unknown program slug: {slug}")
    try:
        program = await _get_program(ref.catoid, ref.poid, slug=slug, force_refresh=force_refresh)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Program not found")
        raise HTTPException(status_code=502, detail=f"Upstream error: {e!s}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Upstream error: {e!s}")

    required_count = 0
    elective_count = 0
    total_block_units = 0

    def walk(node):
        nonlocal required_count, elective_count
        if isinstance(node, CourseNode):
            required_count += 1
            return
        if isinstance(node, SelectNode):
            # Select pools are elective-like choices.
            elective_count += 1
            for child in node.pool.items:
                walk(child)
            return
        if isinstance(node, AllOfNode):
            for c in node.children:
                walk(c)
            return
        if isinstance(node, AnyOfNode):
            elective_count += 1
            for opt in node.options:
                walk(opt)
            return

    for block in program.blocks:
        if block.min_units:
            total_block_units += block.min_units
        walk(block.root)

    return {
        "title": program.title,
        "slug": slug,
        "catalog_year": program.catalog_year,
        "total_units_required": program.total_units_required,
        "block_units_sum": total_block_units,
        "required_course_count": required_count,
        "elective_course_count": elective_count,
        "block_count": len(program.blocks),
    }
