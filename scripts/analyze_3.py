from __future__ import annotations

import argparse
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_DIR = ROOT / "outputs" / "md"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "entry_exit_3"
KW_PATH = ROOT / "config" / "kw.json"

HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
EMPHASIS_RE = re.compile(r"[*_`~#>]+")
PUNCT_RE = re.compile(r"[\s:，。；、（）()\[\]{}\-_/\\|]+")


def _load_keywords() -> None:
    data = json.loads(KW_PATH.read_text(encoding="utf-8"))
    plan1 = data["plan1"]
    globals()["ENTRANCE_ALIASES"] = set(data["entrance_aliases"])
    globals()["EXIT_ALIASES"] = set(data["exit_aliases"])
    globals()["COMBINED_ALIASES"] = set(data["combined_aliases"])
    globals()["ENTRY_EXIT_SIGNAL_RE"] = re.compile(plan1["entry_exit_signal_re"])
    globals()["ENTRANCE_KEYWORDS"] = tuple(plan1["entrance_keywords"])
    globals()["EXIT_KEYWORDS"] = tuple(plan1["exit_keywords"])
    globals()["NON_EE_HEADING_KEYWORDS"] = set(plan1["non_ee_heading_keywords"])
    globals()["NARRATIVE_EXIT_PATTERNS"] = tuple(plan1["narrative_exit_patterns"])
    globals()["SENTENCE_SPLIT_RE"] = re.compile(plan1["sentence_split_re"])


_load_keywords()


@dataclass
class Section:
    title: str
    level: int
    start_line: int = 0
    end_line: int = 0
    lines: list[str] = field(default_factory=list)
    children: list["Section"] = field(default_factory=list)


def strip_front_matter_lines(text: str) -> list[str]:
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return lines[i + 1 :]
    return lines


def strip_front_matter(text: str) -> str:
    return "\n".join(strip_front_matter_lines(text))


def normalize_heading(text: str) -> str:
    text = LINK_RE.sub(r"\1", text)
    text = EMPHASIS_RE.sub("", text)
    text = text.strip()
    text = PUNCT_RE.sub("", text)
    return text


def parse_markdown_sections(text: str) -> Section:
    body_lines = strip_front_matter_lines(text)
    root = Section(title="", level=0, start_line=1, end_line=len(body_lines))
    stack: list[Section] = [root]
    for line_no, raw_line in enumerate(body_lines, 1):
        line = raw_line.rstrip()
        match = HEADING_RE.match(line)
        if match:
            hashes, title = match.groups()
            section = Section(title=title.strip(), level=len(hashes), start_line=line_no)
            while stack and stack[-1].level >= section.level:
                stack.pop()
            stack[-1].children.append(section)
            stack.append(section)
            continue
        stack[-1].lines.append(line)
    _assign_section_ranges(root, len(body_lines))
    return root


def _assign_section_ranges(section: Section, parent_end_line: int) -> None:
    section.end_line = parent_end_line
    for idx, child in enumerate(section.children):
        next_start = section.children[idx + 1].start_line if idx + 1 < len(section.children) else parent_end_line + 1
        _assign_section_ranges(child, next_start - 1)


def collect_text_lines(section: Section) -> list[str]:
    lines = list(section.lines)
    for child in section.children:
        lines.extend(collect_text_lines(child))
    return lines


def clean_block(block: str) -> str:
    block = IMAGE_RE.sub(r"\1", block)
    block = LINK_RE.sub(r"\1", block)
    block = EMPHASIS_RE.sub("", block)
    return re.sub(r"\s+", " ", block).strip()


def split_blocks(lines: Iterable[str]) -> list[str]:
    blocks: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        text = clean_block(" ".join(buffer))
        if text:
            blocks.append(text)
        buffer.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith(("-", "*")) or re.match(r"^\d+\.", stripped):
            flush()
            text = clean_block(re.sub(r"^(-|\*|\d+\.)\s*", "", stripped))
            if text:
                blocks.append(text)
            continue
        buffer.append(stripped)

    flush()
    return blocks


