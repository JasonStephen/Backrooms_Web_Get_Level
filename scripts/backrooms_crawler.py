from __future__ import annotations

import argparse
import copy
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup, NavigableString, Tag


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "crawler_config.json"
SAMPLES_DIR = ROOT / "samples"
OUTPUT_DIR = ROOT / "outputs"

UNWANTED_SELECTORS = [
    "script",
    "style",
    "iframe",
    ".iframe-container",
    ".creditRate",
    ".page-rate-widget-box",
    ".rate-box-with-credit-button",
    ".fader",
    ".footnotes-footer",
    ".footer-wikiwalk-nav",
    ".licensebox",
    ".page-tags",
    "#page-options-container",
    "#page-info-break",
    "#action-area",
    "#action-area-top",
]

SURVIVAL_DEFAULTS = {
    "class-1": ["安全", "稳定", "极少量实体"],
    "class-pending": ["安全性未知", "不稳定", "未探明实体存在"],
}


@dataclass(frozen=True)
class CrawlTarget:
    logical_id: str
    path: str
    source: str


@dataclass
class ArticleCheckResult:
    has_article: bool
    reason: str


@dataclass
class CrawlResult:
    target: CrawlTarget
    status_code: int | None
    has_content: bool
    reason: str
    title: str = ""
    saved_html: str = ""
    saved_md: str = ""
    error: str = ""


