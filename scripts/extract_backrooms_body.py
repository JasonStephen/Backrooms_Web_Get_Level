from __future__ import annotations

import copy
from pathlib import Path
from bs4 import BeautifulSoup, NavigableString, Tag


ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "samples"
OUTPUT_DIR = ROOT / "outputs"


UNWANTED_SELECTORS = [
    "script",
    "style",
    "iframe",
    ".iframe-container",
    ".sd-container",
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


def clean_text(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def make_tag(name: str, **attrs: str) -> Tag:
    return BeautifulSoup("", "html.parser").new_tag(name, **attrs)


def remove_unwanted_nodes(container: Tag) -> None:
    for selector in UNWANTED_SELECTORS:
        for node in container.select(selector):
            node.decompose()

    for node in container.find_all(attrs={"style": True}):
        if not isinstance(node, Tag):
            continue
        style_attr = node.attrs.get("style") if node.attrs else ""
        style = str(style_attr).replace(" ", "").lower()
        if "display:none" in style:
            node.decompose()
            continue

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
        for selector in [".collapsible-block-folded .collapsible-block-link", ".collapsible-block-unfolded-link .collapsible-block-link"]:
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

        labels = [
            clean_text(em.get_text(" ", strip=True))
            for em in tabview.select(".yui-nav li em")
        ]
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
    for tag in container.find_all(True):
        allowed_attrs = {}
        if tag.name == "a" and tag.get("href") and not tag.get("href", "").startswith("javascript:"):
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
        previous = hr.find_previous_sibling()
        next_node = hr.find_next_sibling()
        if previous is None or next_node is None:
            hr.decompose()


def extract_article(source_path: Path) -> BeautifulSoup:
    soup = BeautifulSoup(source_path.read_text(encoding="utf-8"), "html.parser")
    main = soup.select_one("#main-content")
    title = main.select_one("#page-title") if main else None
    content = main.select_one("#page-content") if main else None
    if title is None or content is None:
        raise ValueError(f"Could not locate article in {source_path}")

    content_clone = BeautifulSoup(str(content), "html.parser").select_one("#page-content")
    assert content_clone is not None

    remove_unwanted_nodes(content_clone)
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

        line = prefix + clean_text(" ".join(content_parts))
        lines.append(("  " * level) + line.rstrip())
        lines.extend(block for block in nested_blocks if block)

    return "\n".join(lines)


def render_table(node: Tag) -> str:
    rows = []
    for tr in node.find_all("tr", recursive=False):
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
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

    name = node.name
    if name is None:
        return ""

    if name == "article":
        parts = [render_markdown(child) for child in node.children]
        return "\n\n".join(part for part in parts if part.strip())

    if name == "h1":
        return f"# {clean_text(node.get_text(' ', strip=True))}"
    if name == "h2":
        return f"## {clean_text(node.get_text(' ', strip=True))}"
    if name == "h3":
        return f"### {clean_text(node.get_text(' ', strip=True))}"
    if name == "h4":
        return f"#### {clean_text(node.get_text(' ', strip=True))}"
    if name == "p":
        return inline_text(node)
    if name in {"section", "div"}:
        parts = [render_markdown(child) for child in node.children]
        return "\n\n".join(part for part in parts if part.strip())
    if name in {"ul", "ol"}:
        return render_list(node)
    if name == "blockquote":
        text = "\n".join(
            line for block in [render_markdown(child) for child in node.children] if block
            for line in block.splitlines()
        )
        return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())
    if name == "figure":
        parts = []
        img = node.find("img", recursive=False)
        if img and img.get("src"):
            alt = img.get("alt", "")
            parts.append(f"![{alt}]({img['src']})")
        caption = node.find("figcaption", recursive=False)
        if caption:
            parts.append(inline_text(caption))
        return "\n\n".join(parts)
    if name == "a":
        text = inline_text(node) or node.get("href", "")
        href = node.get("href", "")
        return f"[{text}]({href})" if href else text
    if name in {"strong", "b"}:
        return f"**{inline_text(node)}**"
    if name in {"em", "i"}:
        return f"*{inline_text(node)}*"
    if name == "sup":
        return f"^{inline_text(node)}^"
    if name == "sub":
        return f"~{inline_text(node)}~"
    if name == "br":
        return "\n"
    if name == "hr":
        return "---"
    if name == "table":
        return render_table(node)
    if name in {"figcaption", "span"}:
        return inline_text(node)

    return inline_text(node)


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    summary_lines = [
        "# Backrooms 正文提取规律",
        "",
        "- 正文主入口位于 `#main-content` 下，标题在 `#page-title`，正文在 `#page-content`。",
        "- `#page-content` 前部常混入 iframe、评分/作者信息、隐藏样式代码块，这些需要剔除。",
        "- 正文主体通常由标题、段落、列表、引用、图片、表格构成；折叠块和标签页里的内容应展开保留。",
        "- 正文尾部常以 `footnotes-footer`、`footer-wikiwalk-nav`、`licensebox` 作为结束标记，之后不属于正文。",
        "- 输出 HTML 仅保留语义标签，去掉 CSS、JS 和站点壳子；Markdown 由清洗后的 HTML 直接转换。",
        "",
    ]

    for source_path in sorted(SAMPLES_DIR.glob("sample_*.html")):
        article = extract_article(source_path)
        stem = source_path.stem
        html_path = OUTPUT_DIR / f"{stem}.body.html"
        md_path = OUTPUT_DIR / f"{stem}.body.md"
        html_path.write_text(str(article), encoding="utf-8")
        md_path.write_text(render_markdown(article.article).strip() + "\n", encoding="utf-8")
        summary_lines.append(f"- `{source_path.name}` -> `{html_path.name}` / `{md_path.name}`")

    (OUTPUT_DIR / "extraction_summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