def unique_blocks(blocks: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for block in blocks:
        value = block.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _matches_alias(norm: str, aliases: set[str]) -> bool:
    return norm in aliases or any(alias in norm for alias in aliases)


def _is_noise_heading(title: str) -> bool:
    norm = normalize_heading(title)
    return any(keyword in norm for keyword in NON_EE_HEADING_KEYWORDS)


def _is_narrative_noise(block: str) -> bool:
    return any(re.search(pattern, block) for pattern in NARRATIVE_EXIT_PATTERNS)


def classify_block(block: str) -> str | None:
    if not ENTRY_EXIT_SIGNAL_RE.search(block):
        return None
    ent_score = sum(keyword in block for keyword in ENTRANCE_KEYWORDS)
    exit_score = sum(keyword in block for keyword in EXIT_KEYWORDS)
    if ent_score == 0 and exit_score == 0:
        return None
    if ent_score > exit_score:
        return "entrances"
    if exit_score > ent_score:
        return "exits"
    return None


def find_sections(section: Section, normalized_titles: set[str]) -> list[Section]:
    matches: list[Section] = []
    for child in section.children:
        norm = normalize_heading(child.title)
        if norm in normalized_titles or any(alias in norm for alias in normalized_titles):
            matches.append(child)
        matches.extend(find_sections(child, normalized_titles))
    return matches


def _section_has_entry_exit_signal(section: Section) -> bool:
    return bool(ENTRY_EXIT_SIGNAL_RE.search(" ".join(collect_text_lines(section))))


def _is_entry_exit_section(section: Section) -> bool:
    norm = normalize_heading(section.title)
    aliases = ENTRANCE_ALIASES | EXIT_ALIASES | COMBINED_ALIASES
    return norm in aliases or any(alias in norm for alias in aliases)


def _entry_exit_ranges(section: Section) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for child in section.children:
        if _is_entry_exit_section(child):
            ranges.append((child.start_line, child.end_line))
            continue
        ranges.extend(_entry_exit_ranges(child))
    return ranges


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1] + 1:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _exclude_ranges(lines: list[str], ranges: list[tuple[int, int]]) -> list[str]:
    if not ranges:
        return lines
    kept: list[str] = []
    merged = _merge_ranges(ranges)
    current = 1
    for start, end in merged:
        if current < start:
            kept.extend(lines[current - 1 : start - 1])
        current = max(current, end + 1)
    if current <= len(lines):
        kept.extend(lines[current - 1 :])
    return kept


def _lines_for_ranges(lines: list[str], ranges: list[tuple[int, int]]) -> list[str]:
    selected: list[str] = []
    for start, end in _merge_ranges(ranges):
        selected.extend(lines[start - 1 : end])
    return selected


def _direct_sections(section: Section, aliases: set[str]) -> list[Section]:
    matches: list[Section] = []
    for item in find_sections(section, aliases):
        norm = normalize_heading(item.title)
        if _matches_alias(norm, COMBINED_ALIASES):
            continue
        matches.append(item)
    return matches


def _ranges_from_sections(sections: list[Section]) -> list[tuple[int, int]]:
    return _merge_ranges([(section.start_line, section.end_line) for section in sections])


def _collect_non_entry_exit_lines(section: Section) -> list[str]:
    lines = list(section.lines)
    for child in section.children:
        if _is_entry_exit_section(child):
            continue
        lines.extend(_collect_non_entry_exit_lines(child))
    return lines


def _collect_entry_exit_sections(section: Section) -> list[Section]:
    matches: list[Section] = []
    for child in section.children:
        if _is_entry_exit_section(child):
            matches.append(child)
            continue
        matches.extend(_collect_entry_exit_sections(child))
    return matches


def _render_section_markdown(section: Section) -> list[str]:
    lines: list[str] = []
    if section.title:
        lines.append(f"{'#' * section.level} {section.title}")
    lines.extend(section.lines)
    for child in section.children:
        child_lines = _render_section_markdown(child)
        if lines and lines[-1].strip() and child_lines:
            lines.append("")
        lines.extend(child_lines)
    return lines


