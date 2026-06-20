from __future__ import annotations

import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
MD_DIR = ROOT / "outputs" / "md"
PLAN1_PATH = ROOT / "outputs" / "entry_exit_1" / "entry_exit_descriptions.json"
PLAN2_PATH = ROOT / "outputs" / "entry_exit_2" / "level_references.json"
PLAN3_PATH = ROOT / "outputs" / "entry_exit_3" / "classification_master_table.json"
OUTPUT_DIR = ROOT / "outputs" / "entry_exit_review_ui"
ANNOTATION_PATH = OUTPUT_DIR / "review_annotations.json"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _strip_front_matter_lines(text: str) -> list[str]:
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return lines[i + 1 :]
    return lines


def _safe_text(value: Any) -> str:
    return "" if value is None else str(value)


def _clean_markdown_text(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r" \1 ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r" \1 ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_~>#\-\|]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_phrase_candidates(lines: list[str], limit: int = 240) -> list[dict[str, Any]]:
    cleaned = _clean_markdown_text("\n".join(lines))
    counter: Counter[str] = Counter()

    for match in re.findall(r"[\u4e00-\u9fff]{2,24}", cleaned):
        value = match.strip()
        if 2 <= len(value) <= 8:
            counter[value] += 1
        if len(value) >= 4:
            max_n = min(6, len(value))
            for size in range(2, max_n + 1):
                for i in range(0, len(value) - size + 1):
                    chunk = value[i : i + size]
                    counter[chunk] += 1

    for match in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{1,31}", cleaned):
        value = match.strip()
        if len(value) >= 2:
            counter[value] += 1

    stop_words = {
        "level",
        "entity",
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "backrooms",
        "入口",
        "出口",
        "作者",
        "详情",
        "正文",
        "楼层",
        "层级",
        "介绍",
    }
    filtered: list[tuple[str, int]] = []
    for phrase, count in counter.items():
        lowered = phrase.lower()
        if lowered in stop_words:
            continue
        if phrase.isdigit():
            continue
        if len(phrase) == 2 and count < 2:
            continue
        if re.fullmatch(r"[_\-\s]+", phrase):
            continue
        filtered.append((phrase, count))

    filtered.sort(key=lambda item: (-item[1], -len(item[0]), item[0]))
    return [{"text": phrase, "count": count} for phrase, count in filtered[:limit]]


class ReviewRepository:
    def __init__(self) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.plan1 = self._load_plan1()
        self.plan2 = self._load_plan2()
        self.plan3 = self._load_plan3()
        self.level_ids = sorted(set(self.plan1) | set(self.plan2) | set(self.plan3))
        self.annotations = self._load_annotations()

    def _load_plan1(self) -> dict[str, dict[str, Any]]:
        data = _read_json(PLAN1_PATH)
        return {item["logical_id"]: item for item in data.get("results", [])}

    def _load_plan2(self) -> dict[str, dict[str, Any]]:
        data = _read_json(PLAN2_PATH)
        result: dict[str, dict[str, Any]] = {}
        for item in data.get("records", []):
            result[item["source"]] = item
        return result

    def _load_plan3(self) -> dict[str, dict[str, Any]]:
        data = _read_json(PLAN3_PATH)
        return {item["logical_id"]: item for item in data.get("records", [])}

    def _load_annotations(self) -> dict[str, Any]:
        if not ANNOTATION_PATH.exists():
            return {}
        return _read_json(ANNOTATION_PATH)

    def save_annotations(self) -> None:
        ANNOTATION_PATH.write_text(json.dumps(self.annotations, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_level_data(self, level_id: str) -> dict[str, Any]:
        file_path = MD_DIR / f"{level_id}.body.md"
        raw_text = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
        lines = _strip_front_matter_lines(raw_text)
        line_items = [{"line": idx + 1, "text": line} for idx, line in enumerate(lines)]

        plan1 = self.plan1.get(level_id, {})
        plan2 = self.plan2.get(level_id, {})
        plan3 = self.plan3.get(level_id, {})
        annotation = self.annotations.get(level_id, self._default_annotation())

        return {
            "level_id": level_id,
            "file_exists": file_path.exists(),
            "file_path": str(file_path),
            "line_count": len(lines),
            "lines": line_items,
            "phrase_candidates": _extract_phrase_candidates(lines),
            "plan1": {
                "entrances": plan1.get("entrances", []),
                "exits": plan1.get("exits", []),
                "entrance_level_ids": plan1.get("entrance_level_ids", []),
                "exit_level_ids": plan1.get("exit_level_ids", []),
                "confidence": plan1.get("confidence", ""),
            },
            "plan2": {
                "summary": plan2.get("summary", {}),
                "edges": plan2.get("edges", []),
            },
            "plan3": {
                "state_code": plan3.get("state_code", ""),
                "state_code_1": plan3.get("state_code_1", ""),
                "state_code_2": plan3.get("state_code_2", ""),
                "state_code_3": plan3.get("state_code_3", ""),
                "state_code_4": plan3.get("state_code_4", ""),
                "entry_exit_ranges": plan3.get("entry_exit_ranges", []),
                "state_phrase": plan3.get("state_phrase", ""),
            },
            "annotation": annotation,
        }

    @staticmethod
    def _default_annotation() -> dict[str, Any]:
        return {
            "entry_exit_range": None,
            "author_ranges": [],
            "body_ranges": [],
            "notes": "",
            "checked": False,
            "entry_keywords": [],
            "exit_keywords": [],
            "keyword_notes": "",
        }


class Api:
    def __init__(self) -> None:
        self.repo = ReviewRepository()

    def bootstrap(self) -> dict[str, Any]:
        current = random.choice(self.repo.level_ids) if self.repo.level_ids else ""
        return {
            "level_ids": self.repo.level_ids,
            "current": self.repo.get_level_data(current) if current else {},
            "output_dir": str(OUTPUT_DIR),
            "annotation_path": str(ANNOTATION_PATH),
        }

    def load_level(self, level_id: str) -> dict[str, Any]:
        return self.repo.get_level_data(level_id)

    def random_level(self, current_level: str | None = None) -> dict[str, Any]:
        pool = [level_id for level_id in self.repo.level_ids if level_id != current_level] or self.repo.level_ids
        if not pool:
            return {}
        return self.repo.get_level_data(random.choice(pool))

    def save_annotation(self, level_id: str, annotation: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            "entry_exit_range": self._normalize_single_range(annotation.get("entry_exit_range")),
            "author_ranges": self._normalize_ranges(annotation.get("author_ranges", [])),
            "body_ranges": self._normalize_ranges(annotation.get("body_ranges", [])),
            "notes": _safe_text(annotation.get("notes", "")),
            "checked": bool(annotation.get("checked", False)),
            "entry_keywords": self._normalize_keywords(annotation.get("entry_keywords", [])),
            "exit_keywords": self._normalize_keywords(annotation.get("exit_keywords", [])),
            "keyword_notes": _safe_text(annotation.get("keyword_notes", "")),
        }
        self.repo.annotations[level_id] = normalized
        self.repo.save_annotations()
        return {"ok": True, "annotation": normalized}

    @staticmethod
    def _normalize_single_range(value: Any) -> dict[str, int] | None:
        if not isinstance(value, dict):
            return None
        start = int(value.get("start", 0) or 0)
        end = int(value.get("end", 0) or 0)
        if start <= 0 or end <= 0:
            return None
        if start > end:
            start, end = end, start
        return {"start": start, "end": end}

    def _normalize_ranges(self, values: Any) -> list[dict[str, int]]:
        if not isinstance(values, list):
            return []
        ranges: list[dict[str, int]] = []
        for item in values:
            normalized = self._normalize_single_range(item)
            if normalized is not None:
                ranges.append(normalized)
        ranges.sort(key=lambda item: (item["start"], item["end"]))
        return ranges

    @staticmethod
    def _normalize_keywords(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        cleaned = []
        seen: set[str] = set()
        for value in values:
            text = _safe_text(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        return cleaned


HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Entry Exit Review</title>
  <style>
    :root {
      --bg: #f4efe7;
      --panel: #fffaf3;
      --ink: #222018;
      --muted: #7a7262;
      --accent: #a4461f;
      --accent-soft: #f2d9c6;
      --line: #ddd2c1;
      --ok: #295a32;
      --blue: #e4edf8;
      --green: #e7f4e4;
      --orange: #f9dec9;
      --chip: #f3eee7;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Microsoft YaHei UI", "PingFang SC", sans-serif;
      background: linear-gradient(135deg, #efe4d6 0%, #f8f4ee 46%, #e9ddd0 100%);
      color: var(--ink);
    }
    .app {
      display: grid;
      grid-template-rows: auto 1fr;
      gap: 14px;
      height: 100vh;
      padding: 16px;
    }
    .panel {
      background: rgba(255, 250, 243, 0.93);
      border: 1px solid rgba(162, 126, 84, 0.18);
      border-radius: 18px;
      box-shadow: 0 18px 60px rgba(78, 56, 27, 0.12);
      overflow: hidden;
      min-height: 0;
    }
    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(255,255,255,0.72), rgba(249,241,230,0.88));
    }
    .topbar h1 {
      margin: 0;
      font-size: 20px;
    }
    .status {
      color: var(--muted);
      font-size: 13px;
    }
    .toolbar, .row {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }
    .tabs {
      display: flex;
      gap: 8px;
      padding: 12px 18px 0;
      flex-wrap: wrap;
    }
    .tab-btn {
      border: 1px solid var(--line);
      background: #f1e8dc;
      color: var(--ink);
      border-radius: 999px;
      padding: 8px 14px;
      cursor: pointer;
    }
    .tab-btn.active {
      background: var(--accent);
      color: white;
      border-color: var(--accent);
    }
    .content {
      height: calc(100% - 62px);
      padding: 14px 18px 18px;
      min-height: 0;
    }
    .page {
      display: none;
      height: 100%;
      min-height: 0;
    }
    .page.active {
      display: block;
    }
    .grid-two {
      display: grid;
      grid-template-columns: 1.15fr 0.85fr;
      gap: 16px;
      height: 100%;
      min-height: 0;
    }
    .subpanel {
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(255,255,255,0.72);
      min-height: 0;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    .subpanel-head {
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
    }
    .subpanel-head h2, .subpanel-head h3 {
      margin: 0;
      font-size: 16px;
    }
    .subpanel-body {
      padding: 12px 14px;
      min-height: 0;
      overflow: auto;
      height: 100%;
    }
    .split-body {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .sticky-block {
      flex: 0 0 auto;
    }
    select, input, textarea, button {
      font: inherit;
    }
    select, input, textarea {
      background: white;
      color: var(--ink);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 8px 10px;
    }
    button {
      border: none;
      border-radius: 10px;
      padding: 8px 12px;
      background: var(--accent);
      color: white;
      cursor: pointer;
    }
    button.alt {
      background: #d7c6b2;
      color: var(--ink);
    }
    button.ghost {
      background: #f1e8dc;
      color: var(--ink);
      border: 1px solid var(--line);
    }
    button.warn {
      background: #c2682a;
    }
    textarea {
      width: 100%;
      min-height: 110px;
      resize: vertical;
    }
    .block {
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px;
      margin-bottom: 12px;
      background: rgba(255,255,255,0.72);
    }
    .block:last-child {
      margin-bottom: 0;
    }
    .block h3 {
      margin: 0 0 10px;
      font-size: 15px;
    }
    .subgrid {
      display: grid;
      gap: 10px;
    }
    .mini {
      font-size: 12px;
      color: var(--muted);
    }
    .pill {
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 12px;
      margin-right: 6px;
    }
    .range-chip, .keyword-chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      background: var(--chip);
      border-radius: 999px;
      border: 1px solid var(--line);
      margin: 4px 6px 0 0;
    }
    .line-list {
      display: grid;
      gap: 2px;
      font-family: Consolas, "Courier New", monospace;
      font-size: 13px;
      min-height: 0;
      overflow: auto;
      padding-right: 4px;
      height: 100%;
    }
    .line {
      display: grid;
      grid-template-columns: 62px 1fr;
      gap: 10px;
      padding: 5px 8px;
      border-radius: 8px;
      cursor: pointer;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .line:hover { background: #f4eadf; }
    .line-num {
      color: var(--muted);
      text-align: right;
      user-select: none;
    }
    .line.entry-exit { background: var(--orange); }
    .line.author { background: var(--blue); }
    .line.body { background: var(--green); }
    .line.current-pick { outline: 2px solid var(--accent); }
    .scheme-item {
      margin: 6px 0;
      padding: 8px 10px;
      background: #fff;
      border-radius: 10px;
      border: 1px solid #eee2d3;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .candidate-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
      gap: 10px;
    }
    .candidate-card {
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #fff;
      padding: 10px 12px;
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
    }
    .candidate-card button {
      padding: 6px 10px;
      border-radius: 999px;
    }
    .keyword-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
      gap: 10px;
    }
    .keyword-btn {
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: white;
      color: var(--ink);
      text-align: left;
    }
    .keyword-btn.entry {
      background: var(--green);
      border-color: #9ac391;
    }
    .keyword-btn.exit {
      background: var(--orange);
      border-color: #d49a76;
    }
    .keyword-btn.both {
      background: linear-gradient(135deg, var(--green) 0%, var(--orange) 100%);
      border-color: #bb9368;
    }
    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 12px;
    }
    .muted-box {
      padding: 12px;
      border: 1px dashed var(--line);
      border-radius: 14px;
      color: var(--muted);
      background: rgba(255,255,255,0.5);
    }
    .hidden {
      display: none;
    }
  </style>
</head>
<body>
  <div class="app">
    <section class="panel">
      <div class="topbar">
        <div>
          <h1 id="title">层级人工校验台</h1>
          <div class="status" id="meta"></div>
        </div>
        <div class="toolbar">
          <select id="levelSelect"></select>
          <button id="loadBtn">加载层级</button>
          <button id="randomBtn" class="alt">随机层级</button>
          <button id="saveBtn">保存当前层级</button>
        </div>
      </div>
      <div class="tabs">
        <button class="tab-btn active" data-page="range">范围校验</button>
        <button class="tab-btn" data-page="phrase">词语查看</button>
        <button class="tab-btn" data-page="keyword">关键词选择</button>
        <span class="status" id="saveStatus"></span>
      </div>
      <div class="content">
        <section class="page active" id="page-range">
          <div class="grid-two">
            <div class="subpanel">
              <div class="subpanel-head">
                <h2>原文与区间标注</h2>
                <span class="mini">左键起点，右键终点；Ctrl=正文，Shift=出入口，Alt=作者详情</span>
              </div>
              <div class="subpanel-body split-body">
                <div class="block sticky-block">
                  <h3>区间标注</h3>
                  <div class="subgrid">
                    <div class="row">
                      <span class="mini">当前目标</span>
                      <span class="pill" id="targetBadge">正文区域</span>
                    </div>
                    <div class="row">
                      <input id="rangeStart" type="number" placeholder="开始行">
                      <input id="rangeEnd" type="number" placeholder="结束行">
                      <button id="applyRangeBtn">应用</button>
                      <button id="clearDraftBtn" class="ghost">清空草稿</button>
                    </div>
                    <div>
                      <div class="mini">入口出口板块</div>
                      <div id="entryExitRangeBox"></div>
                    </div>
                    <div>
                      <div class="mini">作者详情相关</div>
                      <div id="authorRangeBox"></div>
                    </div>
                    <div>
                      <div class="mini">正文区域</div>
                      <div id="bodyRangeBox"></div>
                    </div>
                  </div>
                </div>
                <div class="line-list" id="lineList"></div>
              </div>
            </div>

            <div class="subpanel">
              <div class="subpanel-head">
                <h2>方案结果与备注</h2>
                <label class="row" style="gap:6px;">
                  <input id="checkedBox" type="checkbox">
                  <span class="mini">已人工检查</span>
                </label>
              </div>
              <div class="subpanel-body">
                <div class="block">
                  <h3>方案一</h3>
                  <div class="scheme-item">
                    <span class="pill">入口</span>
                    <div id="plan1Entrances"></div>
                  </div>
                  <div class="scheme-item">
                    <span class="pill">出口</span>
                    <div id="plan1Exits"></div>
                  </div>
                </div>

                <div class="block">
                  <h3>方案二</h3>
                  <div class="mini" id="plan2Summary"></div>
                  <div id="plan2Edges"></div>
                </div>

                <div class="block">
                  <h3>方案三</h3>
                  <div id="plan3State"></div>
                </div>

                <div class="block">
                  <h3>范围备注</h3>
                  <textarea id="notes" placeholder="记录你对这一层级范围划分的判断"></textarea>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section class="page" id="page-phrase">
          <div class="grid-two">
            <div class="subpanel">
              <div class="subpanel-head">
                <h2>词语候选</h2>
                <span class="mini">这里只做浏览和人工观察，不会自动当作关键词</span>
              </div>
              <div class="subpanel-body">
                <div class="candidate-grid" id="phraseGrid"></div>
              </div>
            </div>
            <div class="subpanel">
              <div class="subpanel-head">
                <h2>说明</h2>
              </div>
              <div class="subpanel-body">
                <div class="muted-box">
                  这一页是给你快速浏览整篇文章里反复出现的词语或短语。它的作用是辅助判断，不会直接决定入口、出口，也不会替代区间校验。
                </div>
                <div class="block">
                  <h3>方案一到三的结果</h3>
                  <div class="scheme-item"><strong>方案一入口</strong><div id="phrasePlan1Entrances"></div></div>
                  <div class="scheme-item"><strong>方案一出口</strong><div id="phrasePlan1Exits"></div></div>
                  <div class="scheme-item"><strong>方案三编码</strong><div id="phrasePlan3State"></div></div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section class="page" id="page-keyword">
          <div class="grid-two">
            <div class="subpanel">
              <div class="subpanel-head">
                <h2>关键词选择</h2>
                <span class="mini">左键切换为入口关键词，右键切换为出口关键词；再次点击可取消</span>
              </div>
              <div class="subpanel-body">
                <div class="legend">
                  <span class="pill">空白 = 未选择</span>
                  <span class="pill" style="background:#e7f4e4;color:#295a32;">绿色 = 入口</span>
                  <span class="pill" style="background:#f9dec9;color:#8b4a27;">橙色 = 出口</span>
                  <span class="pill" style="background:linear-gradient(135deg, #e7f4e4 0%, #f9dec9 100%);color:#6f4a29;">渐变 = 两边都选</span>
                </div>
                <div class="keyword-grid" id="keywordGrid"></div>
              </div>
            </div>
            <div class="subpanel">
              <div class="subpanel-head">
                <h2>已选结果</h2>
              </div>
              <div class="subpanel-body">
                <div class="block">
                  <h3>入口关键词</h3>
                  <div id="entryKeywordBox"></div>
                </div>
                <div class="block">
                  <h3>出口关键词</h3>
                  <div id="exitKeywordBox"></div>
                </div>
                <div class="block">
                  <h3>关键词备注</h3>
                  <textarea id="keywordNotes" placeholder="记录为什么这个词适合做入口或出口关键词"></textarea>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </section>
  </div>

  <script>
    const state = {
      bootstrap: null,
      current: null,
      draftClick: [],
      annotation: null,
      currentTarget: "body",
      activePage: "range",
      dirty: false,
      savedSnapshot: ""
    };

    const el = (id) => document.getElementById(id);

    function escapeHtml(text) {
      return String(text ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }

    function escapeAttr(text) {
      return escapeHtml(text)
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function clone(obj) {
      return JSON.parse(JSON.stringify(obj));
    }

    function sortedStrings(values) {
      return [...(values || [])].sort((a, b) => String(a).localeCompare(String(b), "zh-CN"));
    }

    function normalizedAnnotationForSnapshot() {
      const entry = sortedStrings(state.annotation.entry_keywords || []);
      const exit = sortedStrings(state.annotation.exit_keywords || []);
      return {
        entry_exit_range: state.annotation.entry_exit_range || null,
        author_ranges: state.annotation.author_ranges || [],
        body_ranges: state.annotation.body_ranges || [],
        notes: state.annotation.notes || "",
        checked: !!state.annotation.checked,
        entry_keywords: entry,
        exit_keywords: exit,
        keyword_notes: state.annotation.keyword_notes || ""
      };
    }

    function updateDirtyState() {
      const currentSnapshot = JSON.stringify(normalizedAnnotationForSnapshot());
      state.dirty = currentSnapshot !== state.savedSnapshot;
      const suffix = state.dirty ? "有未保存内容" : "已同步";
      el("saveStatus").textContent = suffix;
    }

    function markDirty() {
      updateDirtyState();
    }

    function markSaved() {
      state.savedSnapshot = JSON.stringify(normalizedAnnotationForSnapshot());
      updateDirtyState();
    }

    function inRanges(lineNo, ranges) {
      return (ranges || []).some(r => lineNo >= r.start && lineNo <= r.end);
    }

    function getTargetLabel(target) {
      if (target === "entry_exit") return "入口出口板块";
      if (target === "author") return "作者详情相关";
      return "正文区域";
    }

    function syncTargetBadge() {
      el("targetBadge").textContent = getTargetLabel(state.currentTarget);
    }

    function renderRangeList(target, ranges, single) {
      if (single) {
        if (!ranges) {
          target.innerHTML = '<span class="mini">未设置</span>';
          return;
        }
        target.innerHTML = `<span class="range-chip">${ranges.start} - ${ranges.end} <button class="ghost" onclick="clearSingleRange()">移除</button></span>`;
        return;
      }
      if (!ranges.length) {
        target.innerHTML = '<span class="mini">未设置</span>';
        return;
      }
      target.innerHTML = ranges.map((r, idx) =>
        `<span class="range-chip">${r.start} - ${r.end} <button class="ghost" onclick="removeRange('${target.id}', ${idx})">删除</button></span>`
      ).join("");
    }

    function renderKeywordList(targetId, items, type) {
      const target = el(targetId);
      if (!items.length) {
        target.innerHTML = '<span class="mini">未设置</span>';
        return;
      }
      target.innerHTML = items.map(item =>
        `<span class="keyword-chip">${escapeHtml(item)} <button class="ghost keyword-remove-btn" data-type="${escapeAttr(type)}" data-text="${escapeAttr(item)}">移除</button></span>`
      ).join("");
      target.querySelectorAll(".keyword-remove-btn").forEach(node => {
        node.addEventListener("click", () => {
          removeKeyword(node.dataset.type, node.dataset.text);
        });
      });
    }

    function syncAnnotationViews() {
      syncTargetBadge();
      renderRangeList(el("entryExitRangeBox"), state.annotation.entry_exit_range, true);
      renderRangeList(el("authorRangeBox"), state.annotation.author_ranges, false);
      renderRangeList(el("bodyRangeBox"), state.annotation.body_ranges, false);
      renderKeywordList("entryKeywordBox", state.annotation.entry_keywords || [], "entry");
      renderKeywordList("exitKeywordBox", state.annotation.exit_keywords || [], "exit");
      el("notes").value = state.annotation.notes || "";
      el("keywordNotes").value = state.annotation.keyword_notes || "";
      el("checkedBox").checked = !!state.annotation.checked;
      renderLines();
      renderKeywordGrid();
      updateDirtyState();
    }

    function renderLines() {
      const lines = state.current?.lines || [];
      const html = lines.map(item => {
        let cls = "line";
        if (state.annotation.entry_exit_range && item.line >= state.annotation.entry_exit_range.start && item.line <= state.annotation.entry_exit_range.end) {
          cls += " entry-exit";
        } else if (inRanges(item.line, state.annotation.author_ranges)) {
          cls += " author";
        } else if (inRanges(item.line, state.annotation.body_ranges)) {
          cls += " body";
        }
        if (state.draftClick.includes(item.line)) {
          cls += " current-pick";
        }
        return `<div class="${cls}" data-line="${item.line}">
          <div class="line-num">${item.line}</div>
          <div>${escapeHtml(item.text)}</div>
        </div>`;
      }).join("");
      el("lineList").innerHTML = html;
      document.querySelectorAll(".line").forEach(node => {
        node.addEventListener("click", (event) => {
          event.preventDefault();
          onLineLeftClick(Number(node.dataset.line), event);
        });
        node.addEventListener("contextmenu", (event) => {
          event.preventDefault();
          onLineRightClick(Number(node.dataset.line), event);
        });
      });
    }

    function renderPlan1(plan) {
      const entranceHtml = (plan.entrances || []).map(item => `<div class="scheme-item">${escapeHtml(item)}</div>`).join("") || '<div class="mini">无</div>';
      const exitHtml = (plan.exits || []).map(item => `<div class="scheme-item">${escapeHtml(item)}</div>`).join("") || '<div class="mini">无</div>';
      el("plan1Entrances").innerHTML = entranceHtml;
      el("plan1Exits").innerHTML = exitHtml;
      el("phrasePlan1Entrances").innerHTML = entranceHtml;
      el("phrasePlan1Exits").innerHTML = exitHtml;
    }

    function renderPlan2(plan) {
      const summary = plan.summary || {};
      el("plan2Summary").textContent = `入口 ${summary.entrance || 0} / 出口 ${summary.exit || 0} / 无关 ${summary.irrelevant || 0}`;
      el("plan2Edges").innerHTML = (plan.edges || []).map(item => `
        <div class="scheme-item">
          <div><strong>${escapeHtml(item.direction)}</strong> -> ${escapeHtml(item.target || "")} | 行 ${escapeHtml(item.position_line || "")}</div>
          <div class="mini">${escapeHtml(item.reason || "")}</div>
          <div>${escapeHtml(item.context || "")}</div>
        </div>
      `).join("") || '<div class="mini">无</div>';
    }

    function renderPlan3(plan) {
      const ranges = (plan.entry_exit_ranges || []).map(r => `${r[0]}-${r[1]}`).join(", ") || "无";
      const html = `
        <div class="scheme-item">state_code: ${escapeHtml(plan.state_code || "")}</div>
        <div class="scheme-item">四位编码: ${escapeHtml(plan.state_code_1 || "")} / ${escapeHtml(plan.state_code_2 || "")} / ${escapeHtml(plan.state_code_3 || "")} / ${escapeHtml(plan.state_code_4 || "")}</div>
        <div class="scheme-item">建议范围: ${escapeHtml(ranges)}</div>
      `;
      el("plan3State").innerHTML = html;
      el("phrasePlan3State").innerHTML = html;
    }

    function renderPhraseCandidates() {
      const items = state.current?.phrase_candidates || [];
      if (!items.length) {
        el("phraseGrid").innerHTML = '<div class="muted-box">没有可展示的词语候选。</div>';
        return;
      }
      el("phraseGrid").innerHTML = items.map(item => `
        <div class="candidate-card">
          <div>
            <div>${escapeHtml(item.text)}</div>
            <div class="mini">出现 ${item.count} 次</div>
          </div>
          <button class="ghost phrase-copy-btn" data-text="${escapeAttr(item.text)}">带到关键词页</button>
        </div>
      `).join("");
      document.querySelectorAll(".phrase-copy-btn").forEach(node => {
        node.addEventListener("click", () => {
          copyPhraseToKeyword(node.dataset.text);
        });
      });
    }

    function keywordClass(text) {
      const inEntry = (state.annotation.entry_keywords || []).includes(text);
      const inExit = (state.annotation.exit_keywords || []).includes(text);
      if (inEntry && inExit) return "both";
      if (inEntry) return "entry";
      if (inExit) return "exit";
      return "";
    }

    function renderKeywordGrid() {
      const items = state.current?.phrase_candidates || [];
      if (!items.length) {
        el("keywordGrid").innerHTML = '<div class="muted-box">没有可选择的词语候选。</div>';
        return;
      }
      el("keywordGrid").innerHTML = items.map(item => {
        const cls = keywordClass(item.text);
        return `<button class="keyword-btn ${cls}" data-text="${escapeAttr(item.text)}">
          <div>${escapeHtml(item.text)}</div>
          <div class="mini">出现 ${item.count} 次</div>
        </button>`;
      }).join("");
      document.querySelectorAll(".keyword-btn").forEach(node => {
        const text = node.dataset.text;
        node.addEventListener("click", (event) => {
          event.preventDefault();
          toggleKeyword("entry", text);
        });
        node.addEventListener("contextmenu", (event) => {
          event.preventDefault();
          toggleKeyword("exit", text);
        });
      });
    }

    function renderCurrent(data) {
      state.current = data;
      state.currentTarget = "body";
      state.annotation = clone(data.annotation || {
        entry_exit_range: null,
        author_ranges: [],
        body_ranges: [],
        notes: "",
        checked: false,
        entry_keywords: [],
        exit_keywords: [],
        keyword_notes: ""
      });
      state.draftClick = [];
      el("title").textContent = data.level_id || "层级人工校验台";
      el("meta").textContent = `${data.file_path || ""} | 共 ${data.line_count || 0} 行`;
      renderPlan1(data.plan1 || {});
      renderPlan2(data.plan2 || {});
      renderPlan3(data.plan3 || {});
      renderPhraseCandidates();
      syncAnnotationViews();
      markSaved();
    }

    function normalizeDraft() {
      const start = Number(el("rangeStart").value || 0);
      const end = Number(el("rangeEnd").value || 0);
      if (!start || !end) return null;
      return start <= end ? { start, end } : { start: end, end: start };
    }

    function targetFromEvent(event) {
      if (event?.altKey) return "author";
      if (event?.shiftKey) return "entry_exit";
      if (event?.ctrlKey) return "body";
      return null;
    }

    function applyTargetFromEvent(event) {
      const target = targetFromEvent(event);
      if (target) {
        state.currentTarget = target;
        syncTargetBadge();
      }
    }

    function onLineLeftClick(lineNo, event) {
      applyTargetFromEvent(event);
      state.draftClick = [lineNo, state.draftClick[1]].filter(v => v !== undefined);
      el("rangeStart").value = lineNo;
      renderLines();
    }

    function onLineRightClick(lineNo, event) {
      applyTargetFromEvent(event);
      const start = state.draftClick[0];
      state.draftClick = start !== undefined ? [start, lineNo] : [lineNo];
      el("rangeEnd").value = lineNo;
      renderLines();
    }

    function clearSingleRange() {
      state.annotation.entry_exit_range = null;
      syncAnnotationViews();
      markDirty();
    }

    function removeRange(boxId, idx) {
      if (boxId === "authorRangeBox") {
        state.annotation.author_ranges.splice(idx, 1);
      } else if (boxId === "bodyRangeBox") {
        state.annotation.body_ranges.splice(idx, 1);
      }
      syncAnnotationViews();
      markDirty();
    }

    function toggleKeyword(type, text) {
      const key = type === "entry" ? "entry_keywords" : "exit_keywords";
      const bucket = state.annotation[key] || [];
      const index = bucket.indexOf(text);
      if (index >= 0) {
        bucket.splice(index, 1);
      } else {
        bucket.push(text);
      }
      state.annotation[key] = bucket;
      syncAnnotationViews();
      markDirty();
    }

    function removeKeyword(type, text) {
      const key = type === "entry" ? "entry_keywords" : "exit_keywords";
      state.annotation[key] = (state.annotation[key] || []).filter(item => item !== text);
      syncAnnotationViews();
      markDirty();
    }

    function copyPhraseToKeyword(text) {
      state.activePage = "keyword";
      setActivePage("keyword", true);
      if (!(state.annotation.entry_keywords || []).includes(text) && !(state.annotation.exit_keywords || []).includes(text)) {
        state.annotation.entry_keywords.push(text);
      }
      syncAnnotationViews();
      markDirty();
    }

    async function saveAnnotation() {
      state.annotation.notes = el("notes").value;
      state.annotation.keyword_notes = el("keywordNotes").value;
      state.annotation.checked = el("checkedBox").checked;
      const result = await window.pywebview.api.save_annotation(state.current.level_id, state.annotation);
      if (result.ok) {
        state.annotation = clone(result.annotation);
        state.current.annotation = clone(result.annotation);
        syncAnnotationViews();
        markSaved();
      }
    }

    async function handlePendingChanges() {
      if (!state.dirty) {
        return true;
      }
      const shouldSave = window.confirm("当前页有未保存内容。选择“是”会先保存，选择“否”会直接丢弃。");
      if (shouldSave) {
        await saveAnnotation();
        return true;
      }
      state.annotation = clone(state.current.annotation || state.annotation);
      syncAnnotationViews();
      markSaved();
      return true;
    }

    async function loadLevel(levelId) {
      if (!await handlePendingChanges()) {
        return;
      }
      const data = await window.pywebview.api.load_level(levelId);
      renderCurrent(data);
      el("levelSelect").value = data.level_id || "";
    }

    function setActivePage(page, force) {
      if (!force && state.activePage === page) {
        return;
      }
      document.querySelectorAll(".page").forEach(node => {
        node.classList.toggle("active", node.id === `page-${page}`);
      });
      document.querySelectorAll(".tab-btn").forEach(node => {
        node.classList.toggle("active", node.dataset.page === page);
      });
      state.activePage = page;
    }

    window.clearSingleRange = clearSingleRange;
    window.removeRange = removeRange;
    window.copyPhraseToKeyword = copyPhraseToKeyword;

    el("applyRangeBtn").addEventListener("click", () => {
      const range = normalizeDraft();
      if (!range) return;
      const target = state.currentTarget;
      if (target === "entry_exit") {
        state.annotation.entry_exit_range = range;
      } else if (target === "author") {
        state.annotation.author_ranges.push(range);
      } else if (target === "body") {
        state.annotation.body_ranges.push(range);
      }
      state.draftClick = [];
      el("rangeStart").value = "";
      el("rangeEnd").value = "";
      syncAnnotationViews();
      markDirty();
    });

    el("clearDraftBtn").addEventListener("click", () => {
      state.draftClick = [];
      el("rangeStart").value = "";
      el("rangeEnd").value = "";
      renderLines();
    });

    el("notes").addEventListener("input", () => {
      state.annotation.notes = el("notes").value;
      markDirty();
    });

    el("keywordNotes").addEventListener("input", () => {
      state.annotation.keyword_notes = el("keywordNotes").value;
      markDirty();
    });

    el("checkedBox").addEventListener("change", () => {
      state.annotation.checked = el("checkedBox").checked;
      markDirty();
    });

    el("saveBtn").addEventListener("click", saveAnnotation);
    el("loadBtn").addEventListener("click", async () => loadLevel(el("levelSelect").value));
    el("randomBtn").addEventListener("click", async () => {
      if (!await handlePendingChanges()) {
        return;
      }
      const data = await window.pywebview.api.random_level(state.current?.level_id || "");
      renderCurrent(data);
      el("levelSelect").value = data.level_id || "";
    });

    document.querySelectorAll(".tab-btn").forEach(node => {
      node.addEventListener("click", async () => {
        const page = node.dataset.page;
        if (page === state.activePage) {
          return;
        }
        if (!await handlePendingChanges()) {
          return;
        }
        setActivePage(page, true);
      });
    });

    async function init() {
      const boot = await window.pywebview.api.bootstrap();
      state.bootstrap = boot;
      el("levelSelect").innerHTML = (boot.level_ids || []).map(id => `<option value="${escapeHtml(id)}">${escapeHtml(id)}</option>`).join("");
      if (boot.current && boot.current.level_id) {
        renderCurrent(boot.current);
        el("levelSelect").value = boot.current.level_id;
      }
    }

    window.addEventListener("pywebviewready", init);
  </script>
</body>
</html>
"""


def main() -> None:
    try:
        import webview  # type: ignore
    except ImportError:
        raise SystemExit(
            "pywebview 未安装。请先在虚拟环境中安装：.\\.venv\\Scripts\\python.exe -m pip install pywebview"
        )

    api = Api()
    window = webview.create_window(
        title="Entry Exit Review",
        html=HTML,
        js_api=api,
        width=1680,
        height=1020,
    )
    webview.start(debug=False)


if __name__ == "__main__":
    main()
