import json, re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MD_DIR = ROOT / "outputs" / "md"
P1_DIR = ROOT / "outputs" / "entry_exit_1"
KW_PATH = ROOT / "config" / "kw.json"
OUT_PATH = ROOT / "config" / "kw_suggestions.json"
RPT_PATH = ROOT / "config" / "kw_analysis_report.txt"

with open(KW_PATH, "r", encoding="utf-8") as f:
    kw = json.load(f)

ENT_ALIAS = set(kw["entrance_aliases"])
EXIT_ALIAS = set(kw["exit_aliases"])
COMB_ALIAS = set(kw["combined_aliases"])
ENT_KW = set(kw["plan1"]["entrance_keywords"])
EXIT_KW = set(kw["plan1"]["exit_keywords"])
ENT_VERBS = set(kw["plan2"]["entrance_verbs"])
EXIT_VERBS = set(kw["plan2"]["exit_verbs"])
ALL_ALIAS = ENT_ALIAS | EXIT_ALIAS | COMB_ALIAS
ALL_VERB = ENT_KW | EXIT_KW | ENT_VERBS | EXIT_VERBS

rpt = []
def p(line=""): rpt.append(line + "\n")

# ── A1: Heading variants ──
p("=" * 60)
p("A1: Uncovered heading variants")
p("=" * 60)

HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.MULTILINE)
EE_RE = re.compile(r"(?:入|出)口|进入|离开|逃离|逃生|逃脱|撤离|通行|切入|切出|返回|抵达")

def norm(text):
    t = re.sub(r"[*_`~#>]+", "", text)
    t = re.sub(r"[\s:：;；,，.。!！?？()\[\]{}\-_/\\|«»]+", "", t)
    return t.replace("和", "与").replace("＆", "&")

heading_variants = Counter()
heading_sources = {}
for md in sorted(MD_DIR.glob("*.body.md")):
    text = md.read_text(encoding="utf-8")
    lines = text.splitlines()
    fm = 0
    if lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                fm = i + 1; break
    body = "\n".join(lines[fm:])
    auth = re.search(r"^#{1,4}\s*(?:作者|授权|脚注|来源|图源|译者|翻译|版权).*$", body, re.MULTILINE)
    if auth: body = body[:auth.start()]
    for m in HEADING_RE.finditer(body):
        raw = m.group(2).strip()
        n = norm(raw)
        if EE_RE.search(n) and n not in ALL_ALIAS:
            heading_variants[n] += 1
            if n not in heading_sources: heading_sources[n] = set()
            heading_sources[n].add(md.name.replace(".body.md", ""))

p(f"Total uncovered: {len(heading_variants)}")
for v, c in heading_variants.most_common(50):
    srcs = sorted(heading_sources[v])[:5]
    p(f"  [{c:3d}] {v}  (sources: {', '.join(srcs)})")

# ── A2: Verb/phrase extraction near level references in leak + none files ──
p("\n" + "=" * 60)
p("A2: Directional phrases near level refs (leak & none files)")
p("=" * 60)

with open(P1_DIR / "entry_exit_descriptions.json", "r", encoding="utf-8") as f:
    p1 = json.load(f)

target_files = set()
for r in p1["results"]:
    if r.get("ent_status") in ("漏网", "确认无") or r.get("exit_status") in ("漏网", "确认无"):
        target_files.add(r["logical_id"])
p(f"Files to scan: {len(target_files)}")

# Extract sentences containing level references + directional words
SENTENCE_RE = re.compile(r"[^。！？\n]{10,200}")
LEVEL_RE = re.compile(r"(?:level-\d+(?:-\d+)*|[Ll]evel\s*\d+(?:\.\d+)*)", re.IGNORECASE)
DIR_CUE = re.compile(r"(从|经由|通过|穿过|进入|离开|回到|返回|通往|通向|到达|抵达|来到|切出|切入|转移|前往|去往|去到|来到|走到|走出|出去|出去到|逃出|逃到|撤离到|返回去|回去|原路返回)")

auth_re = re.compile(r"^#{1,4}\s*(?:作者|授权|脚注|来源|图源|译者).*$", re.MULTILINE)

ent_phrases = Counter()
exit_phrases = Counter()
# Also extract raw sentences for context
examples = []

for lid in sorted(target_files):
    f = MD_DIR / f"{lid}.body.md"
    if not f.exists(): continue
    text = f.read_text(encoding="utf-8")
    lines = text.splitlines()
    fm = 0
    if lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                fm = i + 1; break
    body = "\n".join(lines[fm:])
    auth = auth_re.search(body)
    if auth: body = body[:auth.start()]
    
    for sent_m in SENTENCE_RE.finditer(body):
        sent = sent_m.group(0)
        if not LEVEL_RE.search(sent): continue
        if not DIR_CUE.search(sent): continue
        
        # Extract the directional phrase
        for dm in DIR_CUE.finditer(sent):
            cue = dm.group(1)
            # Get 2 chars before + cue + 6 chars after
            start = max(0, dm.start() - 2)
            end = min(len(sent), dm.end() + 8)
            phrase = sent[start:end].strip()
            phrase = re.sub(r"\s+", "", phrase)
            
            if any(w in phrase for w in ("进入", "抵达", "来到", "从", "经由", "通过", "穿过", "前往", "去往", "去到", "走到")):
                ent_phrases[phrase] += 1
            elif any(w in phrase for w in ("离开", "回到", "返回", "逃出", "逃到", "撤离到", "出去", "回去", "走出", "原路返回")):
                exit_phrases[phrase] += 1