def clean_text(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def make_tag(name: str, **attrs: str) -> Tag:
    return BeautifulSoup("", "html.parser").new_tag(name, **attrs)


def normalize_placeholder_text(text: str) -> str:
    text = clean_text(text)
    return "" if not text or text.startswith("{$") else text


def preferred_lang_text(node: Tag | None) -> str:
    if node is None:
        return ""

    for selector in [".lang-cn", ".lang-tr"]:
        match = node.select_one(selector)
        if match:
            return clean_text(match.get_text(" ", strip=True))

    return clean_text(node.get_text(" ", strip=True))


def convert_sd_containers(container: Tag) -> None:
    for sd in container.select(".sd-container"):
        section = make_tag("section")
        section["data-type"] = "survival-difficulty"

        heading = make_tag("h2")
        heading.string = preferred_lang_text(sd.select_one(".top-text")) or "生存难度"
        section.append(heading)

        bottom_text = sd.select_one(".bottom-text")
        if bottom_text:
            raw_bottom = clean_text(bottom_text.get_text(" ", strip=True))
            label = preferred_lang_text(bottom_text) or "等级"
            remainder = clean_text(raw_bottom.replace(label, "", 1))
            grade_text = f"{label} {remainder}".strip() if remainder else raw_bottom
            if grade_text:
                grade = make_tag("p")
                grade.string = grade_text
                section.append(grade)

        bullets: list[str] = []
        for li in sd.select(".bottom-box li"):
            text = normalize_placeholder_text(li.get_text(" ", strip=True).replace(".", " "))
            if text:
                bullets.append(text)

        if not bullets:
            top_box = sd.select_one(".top-box")
            class_source = top_box.get("class", []) if top_box else sd.get("class", [])
            class_name = next((cls for cls in class_source if cls.startswith("class-")), "")
            bullets = SURVIVAL_DEFAULTS.get(class_name, []).copy()

        if bullets:
            ul = make_tag("ul")
            for bullet in bullets:
                li = make_tag("li")
                li.string = bullet
                ul.append(li)
            section.append(ul)

        sd.replace_with(section)


def remove_unwanted_nodes(container: Tag) -> None:
    for selector in UNWANTED_SELECTORS:
        for node in container.select(selector):
            node.decompose()

    for node in container.find_all(attrs={"style": True}):
        style = str((node.attrs or {}).get("style", "")).replace(" ", "").lower()
        if "display:none" not in style:
            continue

        classes = set(node.get("class", []))
        if (
            "collapsible-block-unfolded" in classes
            or "collapsible-block-content" in classes
            or "collapsible-block-unfolded-link" in classes
            or node.get("id", "").startswith("wiki-tab-")
            or node.find_parent(class_="yui-navset") is not None
        ):
            continue

        node.decompose()

    for node in container.find_all(["div", "span", "p"]):
        if not node.attrs and not clean_text(node.get_text(" ", strip=True)) and not node.find(["img", "br", "hr"]):
            node.decompose()


def unwrap_noise(container: Tag) -> None:
    for selector in [".html-block-iframe", ".foldable-list-container"]:
        for node in container.select(selector):
            node.decompose()

    for node in container.find_all(["span", "div"]):
        classes = set(node.get("class", []))
        if classes & {
            "lang-cn",
            "lang-tr",
            "default",
            "diamondy",
            "bg",
            "gradient-box",
            "header-diamond",
            "top-text",
            "bottom-text",
        }:
            if node.name == "span" and node.parent and node.parent.name != "li":
                node.unwrap()


def flatten_collapsible_blocks(container: Tag) -> None:
    for block in container.select(".collapsible-block"):
        unfolded = block.select_one(".collapsible-block-content")
        if unfolded is None:
            block.decompose()
            continue

        section = make_tag("section")

        title = None
        for selector in [
            ".collapsible-block-folded .collapsible-block-link",
            ".collapsible-block-unfolded-link .collapsible-block-link",
        ]:
            link = block.select_one(selector)
            if link:
                title = clean_text(link.get_text(" ", strip=True))
                if title:
                    break

        if title:
            heading = make_tag("h2")
            heading.string = title
            section.append(heading)

        for child in list(unfolded.contents):
            section.append(copy.copy(child))

        block.replace_with(section)


def flatten_tabviews(container: Tag) -> None:
    for tabview in container.select(".yui-navset"):
        section = make_tag("section")
        labels = [clean_text(em.get_text(" ", strip=True)) for em in tabview.select(".yui-nav li em")]
        panels = tabview.select(".yui-content > div")

        for index, panel in enumerate(panels):
            title = labels[index] if index < len(labels) else f"Tab {index + 1}"
            heading = make_tag("h2")
            heading.string = title
            section.append(heading)
            for child in list(panel.contents):
                section.append(copy.copy(child))

        tabview.replace_with(section)


def simplify_media(container: Tag) -> None:
    for image_block in container.select(".scp-image-block"):
        figure = make_tag("figure")
        img = image_block.find("img")
        if img:
            new_img = make_tag("img", src=img.get("src", ""))
            if img.get("alt"):
                new_img["alt"] = img["alt"]
            figure.append(new_img)

        caption = image_block.select_one(".scp-image-caption")
        if caption:
            figcaption = make_tag("figcaption")
            for child in list(caption.contents):
                figcaption.append(copy.copy(child))
            figure.append(figcaption)

        image_block.replace_with(figure)


def normalize_structure(container: Tag) -> None:
    for sup in container.find_all("sup"):
        sup.decompose()

    for tag in container.find_all(True):
        allowed_attrs: dict[str, str] = {}
        if tag.name == "a" and tag.get("href") and not tag["href"].startswith("javascript:"):
            allowed_attrs["href"] = tag["href"]
        if tag.name == "img":
            if tag.get("src"):
                allowed_attrs["src"] = tag["src"]
            if tag.get("alt"):
                allowed_attrs["alt"] = tag["alt"]
        tag.attrs = allowed_attrs

    for tag in container.find_all(["span", "div"]):
        if not tag.attrs and tag.name != "div":
            tag.unwrap()

    for div in list(container.find_all("div")):
        if div.find(["p", "h1", "h2", "h3", "h4", "ul", "ol", "blockquote", "figure", "table", "hr", "section"]):
            div.name = "section"
        elif not clean_text(div.get_text(" ", strip=True)):
            div.decompose()
        else:
            div.unwrap()

    for hr in list(container.find_all("hr")):
        if hr.find_previous_sibling() is None or hr.find_next_sibling() is None:
            hr.decompose()


def detect_article_state(soup: BeautifulSoup) -> ArticleCheckResult:
    main = soup.select_one("#main-content")
    content = main.select_one("#page-content") if main else None
    if content is None:
        return ArticleCheckResult(False, "missing-page-content")

    content_text = clean_text(content.get_text(" ", strip=True))
    if "页面不存在" in content_text or "Not found" in content_text or "type=404" in str(content):
        return ArticleCheckResult(False, "404-page")

    if not content.find(["p", "h1", "h2", "h3", "h4", "ul", "ol", "blockquote", "table", "img", "figure", "section", "div"]):
        return ArticleCheckResult(False, "empty-body")

    return ArticleCheckResult(True, "article")


def extract_article_from_soup(soup: BeautifulSoup) -> BeautifulSoup:
    state = detect_article_state(soup)
    if not state.has_article:
        raise ValueError(f"No article body: {state.reason}")

    main = soup.select_one("#main-content")
    title = main.select_one("#page-title") if main else None
    content = main.select_one("#page-content") if main else None
    if title is None or content is None:
        raise ValueError("Could not locate article title/content")

    content_clone = BeautifulSoup(str(content), "html.parser").select_one("#page-content")
    if content_clone is None:
        raise ValueError("Could not clone page content")

    remove_unwanted_nodes(content_clone)
    convert_sd_containers(content_clone)
    flatten_collapsible_blocks(content_clone)
    flatten_tabviews(content_clone)
    simplify_media(content_clone)
    unwrap_noise(content_clone)
    normalize_structure(content_clone)

    article_soup = BeautifulSoup("", "html.parser")
    article = article_soup.new_tag("article")
    heading = article_soup.new_tag("h1")
    heading.string = clean_text(title.get_text(" ", strip=True))
    article.append(heading)

    for child in list(content_clone.contents):
        article.append(copy.copy(child))

    article_soup.append(article)
    return article_soup


def inline_text(node: Tag) -> str:
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
            continue

        text = render_markdown(child, inline=True)
        if text:
            parts.append(text)

    return clean_text("".join(parts))


def render_list(node: Tag, level: int = 0) -> str:
    lines: list[str] = []
    ordered = node.name == "ol"

    for index, item in enumerate(node.find_all("li", recursive=False), start=1):
        prefix = f"{index}. " if ordered else "- "
        content_parts: list[str] = []
        nested_blocks: list[str] = []

        for child in item.children:
            if isinstance(child, NavigableString):
                text = clean_text(str(child))
                if text:
                    content_parts.append(text)
                continue

            if child.name in {"ul", "ol"}:
                nested_blocks.append(render_list(child, level + 1))
            else:
                text = render_markdown(child, inline=True)
                if text:
                    content_parts.append(text)

        lines.append(("  " * level) + prefix + clean_text(" ".join(content_parts)).rstrip())
        lines.extend(block for block in nested_blocks if block)

    return "\n".join(lines)


def render_table(node: Tag) -> str:
    rows: list[list[str]] = []
    for tr in node.find_all("tr", recursive=False):
        cells = tr.find_all(["th", "td"], recursive=False)
        if cells:
            rows.append([inline_text(cell) for cell in cells])

    if not rows:
        return ""

    header = rows[0]
    body = rows[1:] or [[]]
    header_line = "| " + " | ".join(header) + " |"
    separator_line = "| " + " | ".join(["---"] * len(header)) + " |"
    body_lines = ["| " + " | ".join(row) + " |" for row in body if row]
    return "\n".join([header_line, separator_line, *body_lines])


def render_markdown(node: Tag, inline: bool = False) -> str:
    if isinstance(node, NavigableString):
        return clean_text(str(node))

    if node.name is None:
        return ""

    if node.name == "article":
        parts = [render_markdown(child) for child in node.children]
        return "\n\n".join(part for part in parts if part.strip())
    if node.name == "h1":
        return f"# {clean_text(node.get_text(' ', strip=True))}"
    if node.name == "h2":
        return f"## {clean_text(node.get_text(' ', strip=True))}"
    if node.name == "h3":
        return f"### {clean_text(node.get_text(' ', strip=True))}"
    if node.name == "h4":
        return f"#### {clean_text(node.get_text(' ', strip=True))}"
    if node.name == "p":
        return inline_text(node)
    if node.name in {"section", "div"}:
        parts = [render_markdown(child) for child in node.children]
        return "\n\n".join(part for part in parts if part.strip())
    if node.name in {"ul", "ol"}:
        return render_list(node)
    if node.name == "blockquote":
        text = "\n".join(
            line
            for block in [render_markdown(child) for child in node.children]
            if block
            for line in block.splitlines()
        )
        return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())
    if node.name == "figure":
        parts: list[str] = []
        img = node.find("img", recursive=False)
        if img and img.get("src"):
            parts.append(f"![{img.get('alt', '')}]({img['src']})")
        caption = node.find("figcaption", recursive=False)
        if caption:
            parts.append(inline_text(caption))
        return "\n\n".join(parts)
    if node.name == "a":
        text = inline_text(node) or node.get("href", "")
        href = node.get("href", "")
        return f"[{text}]({href})" if href else text
    if node.name in {"strong", "b"}:
        return f"**{inline_text(node)}**"
    if node.name in {"em", "i"}:
        return f"*{inline_text(node)}*"
    if node.name == "sup":
        return ""
    if node.name == "sub":
        return f"~{inline_text(node)}~"
    if node.name == "br":
        return "\n"
    if node.name == "hr":
        return "---"
    if node.name == "table":
        return render_table(node)
    if node.name in {"figcaption", "span"}:
        return inline_text(node)

    return inline_text(node)