def _render_non_entry_exit_markdown(section: Section) -> list[str]:
    lines = list(section.lines)
    for child in section.children:
        if _is_entry_exit_section(child):
            continue
        child_lines = _render_section_markdown(child) if child.level > 0 else _render_non_entry_exit_markdown(child)
        if lines and lines[-1].strip() and child_lines:
            lines.append("")
        lines.extend(child_lines)
    return lines


def split_markdown_parts(text: str) -> dict[str, str]:
    tree = parse_markdown_sections(text)
    entry_exit_sections = _collect_entry_exit_sections(tree)
    entry_exit_lines: list[str] = []
    for section in entry_exit_sections:
        section_lines = _render_section_markdown(section)
        if entry_exit_lines and entry_exit_lines[-1].strip() and section_lines:
            entry_exit_lines.append("")
        entry_exit_lines.extend(section_lines)

    body_lines = _render_non_entry_exit_markdown(tree)
    return {
        "entry_exit_markdown": "\n".join(entry_exit_lines).strip(),
        "body_markdown": "\n".join(body_lines).strip(),
    }


def extract_from_combined_section(section: Section) -> tuple[list[str], list[str]]:
    entrances: list[str] = []
    exits: list[str] = []
    ent_idx: list[int] = []
    exit_idx: list[int] = []

    for i, child in enumerate(section.children):
        norm = normalize_heading(child.title)
        if _matches_alias(norm, ENTRANCE_ALIASES) and not _matches_alias(norm, COMBINED_ALIASES):
            ent_idx.append(i)
        if _matches_alias(norm, EXIT_ALIASES) and not _matches_alias(norm, COMBINED_ALIASES):
            exit_idx.append(i)

    section_blocks = split_blocks(section.lines)

    if ent_idx and exit_idx:
        first_ent = ent_idx[0]
        first_exit = exit_idx[0]
        entrances.extend(section_blocks)
        for child in section.children[first_ent:first_exit]:
            if not _is_noise_heading(child.title):
                entrances.extend(split_blocks(collect_text_lines(child)))
        for child in section.children[first_exit:]:
            if not _is_noise_heading(child.title):
                exits.extend(split_blocks(collect_text_lines(child)))
        return unique_blocks(entrances), unique_blocks(exits)

    if ent_idx:
        entrances.extend(section_blocks)
        for child in section.children[ent_idx[0] :]:
            if _section_has_entry_exit_signal(child):
                entrances.extend(split_blocks(collect_text_lines(child)))
        return unique_blocks(entrances), unique_blocks(exits)

    if exit_idx:
        exits.extend(section_blocks)
        for child in section.children[exit_idx[0] :]:
            if _section_has_entry_exit_signal(child):
                exits.extend(split_blocks(collect_text_lines(child)))
        return unique_blocks(entrances), unique_blocks(exits)

    for block in section_blocks:
        bucket = classify_block(block)
        if bucket == "entrances":
            entrances.append(block)
        elif bucket == "exits":
            exits.append(block)

    return unique_blocks(entrances), unique_blocks(exits)


def _scan_body_for_ee(text: str, tree: Section) -> tuple[list[str], list[str]]:
    entrances: list[str] = []
    exits: list[str] = []
    body_lines = strip_front_matter_lines(text)
    entry_exit_lines = _exclude_ranges(body_lines, _entry_exit_ranges(tree))
    blocks = split_blocks(entry_exit_lines)
    for block in blocks:
        if _is_narrative_noise(block):
            continue
        bucket = classify_block(block)
        if bucket == "entrances":
            entrances.append(block)
        elif bucket == "exits":
            exits.append(block)

        if len(block) > 120:
            sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(block) if s.strip() and len(s.strip()) > 10]
            for sentence in sentences:
                if _is_narrative_noise(sentence):
                    continue
                sent_bucket = classify_block(sentence)
                if sent_bucket == "entrances":
                    entrances.append(sentence)
                elif sent_bucket == "exits":
                    exits.append(sentence)
    return unique_blocks(entrances), unique_blocks(exits)


