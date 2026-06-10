# Backrooms 正文提取规律

- 正文主入口位于 `#main-content` 下，标题在 `#page-title`，正文在 `#page-content`。
- `#page-content` 前部常混入 iframe、评分/作者信息、隐藏样式代码块，这些需要剔除。
- 正文主体通常由标题、段落、列表、引用、图片、表格构成；折叠块和标签页里的内容应展开保留。
- 正文尾部常以 `footnotes-footer`、`footer-wikiwalk-nav`、`licensebox` 作为结束标记，之后不属于正文。
- 输出 HTML 仅保留语义标签，去掉 CSS、JS 和站点壳子；Markdown 由清洗后的 HTML 直接转换。

- `sample_0.html` -> `sample_0.body.html` / `sample_0.body.md`
- `sample_1.html` -> `sample_1.body.html` / `sample_1.body.md`
- `sample_2.html` -> `sample_2.body.html` / `sample_2.body.md`
- `sample_3.html` -> `sample_3.body.html` / `sample_3.body.md`