def slugify(value: str) -> str:
    return value.replace("/", "_").replace(":", "_")


def build_targets(config: dict[str, Any], mode: str, count: int | None, seed: int | None) -> list[CrawlTarget]:
    preferred_paths = config.get("preferred_paths", {})
    unique: dict[str, CrawlTarget] = {}

    if mode == "test":
        for path in sorted(SAMPLES_DIR.glob(config.get("test_samples_glob", "sample_*.html"))):
            unique[path.stem] = CrawlTarget(path.stem, str(path), "sample")
        return list(unique.values())

    numeric = config["numeric_range"]
    for number in range(numeric["start"], numeric["end"] + 1):
        logical_id = f"level-{number}"
        path = preferred_paths.get(logical_id, logical_id)
        unique[logical_id] = CrawlTarget(logical_id, path, "numeric")

    for extra_path in config.get("whitelist_extra_paths", []):
        path = preferred_paths.get(extra_path, extra_path)
        unique[extra_path] = CrawlTarget(extra_path, path, "whitelist")

    targets = list(unique.values())

    if mode == "complete":
        if not config.get("complete_mode_enabled", False):
            raise SystemExit("Complete mode is currently disabled in crawler_config.json.")
        return targets

    if mode == "random":
        sample_size = count
        if sample_size is None:
            default_count = int(config.get("random_default_count", 10))
            raw_value = input(f"Random count (default {default_count}): ").strip()
            sample_size = int(raw_value) if raw_value else default_count

        if sample_size < 1:
            raise SystemExit("Random count must be at least 1.")
        if sample_size > len(targets):
            raise SystemExit(f"Random count {sample_size} exceeds target pool size {len(targets)}.")

        rng = random.Random(seed)
        return rng.sample(targets, sample_size)

    raise SystemExit(f"Unsupported mode: {mode}")