def _scan_region_for_ee(lines: list[str]) -> tuple[list[str], list[str]]:
    entrances: list[str] = []
    exits: list[str] = []
    blocks = split_blocks(lines)
    for block in blocks:
        if _is_narrative_noise(block):
            continue
        bucket = classify_block(block)
        if bucket == "entrances":
            entrances.append(block)
        elif bucket == "exits":
            exits.append(block)

        if len(block) > 120:
            sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(block) if s.strip() and len(s.strip()) > 10]
            for sentence in sentences:
                if _is_narrative_noise(sentence):
                    continue
                sent_bucket = classify_block(sentence)
                if sent_bucket == "entrances":
                    entrances.append(sentence)
                elif sent_bucket == "exits":
                    exits.append(sentence)
    return unique_blocks(entrances), unique_blocks(exits)


def _extract_from_standalone_section(section: Section, is_entrance: bool) -> tuple[list[str], list[str]]:
    entrances: list[str] = []
    exits: list[str] = []
    other_indices: list[int] = []
    search_aliases = EXIT_ALIASES if is_entrance else ENTRANCE_ALIASES

    for i, child in enumerate(section.children):
        norm = normalize_heading(child.title)
        if _matches_alias(norm, search_aliases) and not _matches_alias(norm, COMBINED_ALIASES):
            other_indices.append(i)

    blocks = split_blocks(section.lines)
    (entrances if is_entrance else exits).extend(blocks)

    if other_indices:
        split_at = other_indices[0]
        before = section.children[:split_at]
        after = section.children[split_at:]
        src_primary = entrances if is_entrance else exits
        for child in before:
            if not _is_noise_heading(child.title) and _section_has_entry_exit_signal(child):
                src_primary.extend(split_blocks(collect_text_lines(child)))
        src_other = exits if is_entrance else entrances
        for child in after:
            if not _is_noise_heading(child.title):
                src_other.extend(split_blocks(collect_text_lines(child)))
    else:
        src = entrances if is_entrance else exits
        for child in section.children:
            if _section_has_entry_exit_signal(child):
                src.extend(split_blocks(collect_text_lines(child)))

    return unique_blocks(entrances), unique_blocks(exits)


def logical_id_from_filename(path: Path) -> str:
    return path.name.removesuffix(".body.md")


def _natural_sort_key(path: Path) -> tuple[list[int], str]:
    logical_id = path.name.removesuffix(".body.md")
    nums: list[int] = []
    for part in logical_id.split("-"):
        try:
            nums.append(int(part))
        except ValueError:
            nums.append(10**9)
    return nums, logical_id


def _has_child_marker(section: Section, aliases: set[str]) -> bool:
    for child in section.children:
        norm = normalize_heading(child.title)
        if norm in aliases or any(alias in norm for alias in aliases):
            return True
    return False


def _collect_standalone_blocks(sections: list[Section], is_entrance: bool) -> tuple[list[str], list[str]]:
    entrances: list[str] = []
    exits: list[str] = []
    for section in sections:
        ent, ext = _extract_from_standalone_section(section, is_entrance=is_entrance)
        entrances.extend(ent)
        exits.extend(ext)
    return unique_blocks(entrances), unique_blocks(exits)


def _state_label(value: int) -> str:
    return {
        0: "存在",
        1: "不存在",
        2: "不确定",
    }[value]


def _resolve_section_state(
    has_direct_heading: bool,
    has_combined_section: bool,
    section_blocks: list[str],
    combined_blocks: list[str],
) -> int:
    if has_direct_heading:
        return 0 if section_blocks else 1
    if has_combined_section:
        return 0 if combined_blocks else 2
    return 1


def _body_state(blocks: list[str], allow_undetermined: bool) -> int:
    if blocks:
        return 0
    return 2 if allow_undetermined else 1


