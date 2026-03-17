"""Fetch and parse USC catalogue program pages into structured models."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Sequence

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.models import (
    AllOfNode,
    AnyOfNode,
    AtomicConstraint,
    CourseNode,
    Constraint,
    Pool,
    Program,
    ProgramId,
    RequirementBlock,
    RequirementConfig,
    RequirementNode,
    SelectNode,
    TextNode,
)


def _block_kind_from_title(title: str) -> Literal["core", "elective", "ge", "pre_major", "supporting", "other"]:
    """Infer requirement block kind from section title."""
    t = title.lower()
    if "pre-major" in t or "pre major" in t:
        return "pre_major"
    if "general education" in t or "ge " in t or "gen ed" in t:
        return "ge"
    if "elective" in t and "free" in t:
        return "elective"
    if "technical elective" in t or "elective" in t:
        return "elective"
    if "major requirement" in t or "core" in t:
        return "core"
    if "composition" in t or "writing" in t:
        return "supporting"
    return "other"


def _parse_units_from_title(title: str) -> tuple[int | None, int | None]:
    """Extract (min_units, max_units) from a block title like 'Major Requirements (62 Units)'."""
    match = re.search(r"\((\d+)\s*[Uu]nits?\)", title)
    if match:
        u = int(match.group(1))
        return (u, u)
    return (None, None)


def _parse_course_line(link_text: str, units_text: str | None) -> tuple[str, str | None, float | None]:
    """Parse 'CSCI 102L Introduction to Programming' and 'Units: 2' into course_id, title, units."""
    course_id = ""
    title = None
    # Course ID is typically first token(s): e.g. CSCI 102L, WRIT 150, MATH 125g
    parts = link_text.strip().split(None, 2)
    if len(parts) >= 2:
        # Handle "MATH 125g" or "CSCI 102L" or "WRIT 150"
        course_id = f"{parts[0]} {parts[1]}"
        if len(parts) == 3:
            title = parts[2].strip()
    elif len(parts) == 1:
        course_id = parts[0]
    units = None
    if units_text:
        um = re.search(r"Units?:\s*(\d+(?:\.\d+)?)", units_text, re.I)
        if um:
            units = float(um.group(1))
    return (course_id, title, units)


def _slug_from_title(title: str) -> str:
    """Generate a simple id/slug from block title for RequirementBlock.id."""
    s = re.sub(r"[^\w\s]", "", title.lower())
    s = re.sub(r"\s+", "_", s.strip())
    return s or "block"


def _extract_total_units(description_text: str) -> int | None:
    """Parse 'minimum requirement for the degree is 128 units' or 'Total Units: 128'."""
    m = re.search(r"(?:degree is|total units?:\s*)(\d+)\s*units?", description_text, re.I)
    return int(m.group(1)) if m else None


def _extract_program_notes(description_el) -> list[str]:
    """Collect program-wide notes from description paragraphs."""
    notes: list[str] = []
    if not description_el:
        return notes
    for p in description_el.find_all("p"):
        text = p.get_text(separator=" ", strip=True)
        if text and len(text) > 10:
            notes.append(text)
    return notes


def _parse_total_units_block(block_title: str) -> tuple[int | None, RequirementBlock | None]:
    """Parse 'Total Units: 128' block; return (total_units, optional block for display)."""
    m = re.search(r"Total\s+Units?:\s*(\d+)", block_title, re.I)
    if m:
        return (int(m.group(1)), None)
    return (None, None)


@dataclass(frozen=True)
class _Section:
    level: int  # 2,3,4 for h2/h3/h4
    title: str
    el: any
    children: list["_Section"]
    intro_paragraphs: list[str]
    uls: list[any]


def _heading_level(tag_name: str) -> int | None:
    if tag_name == "h2":
        return 2
    if tag_name == "h3":
        return 3
    if tag_name == "h4":
        return 4
    return None


def _iter_content_elements(root) -> list[any]:
    """Collect headings + p + ul in document order within block content."""
    elems: list[any] = []
    for el in root.find_all(["h2", "h3", "h4", "p", "ul"], recursive=True):
        elems.append(el)
    return elems


def _build_section_tree(root) -> list[_Section]:
    """Build a heading hierarchy tree over root; collects intro paragraphs and lists."""
    elems = _iter_content_elements(root)
    stack: list[_Section] = []
    top: list[_Section] = []

    def push_section(level: int, title: str, el: any):
        nonlocal stack, top
        node = _Section(level=level, title=title, el=el, children=[], intro_paragraphs=[], uls=[])
        while stack and stack[-1].level >= level:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            top.append(node)
        stack.append(node)

    for el in elems:
        lvl = _heading_level(el.name)
        if lvl is not None:
            push_section(lvl, el.get_text(" ", strip=True), el)
            continue
        if not stack:
            continue
        if el.name == "p":
            text = el.get_text(" ", strip=True)
            if text:
                stack[-1].intro_paragraphs.append(text)
            continue
        if el.name == "ul":
            if el.find("li", class_="acalog-course"):
                stack[-1].uls.append(el)
            continue
    return top


def _build_section_tree_from_core_divs(search_root) -> list[_Section]:
    """Build a heading hierarchy from sequential div.acalog-core blocks."""
    core_divs = search_root.find_all("div", class_="acalog-core")
    stack: list[_Section] = []
    top: list[_Section] = []

    def push(node: _Section):
        nonlocal stack, top
        while stack and stack[-1].level >= node.level:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            top.append(node)
        stack.append(node)

    for div in core_divs:
        heading = div.find(["h2", "h3", "h4"])
        if not heading:
            continue
        lvl = _heading_level(heading.name) or 2
        title = heading.get_text(" ", strip=True)
        intro_paragraphs: list[str] = []
        for p in div.find_all("p"):
            text = p.get_text(" ", strip=True)
            if text:
                intro_paragraphs.append(text)
        uls = [ul for ul in div.find_all("ul") if ul.find("li", class_="acalog-course")]
        node = _Section(level=lvl, title=title, el=heading, children=[], intro_paragraphs=intro_paragraphs, uls=uls)
        push(node)

    return top


def _parse_course_from_li(li) -> CourseNode | None:
    a = li.find("a", href=True)
    if not a:
        return None
    link_text = a.get_text(strip=True)
    span = li.find("span")
    rest = span.get_text(strip=True) if span else li.get_text(" ", strip=True)
    units_text = rest.replace(link_text, "").strip() if link_text and link_text in rest else rest
    course_id, title, units = _parse_course_line(link_text, units_text)
    if not course_id:
        return None
    return CourseNode(course_id=course_id, title=title or None, units=units)


def _extract_ul_stream(ul) -> list[tuple[CourseNode, str | None]]:
    """Extract a linear stream of (course, connector_after)."""
    stream: list[tuple[CourseNode, str | None]] = []
    course_lis = ul.find_all("li", class_="acalog-course")
    all_lis = ul.find_all("li")
    index_by_li = {li: i for i, li in enumerate(all_lis)}
    course_positions = [index_by_li[li] for li in course_lis if li in index_by_li]

    adhoc = {index_by_li[li]: li.get_text(" ", strip=True).lower() for li in all_lis if "acalog-adhoc" in (li.get("class") or [])}

    for i, li in enumerate(course_lis):
        course = _parse_course_from_li(li)
        if not course:
            continue
        connector: str | None = None

        # Connector can be in trailing span text.
        span = li.find("span")
        tail = span.get_text(" ", strip=True).lower() if span else li.get_text(" ", strip=True).lower()
        m = re.search(r"\b(and|or)\s*$", tail)
        if m:
            connector = m.group(1)
            stream.append((course, connector))
            continue

        # Or connector can be an adhoc li after the course in DOM order.
        pos = index_by_li.get(li)
        if pos is None:
            stream.append((course, connector))
            continue
        # Find next adhoc between this course and the next course.
        next_course_pos = course_positions[i + 1] if i + 1 < len(course_positions) else None
        for probe in range(pos + 1, (next_course_pos or (pos + 3)) + 1):
            t = adhoc.get(probe)
            if t in ("and", "or"):
                connector = t
                break
        stream.append((course, connector))
    return stream


def _parse_catalogue_list(stream: Sequence[tuple[CourseNode, str | None]]) -> list[RequirementNode]:
    """
    Parse USC catalogue list semantics:
    - connector 'and' joins within a sequence
    - connector 'or' creates a local choice within the current slot
    - connector None ends the current slot; slots are implicitly ANDed at the parent level
    """
    out: list[RequirementNode] = []
    if not stream:
        return out

    # Current slot: list of option sequences (each sequence is list[CourseNode])
    options: list[list[CourseNode]] = [[]]

    def flush_slot():
        nonlocal options, out
        # Remove empty sequences
        opts = [seq for seq in options if seq]
        if not opts:
            options = [[]]
            return
        if len(opts) == 1:
            seq = opts[0]
            if len(seq) == 1:
                out.append(seq[0])
            else:
                out.append(AllOfNode(children=list(seq)))
        else:
            out.append(AnyOfNode(options=[AllOfNode(children=list(seq)) if len(seq) > 1 else seq[0] for seq in opts]))
        options = [[]]

    for course, conn in stream:
        options[-1].append(course)
        if conn == "and":
            continue
        if conn == "or":
            # end current sequence and start new option sequence
            options.append([])
            continue
        # None => end of slot
        flush_slot()

    flush_slot()
    return out


_PHRASE_COUNT = re.compile(r"\b(?:take|complete|choose|select)\s+(?:at\s+least\s+)?(\d+)\s+(?:courses|classes)\b", re.I)
_PHRASE_UNITS = re.compile(r"\b(?:at\s+least|min(?:imum)?\s+of|exactly)?\s*(\d+(?:\.\d+)?)\s*units?\b", re.I)

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


def _normalize_number_words(text: str) -> str:
    def repl(m: re.Match) -> str:
        w = m.group(0).lower()
        return str(_NUMBER_WORDS.get(w, w))

    return re.sub(r"\b(" + "|".join(_NUMBER_WORDS.keys()) + r")\b", repl, text, flags=re.I)


def _parse_constraints(text: str, config: RequirementConfig) -> Constraint | None:
    raw = text.strip()
    if not raw:
        return None
    atoms: list[AtomicConstraint] = []

    # Outside SUBJECT / not in SUBJECT
    m = re.search(r"\boutside\s+(?:the\s+)?([A-Z]{3,5})\b", raw, re.I)
    if m:
        atoms.append(AtomicConstraint(kind="subject_not_in", subject=m.group(1).upper(), value=None))

    # At least K of N outside SUBJECT
    m = re.search(r"\bat\s+least\s+(\d+)\s+of\s+(?:the\s+)?(\d+)\s+must\s+be\s+outside\s+(?:the\s+)?([A-Z]{3,5})\b", raw, re.I)
    if m:
        atoms.append(AtomicConstraint(kind="subject_not_in", subject=m.group(3).upper(), value=int(m.group(1))))

    # 300- or 400-level SUBJECT
    m = re.search(r"\b(\d{3})-\s*or\s*(\d{3})-level\s+([A-Z]{3,5})\b", raw, re.I)
    if m:
        lo = int(m.group(1))
        hi = int(m.group(2))
        subj = m.group(3).upper()
        atoms.append(AtomicConstraint(kind="subject_in", subject=subj, value=None))
        atoms.append(AtomicConstraint(kind="course_number_min", value=lo))
        atoms.append(AtomicConstraint(kind="course_number_max", value=hi + 99))

    # NNN-level or above
    m = re.search(r"\b(\d{3})-level\s+or\s+above\b", raw, re.I)
    if m:
        atoms.append(AtomicConstraint(kind="course_number_min", value=int(m.group(1))))

    # SUBJECT courses
    m = re.search(r"\b([A-Z]{3,5})\s+courses?\b", raw, re.I)
    if m:
        atoms.append(AtomicConstraint(kind="subject_in", subject=m.group(1).upper(), value=None))

    # Upper-division count
    m = re.search(r"\bat\s+least\s+(\d+)\s+must\s+be\s+upper-division\b", raw, re.I)
    if m:
        atoms.append(AtomicConstraint(kind="min_upper_division_count", value=int(m.group(1))))

    # From List X
    m = re.search(r"\bfrom\s+list\s+([a-z])\b", raw, re.I)
    if m:
        atoms.append(AtomicConstraint(kind="min_from_pool", pool_name=f"List {m.group(1).upper()}", value=1))

    # 4-unit courses
    m = re.search(r"\b(\d+)\s*-\s*unit\b", raw, re.I)
    if m:
        atoms.append(AtomicConstraint(kind="each_course_units_equals", value=float(m.group(1))))

    if not atoms:
        return Constraint(raw_text=raw, parsed=[])
    return Constraint(raw_text=raw, parsed=atoms)


def _detect_select_rule(title: str, intro: list[str]) -> tuple[int | None, int | None, float | None, float | None, list[str]]:
    """Return (min_count,max_count,min_units,max_units,constraint_texts)."""
    texts = [title, *intro]
    joined = _normalize_number_words(" ".join(texts))
    constraint_texts: list[str] = []
    min_count = max_count = None
    min_units = max_units = None

    m = re.search(r"\b(?:exactly|take)\s+(\d+)\s+(?:courses|classes)\b", joined, re.I)
    if m:
        v = int(m.group(1))
        min_count = v
        max_count = v
        constraint_texts.append(joined)
        return (min_count, max_count, min_units, max_units, constraint_texts)

    # “Three courses from the following list are required”
    m = re.search(r"\b(\d+)\s+(?:courses|classes)\s+from\s+the\s+following\b", joined, re.I)
    if m:
        v = int(m.group(1))
        min_count = v
        max_count = v
        constraint_texts.append(joined)
        return (min_count, max_count, min_units, max_units, constraint_texts)

    # “Three MATH courses ... chosen from the following list”
    m = re.search(r"\b(\d+)\s+[A-Z]{3,5}\s+courses?\b.*\b(chosen\s+from|from\s+the\s+following)\b", joined, re.I)
    if m:
        v = int(m.group(1))
        min_count = v
        max_count = v
        constraint_texts.append(joined)
        return (min_count, max_count, min_units, max_units, constraint_texts)

    m = re.search(r"\bat\s+least\s+(\d+)\s+(?:courses|classes)\b", joined, re.I)
    if m:
        min_count = int(m.group(1))
        constraint_texts.append(joined)
        return (min_count, max_count, min_units, max_units, constraint_texts)

    # “Four electives …” phrasing
    m = re.search(r"\b(\d+)\s+electives?\b", joined, re.I)
    if m and "elective" in joined.lower():
        v = int(m.group(1))
        min_count = v
        max_count = None
        constraint_texts.append(joined)
        return (min_count, max_count, min_units, max_units, constraint_texts)

    m = re.search(r"\b(\d+(?:\.\d+)?)\s*units?\s+must\s+be\s+selected\b", joined, re.I)
    if m:
        min_units = float(m.group(1))
        max_units = float(m.group(1))
        constraint_texts.append(joined)
        return (min_count, max_count, min_units, max_units, constraint_texts)

    m = re.search(r"\bat\s+least\s+(\d+(?:\.\d+)?)\s*units?\b", joined, re.I)
    if m:
        min_units = float(m.group(1))
        constraint_texts.append(joined)
        return (min_count, max_count, min_units, max_units, constraint_texts)

    # Heading “(X Units)” case: treat as min_units for select-like headings that contain “Elective”.
    if "elective" in title.lower():
        mu, mx = _parse_units_from_title(title)
        if mu is not None and mx is not None:
            min_units = float(mu)
            max_units = float(mx)
    return (min_count, max_count, min_units, max_units, constraint_texts)


def _section_to_requirement(section: _Section, bucket_id: str, config: RequirementConfig, *, include_children: bool) -> RequirementNode:
    title = section.title
    intro = section.intro_paragraphs
    cue_text = " ".join(intro).lower()

    # Track choice: multiple children + cue phrase.
    if include_children and section.children and any(p in cue_text for p in ["one of the following", "choose one", "select one", "complete one", "choose one track", "one track"]):
        options: list[RequirementNode] = []
        for c in section.children:
            opt = _section_to_requirement(c, bucket_id=bucket_id, config=config, include_children=True)
            # Avoid redundant nesting/labels like AllOf(label="Biology:") inside another AllOf(label="Biology:")
            if getattr(opt, "label", None) in (None, "") and hasattr(opt, "label"):
                opt.label = c.title  # type: ignore[attr-defined]
            options.append(opt)
        return AnyOfNode(options=options, label=title)

    # Multi-pool lists: children titled List A/B etc.
    list_children = [c for c in (section.children if include_children else []) if re.match(r"^list\s+[a-z]\b", c.title.strip(), re.I)]
    if list_children:
        pools: list[Pool] = []
        for c in list_children:
            pool_items: list[RequirementNode] = []
            for ul in c.uls:
                stream = _extract_ul_stream(ul)
                pool_items.extend(_parse_catalogue_list(stream))
            pools.append(Pool(kind="explicit", name=c.title.strip(), items=pool_items))
        min_c, max_c, min_u, max_u, texts = _detect_select_rule(title, intro)
        constraints: list[Constraint] = []
        for t in texts:
            con = _parse_constraints(t, config)
            if con:
                constraints.append(con)
        # Pool-of-pools represented as explicit pool with children being labeled selects (downstream can interpret).
        pool_node_items: list[RequirementNode] = [
            SelectNode(label=p.name, pool=p, bucket_id=bucket_id) for p in pools
        ]
        return SelectNode(
            label=title,
            min_count=min_c,
            max_count=max_c,
            min_units=min_u,
            max_units=max_u,
            pool=Pool(kind="explicit", name=title, items=pool_node_items),
            constraints=constraints,
            bucket_id=bucket_id,
        )

    # Free electives by heading text.
    if "free elective" in title.lower():
        mu, mx = _parse_units_from_title(title)
        return SelectNode(
            label=title,
            min_units=float(mu) if mu is not None else 0.0,
            max_units=float(mx) if mx is not None else None,
            pool=Pool(kind="any_course"),
            bucket_id=bucket_id,
        )

    # Department-wide electives: “X units of SUBJECT electives”.
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*units?\s+of\s+([A-Z]{3,5})\s+electives\b", " ".join([title, *intro]), re.I)
    if m:
        return SelectNode(
            label=title,
            min_units=float(m.group(1)),
            pool=Pool(kind="subject", subject=m.group(2).upper()),
            bucket_id=bucket_id,
        )

    # If a section has uls and a select phrasing, build Select over pooled courses.
    min_c, max_c, min_u, max_u, texts = _detect_select_rule(title, intro)
    if (min_c is not None) or (min_u is not None):
        pool_items: list[RequirementNode] = []
        for ul in section.uls:
            stream = _extract_ul_stream(ul)
            pool_items.extend(_parse_catalogue_list(stream))
        constraints: list[Constraint] = []
        for t in texts:
            con = _parse_constraints(t, config)
            if con:
                constraints.append(con)
        return SelectNode(
            label=title,
            min_count=min_c,
            max_count=max_c,
            min_units=min_u,
            max_units=max_u,
            pool=Pool(kind="explicit", name=title, items=pool_items),
            constraints=constraints,
            bucket_id=bucket_id,
        )

    # If exactly one UL: parse inline and/or or plain list.
    if section.uls:
        children: list[RequirementNode] = []
        for ul in section.uls:
            stream = _extract_ul_stream(ul)
            parsed = _parse_catalogue_list(stream)
            for n in parsed:
                # Stamp bucket_id onto leaf courses for downstream allocation.
                def stamp(node):
                    if isinstance(node, CourseNode):
                        node.bucket_id = bucket_id  # type: ignore[misc]
                        return
                    if isinstance(node, AllOfNode):
                        for c in node.children:
                            stamp(c)
                    if isinstance(node, AnyOfNode):
                        for o in node.options:
                            stamp(o)
                    if isinstance(node, SelectNode):
                        for c in node.pool.items:
                            stamp(c)

                stamp(n)
                children.append(n)
        if children:
            return AllOfNode(children=children, label=title)

    # If subsections exist but no explicit choice language: treat as additive AllOf.
    if include_children and section.children:
        return AllOfNode(
            children=[_section_to_requirement(c, bucket_id=bucket_id, config=config, include_children=True) for c in section.children],
            label=title,
        )

    # Fallback to intro text if any.
    if intro:
        return TextNode(text=" ".join(intro))
    return AllOfNode(children=[], label=title)


def parse_program_html(html: str, catoid: int, poid: int, slug: str | None = None) -> Program:
    soup = BeautifulSoup(html, "lxml")
    warnings: list[str] = []
    config = RequirementConfig()

    title_el = soup.find("h1", id="acalog-page-title")
    title = title_el.get_text(strip=True) if title_el else ""

    catalog_el = soup.find("span", class_="acalog_catalog_name")
    catalog_year = ""
    if catalog_el:
        raw = catalog_el.get_text(strip=True)
        m = re.search(r"(\d{4}-\d{4})", raw)
        catalog_year = m.group(1) if m else raw

    level: Literal["undergraduate", "graduate"] = "undergraduate"
    program_type: Literal["major", "minor", "certificate"] = "major"
    if "minor" in title.lower():
        program_type = "minor"
    elif "certificate" in title.lower():
        program_type = "certificate"
    if "graduate" in title.lower() or "master" in title.lower() or "ph.d" in title.lower():
        level = "graduate"

    desc_el = soup.find("div", class_=re.compile("program_description"))
    description_text = desc_el.get_text(separator=" ", strip=True) if desc_el else ""
    total_units = _extract_total_units(description_text)
    program_notes = _extract_program_notes(desc_el)

    content_root = title_el.find_parent("td", class_="block_content") if title_el else None
    search_root = content_root if content_root else soup

    blocks: list[RequirementBlock] = []
    seen: set[str] = set()

    roots = _build_section_tree_from_core_divs(search_root)

    def emit_blocks(sections: list[_Section], parent_consumes_children: bool = False):
        nonlocal total_units
        for sec in sections:
            block_title = sec.title

            # Total Units: X can appear as a separate core div.
            tot, _ = _parse_total_units_block(block_title)
            if tot is not None:
                total_units = total_units or tot
                continue

            block_id = _slug_from_title(block_title)
            root_node = _section_to_requirement(sec, bucket_id=block_id, config=config, include_children=True)

            # If this section is a “container” select/choice (and has no direct course list),
            # we emit it as a single block and do not emit children separately.
            cue_text = " ".join(sec.intro_paragraphs).lower()
            is_container = (
                bool(sec.children)
                and not bool(sec.uls)
                and (
                    isinstance(root_node, (SelectNode, AnyOfNode))
                    or any(p in cue_text for p in ["one of the following", "choose one", "select one", "must be selected", "must be taken from", "take at least", "minimum of"])
                )
            )

            if not parent_consumes_children:
                min_u, max_u = _parse_units_from_title(block_title)
                kind = _block_kind_from_title(block_title)
                # If we are also going to emit children as separate blocks, do not embed them in this block's root.
                root_node = _section_to_requirement(sec, bucket_id=block_id, config=config, include_children=is_container)
                b = RequirementBlock(
                    id=block_id,
                    title=block_title,
                    min_units=min_u,
                    max_units=max_u,
                    kind=kind,
                    root=root_node,
                    notes=[],
                    warnings=[],
                )
                if b.id not in seen:
                    blocks.append(b)
                    seen.add(b.id)

            emit_blocks(sec.children, parent_consumes_children=is_container or parent_consumes_children)

    emit_blocks(roots, parent_consumes_children=False)

    return Program(
        id=ProgramId(catoid=catoid, poid=poid, slug=slug),
        title=title,
        level=level,
        type=program_type,
        catalog_year=catalog_year,
        total_units_required=total_units,
        blocks=blocks,
        notes=program_notes,
        warnings=warnings,
        config=config,
    )


async def fetch_program(catoid: int, poid: int, slug: str | None = None) -> Program:
    """Fetch program page from USC catalogue and return parsed Program."""
    url = f"{settings.catalogue_base_url.rstrip('/')}/preview_program.php"
    params = {"catoid": catoid, "poid": poid}
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
    return parse_program_html(response.text, catoid, poid, slug)