def fetch_web_target(target: CrawlTarget, config: dict[str, Any]) -> tuple[int | None, BeautifulSoup | None, str]:
    url = f"{config['base_url']}/{target.path}"
    timeout = int(config.get("request_timeout_seconds", 20))

    try:
        response = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        return None, None, str(exc)

    if response.status_code == 404:
        return 404, None, ""

    return response.status_code, BeautifulSoup(response.text, "html.parser"), ""


def save_article_outputs(article: BeautifulSoup, mode: str, logical_id: str) -> tuple[str, str]:
    mode_dir = OUTPUT_DIR / mode
    mode_dir.mkdir(parents=True, exist_ok=True)

    slug = slugify(logical_id)
    html_path = mode_dir / f"{slug}.body.html"
    md_path = mode_dir / f"{slug}.body.md"

    html_path.write_text(str(article), encoding="utf-8")
    md_path.write_text(render_markdown(article.article).strip() + "\n", encoding="utf-8")
    return str(html_path.relative_to(ROOT)), str(md_path.relative_to(ROOT))


def process_sample_target(target: CrawlTarget, mode: str) -> CrawlResult:
    source_path = Path(target.path)
    soup = BeautifulSoup(source_path.read_text(encoding="utf-8"), "html.parser")
    state = detect_article_state(soup)
    title = ""

    main = soup.select_one("#main-content")
    page_title = main.select_one("#page-title") if main else None
    if page_title is not None:
        title = clean_text(page_title.get_text(" ", strip=True))

    if not state.has_article:
        return CrawlResult(target, None, False, state.reason, title=title)

    article = extract_article_from_soup(soup)
    saved_html, saved_md = save_article_outputs(article, mode, target.logical_id)
    return CrawlResult(target, None, True, state.reason, title=title, saved_html=saved_html, saved_md=saved_md)