def _state_phrase(item: dict[str, object]) -> str:
    return (
        f"板块入口={item['section_entrance_label']}，"
        f"板块出口={item['section_exit_label']}，"
        f"正文入口={item['body_entrance_label']}，"
        f"正文出口={item['body_exit_label']}"
    )


def _overall_phrase(item: dict[str, object]) -> str:
    return f"{item['state_code']} | {_state_phrase(item)}"


def analyze_file(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    logical_id = logical_id_from_filename(path)
    tree = parse_markdown_sections(text)
    body_lines = strip_front_matter_lines(text)
    combined_sections = find_sections(tree, COMBINED_ALIASES)
    entrance_sections = _direct_sections(tree, ENTRANCE_ALIASES)
    exit_sections = _direct_sections(tree, EXIT_ALIASES)

    combined_ranges = _ranges_from_sections(combined_sections)
    entrance_ranges = _ranges_from_sections(entrance_sections)
    exit_ranges = _ranges_from_sections(exit_sections)
    entry_exit_ranges = _merge_ranges(combined_ranges + entrance_ranges + exit_ranges)

    combined_section = combined_sections[-1] if combined_sections else None
    has_combined_section = bool(combined_sections)
    has_standalone_entrance = bool(entrance_sections)
    has_standalone_exit = bool(exit_sections)
    has_entrance_subheading = bool(combined_section) and _has_child_marker(combined_section, ENTRANCE_ALIASES - COMBINED_ALIASES)
    has_exit_subheading = bool(combined_section) and _has_child_marker(combined_section, EXIT_ALIASES - COMBINED_ALIASES)

    entrance_lines = _lines_for_ranges(body_lines, entrance_ranges)
    exit_lines = _lines_for_ranges(body_lines, exit_ranges)
    combined_lines = _lines_for_ranges(body_lines, combined_ranges)
    body_only_lines = _exclude_ranges(body_lines, entry_exit_ranges)

    entrance_blocks, _ = _scan_region_for_ee(entrance_lines)
    _, exit_blocks = _scan_region_for_ee(exit_lines)
    combined_entrances, combined_exits = _scan_region_for_ee(combined_lines)
    body_entrances, body_exits = _scan_region_for_ee(body_only_lines)

    section_entrance_state = _resolve_section_state(has_standalone_entrance, has_combined_section, entrance_blocks, combined_entrances)
    section_exit_state = _resolve_section_state(has_standalone_exit, has_combined_section, exit_blocks, combined_exits)

    allow_body_undetermined = not (has_combined_section or has_standalone_entrance or has_standalone_exit)
    body_entrance_state = _body_state(body_entrances, allow_body_undetermined)
    body_exit_state = _body_state(body_exits, allow_body_undetermined)

    return {
        "file": path.name,
        "logical_id": logical_id,
        "state_code_1": str(section_entrance_state),
        "state_code_2": str(section_exit_state),
        "state_code_3": str(body_entrance_state),
        "state_code_4": str(body_exit_state),
        "section_entrance_state": section_entrance_state,
        "section_entrance_label": _state_label(section_entrance_state),
        "section_exit_state": section_exit_state,
        "section_exit_label": _state_label(section_exit_state),
        "body_entrance_state": body_entrance_state,
        "body_entrance_label": _state_label(body_entrance_state),
        "body_exit_state": body_exit_state,
        "body_exit_label": _state_label(body_exit_state),
        "state_code": f"{section_entrance_state}{section_exit_state}{body_entrance_state}{body_exit_state}",
        "overall_phrase": "",
        "has_combined_section": has_combined_section,
        "has_standalone_entrance": has_standalone_entrance,
        "has_standalone_exit": has_standalone_exit,
        "has_entrance_subheading": has_entrance_subheading,
        "has_exit_subheading": has_exit_subheading,
        "entry_exit_ranges": entry_exit_ranges,
    }


def finalize_item(item: dict[str, object]) -> dict[str, object]:
    item = dict(item)
    item["state_phrase"] = _state_phrase(item)
    item["overall_phrase"] = _overall_phrase(item)
    return item


def build_details_output(results: list[dict[str, object]]) -> dict[str, object]:
    return {"file_count": len(results), "results": results}


def build_summary_output(results: list[dict[str, object]]) -> dict[str, object]:
    state_code_counts: dict[str, int] = {}
    for item in results:
        code = str(item["state_code"])
        state_code_counts[code] = state_code_counts.get(code, 0) + 1
    return {
        "file_count": len(results),
        "state_code_counts": dict(sorted(state_code_counts.items())),
    }


def build_master_table_output(results: list[dict[str, object]]) -> dict[str, object]:
    return {"file_count": len(results), "records": results}


def _col_name(index: int) -> str:
    name = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _xml_escape(value: object) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _sheet_xml(rows: list[list[object]], widths: list[int] | None = None) -> str:
    max_cols = max((len(row) for row in rows), default=0)
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
    ]
    if rows:
        parts.append(f'<dimension ref="A1:{_col_name(max_cols)}{len(rows)}"/>')
        parts.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
    parts.append('<sheetFormatPr defaultRowHeight="15"/>')
    if widths:
        parts.append("<cols>")
        for idx, width in enumerate(widths, 1):
            parts.append(f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>')
        parts.append("</cols>")
    parts.append("<sheetData>")
    for r_idx, row in enumerate(rows, 1):
        parts.append(f'<row r="{r_idx}">')
        for c_idx, value in enumerate(row, 1):
            cell_ref = f"{_col_name(c_idx)}{r_idx}"
            text = _xml_escape(value)
            parts.append(f'<c r="{cell_ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        parts.append("</row>")
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def build_excel_workbook(results: list[dict[str, object]], output_path: Path) -> None:
    detail_rows: list[list[object]] = [
        [
            "level_id",
            "state_code_1",
            "state_code_2",
            "state_code_3",
            "state_code_4",
            "state_code",
            "code_1_desc",
            "code_2_desc",
            "code_3_desc",
            "code_4_desc",
            "state_desc",
            "has_combined_section",
            "has_standalone_entrance",
            "has_standalone_exit",
            "has_entrance_subheading",
            "has_exit_subheading",
        ]
    ]
    for item in results:
        detail_rows.append(
            [
                item["logical_id"],
                item["state_code_1"],
                item["state_code_2"],
                item["state_code_3"],
                item["state_code_4"],
                item["state_code"],
                f"第1位=板块入口={item['section_entrance_label']}",
                f"第2位=板块出口={item['section_exit_label']}",
                f"第3位=正文入口={item['body_entrance_label']}",
                f"第4位=正文出口={item['body_exit_label']}",
                item["overall_phrase"],
                item["has_combined_section"],
                item["has_standalone_entrance"],
                item["has_standalone_exit"],
                item["has_entrance_subheading"],
                item["has_exit_subheading"],
            ]
        )

    summary = build_summary_output(results)
    total = max(summary["file_count"], 1)
    summary_rows: list[list[object]] = [["state_code", "count", "percent"]]
    for code, count in summary["state_code_counts"].items():
        summary_rows.append([code, count, f"{count / total * 100:.2f}%"])

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""

    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""

    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <bookViews><workbookView xWindow="0" yWindow="0" windowWidth="24000" windowHeight="12000"/></bookViews>
  <sheets>
    <sheet name="Results" sheetId="1" r:id="rId1"/>
    <sheet name="Summary" sheetId="2" r:id="rId2"/>
  </sheets>
</workbook>"""

    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
</Relationships>"""

    app = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Codex</Application>
</Properties>"""

    core = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>entry_exit_3</dc:title>
  <dc:creator>Codex</dc:creator>
</cp:coreProperties>"""

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", _sheet_xml(detail_rows, [18, 10, 10, 10, 10, 12, 24, 24, 24, 24, 60, 12, 12, 12, 14, 14]))
        zf.writestr("xl/worksheets/sheet2.xml", _sheet_xml(summary_rows, [18, 10, 12]))
        zf.writestr("docProps/app.xml", app)
        zf.writestr("docProps/core.xml", core)


def build_rules_markdown() -> str:
    return """# analyze_3 四位编码规则

## 四位顺序

`state_code` 的四位顺序固定为：

1. `section_entrance`
2. `section_exit`
3. `body_entrance`
4. `body_exit`

## 数值含义

- `0`：存在
- `1`：不存在
- `2`：不确定

## section 两位

前两位表示出入口板块层面的判断。

- `0`
  - 对应入口 / 出口 section 存在，且提取到描述
- `1`
  - 对应入口 / 出口 section 不存在
  - 或存在对应结构，但没有提取到描述
- `2`
  - 存在综合出入口结构，但无法稳定区分到该侧

## body 两位

后两位表示正文层面的判断。

- `0`
  - 正文中存在对应入口 / 出口描述
- `1`
  - 正文中不存在对应描述
- `2`
  - 正文中无法稳定判断

## 示例

- `0011`
  - section 中入口与出口都存在
  - 正文中入口与出口都不存在

- `0001`
  - section 中入口与出口都存在
  - 正文中入口存在，出口不存在

- `1100`
  - section 中入口与出口都不存在
  - 正文中入口与出口都存在

- `1122`
  - section 中入口与出口都不存在
  - 正文中入口与出口都不确定
"""


def build_rules_markdown() -> str:
    return """# analyze_3 四位编码规则

## 四位顺序

`state_code` 的四位顺序固定为：

1. `state_code_1`：板块入口
2. `state_code_2`：板块出口
3. `state_code_3`：正文入口
4. `state_code_4`：正文出口

## 数值含义

- `0`：存在
- `1`：不存在
- `2`：不确定

## 板块层判断

前两位表示文章是否存在明确的“入口 / 出口”板块描述：

- `0`
  - 对应板块存在，且提取到了该侧描述
- `1`
  - 对应板块不存在
  - 或虽然存在相关结构，但没有提取到该侧描述
- `2`
  - 存在混合式出入口结构，但无法稳定判断该侧

## 正文层判断

后两位表示正文中是否存在入口 / 出口描述：

- `0`
  - 正文中存在对应描述
- `1`
  - 正文中不存在对应描述
- `2`
  - 正文中无法稳定判断

## 示例

- `0000`
  - 板块入口、板块出口、正文入口、正文出口都存在

- `0011`
  - 板块入口、板块出口存在
  - 正文入口、正文出口不存在

- `1100`
  - 板块入口、板块出口不存在
  - 正文入口、正文出口存在

- `1122`
  - 板块入口、板块出口不存在
  - 正文入口、正文出口不确定
"""


def write_outputs(results: list[dict[str, object]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    details_path = output_dir / "classification_details.json"
    summary_path = output_dir / "classification_summary.json"
    master_json_path = output_dir / "classification_master_table.json"
    excel_path = output_dir / "classification_master_table.xlsx"
    rules_path = output_dir / "classification_code_rules.md"

    details_path.write_text(json.dumps(build_details_output(results), ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(build_summary_output(results), ensure_ascii=False, indent=2), encoding="utf-8")
    master_json_path.write_text(json.dumps(build_master_table_output(results), ensure_ascii=False, indent=2), encoding="utf-8")
    build_excel_workbook(results, excel_path)
    rules_path.write_text(build_rules_markdown(), encoding="utf-8")

    print(f"Wrote details to {details_path}")
    print(f"Wrote summary to {summary_path}")
    print(f"Wrote master json to {master_json_path}")
    print(f"Wrote excel to {excel_path}")
    print(f"Wrote rules to {rules_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze Backrooms entry/exit pages with four-digit state codes.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--pattern", default="*.body.md")
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    files = sorted(args.input_dir.glob(args.pattern), key=_natural_sort_key)
    if args.limit is not None:
        files = files[: args.limit]
    results = [finalize_item(analyze_file(path)) for path in files]
    write_outputs(results, args.output_dir)


if __name__ == "__main__":
    main()