p(f"\nEntrance-like phrases: {len(ent_phrases)}")
for v, c in ent_phrases.most_common(30):
    uncovered = v not in ALL_VERB and all(v not in x for x in ALL_VERB)
    tag = " [NEW]" if uncovered else ""
    p(f"  [{c:3d}] {v}{tag}")

p(f"\nExit-like phrases: {len(exit_phrases)}")
for v, c in exit_phrases.most_common(30):
    uncovered = v not in ALL_VERB and all(v not in x for x in ALL_VERB)
    tag = " [NEW]" if uncovered else ""
    p(f"  [{c:3d}] {v}{tag}")

# ── B2: Direction flips (improved) ──
p("\n" + "=" * 60)
p("B2: Top direction disagreements (plan1 vs plan2)")
p("=" * 60)
with open(P1_DIR / "entry_exit_level_ids.json", "r", encoding="utf-8") as f:
    p1_ids = json.load(f)
with open(ROOT / "outputs" / "entry_exit_2" / "level_summary_clean.json", "r", encoding="utf-8") as f:
    p2_clean = json.load(f)
p1m = {r["logical_id"]: r for r in p1_ids["results"]}
p2m = {r["source"]: r for r in p2_clean["records"]}

flip_ent = Counter()  # p1=ent p2=exit
flip_exit = Counter()  # p1=exit p2=ent
for src in sorted(set(p1m) & set(p2m)):
    p1e = set(p1m[src]["entrance_level_ids"])
    p1x = set(p1m[src]["exit_level_ids"])
    p2e = set(p2m[src]["entrances"])
    p2x = set(p2m[src]["exits"])
    for t in p1e & p2x: flip_ent[f"{src}->{t}"] += 1
    for t in p1x & p2e: flip_exit[f"{src}->{t}"] += 1

p(f"\np1=ent p2=exit: {len(flip_ent)} edges")
for k, c in flip_ent.most_common(30):
    p(f"  [{c:2d}] {k}")

p(f"\np1=exit p2=ent: {len(flip_exit)} edges")
for k, c in flip_exit.most_common(30):
    p(f"  [{c:2d}] {k}")

# ── Build suggestions ──
suggestions = {
    "add_entrance_aliases": [],
    "add_exit_aliases": [],
    "add_combined_aliases": [],
    "add_entrance_verbs_or_keywords": [],
    "add_exit_verbs_or_keywords": [],
    "direction_flip_examples": list(flip_ent.most_common(15)),
    "ambiguity_notes": [],
}

for v, c in heading_variants.most_common(30):
    if c < 1: continue
    if "入口" in v and "出口" not in v:
        suggestions["add_entrance_aliases"].append({"value": v, "count": c, "sources": list(heading_sources.get(v, []))[:3]})
    elif "出口" in v and "入口" not in v:
        suggestions["add_exit_aliases"].append({"value": v, "count": c, "sources": list(heading_sources.get(v, []))[:3]})
    elif "入口" in v or "出口" in v:
        suggestions["add_combined_aliases"].append({"value": v, "count": c, "sources": list(heading_sources.get(v, []))[:3]})

for v, c in ent_phrases.most_common(20):
    if v not in ALL_VERB and all(v not in x for x in ALL_VERB) and c >= 2:
        suggestions["add_entrance_verbs_or_keywords"].append({"value": v, "count": c})
for v, c in exit_phrases.most_common(20):
    if v not in ALL_VERB and all(v not in x for x in ALL_VERB) and c >= 2:
        suggestions["add_exit_verbs_or_keywords"].append({"value": v, "count": c})

suggestions["ambiguity_notes"].append({
    "word": "进入",
    "issue": "在 plan2 EXIT_VERBS 中（'进入Level X'=出口），但 '进入方法'=入口。方向标反 609 条边的首要根因。建议从 EXIT_VERBS 移除，改为按宾语判断。",
})

OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUT_PATH.write_text(json.dumps(suggestions, ensure_ascii=False, indent=2), encoding="utf-8")
RPT_PATH.write_text("".join(rpt), encoding="utf-8")

print(f"Done. {len(heading_variants)} headings, {len(ent_phrases)}+{len(exit_phrases)} phrases found.")
print(f"Report: {RPT_PATH}")
print(f"Suggestions: {OUT_PATH}")