def process_web_target(target: CrawlTarget, config: dict[str, Any], mode: str) -> CrawlResult:
    status_code, soup, error = fetch_web_target(target, config)
    if error:
        return CrawlResult(target, status_code, False, "request-error", error=error)
    if status_code == 404:
        return CrawlResult(target, 404, False, "http-404")
    if soup is None:
        return CrawlResult(target, status_code, False, "missing-response-body")

    state = detect_article_state(soup)
    main = soup.select_one("#main-content")
    page_title = main.select_one("#page-title") if main else None
    title = clean_text(page_title.get_text(" ", strip=True)) if page_title is not None else ""

    if not state.has_article:
        return CrawlResult(target, status_code, False, state.reason, title=title)

    article = extract_article_from_soup(soup)
    saved_html, saved_md = save_article_outputs(article, mode, target.logical_id)
    return CrawlResult(target, status_code, True, state.reason, title=title, saved_html=saved_html, saved_md=saved_md)


def process_targets(targets: list[CrawlTarget], config: dict[str, Any], mode: str) -> list[CrawlResult]:
    results: list[CrawlResult] = []
    for target in targets:
        if mode == "test":
            results.append(process_sample_target(target, mode))
        else:
            results.append(process_web_target(target, config, mode))
    return results


def write_report(results: list[CrawlResult], mode: str) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    report_path = OUTPUT_DIR / f"{mode}_results.txt"

    lines = [f"Backrooms crawl results ({mode})", ""]
    available = [result.target.logical_id for result in results if result.has_content]

    for result in results:
        lines.append(result.target.logical_id)
        lines.append(f"Source: {result.target.source}")
        lines.append(f"Path: {result.target.path}")
        if result.status_code is not None:
            lines.append(f"HTTP Status: {result.status_code}")
        lines.append(f"Has Content: {'yes' if result.has_content else 'no'}")
        lines.append(f"Reason: {result.reason}")
        if result.title:
            lines.append(f"Title: {result.title}")
        if result.saved_html:
            lines.append(f"HTML: {result.saved_html}")
        if result.saved_md:
            lines.append(f"Markdown: {result.saved_md}")
        if result.error:
            lines.append(f"Error: {result.error}")
        lines.append("")

    lines.append("Pages with content:")
    if available:
        lines.extend(available)
    else:
        lines.append("none")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backrooms wiki crawler with complete, random, and test modes.")
    parser.add_argument("--mode", choices=["complete", "random", "test"], required=True)
    parser.add_argument("--count", type=int, help="Random mode only: number of targets to sample.")
    parser.add_argument("--seed", type=int, help="Optional random seed for reproducible random mode runs.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    targets = build_targets(config, args.mode, args.count, args.seed)
    results = process_targets(targets, config, args.mode)
    report_path = write_report(results, args.mode)

    print(f"Mode: {args.mode}")
    print(f"Targets processed: {len(results)}")
    print(f"Pages with content: {sum(result.has_content for result in results)}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
