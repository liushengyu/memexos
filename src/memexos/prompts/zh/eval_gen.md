$spec

---

## 你的任务：为这个知识库出检索测评题

当前目录是知识库：`index.md` 是目录，`wiki/` 是编译好的页面，`raw/` 是原始来源，`.memex/catalog.jsonl` 是来源分类。
请出 $n 道题，配额：$quota。

- 先读 `index.md` 了解全貌，再打开具体页面出题。**每道 answerable 题的 gold_pages 必须是你亲自打开读过、确实包含答案的页面**；
  key_facts 每条都能在 gold 页面里找到。
- 题目分散到不同领域和不同类型的页面（概念、实体、来源页、主题），不要集中在几页。
- `usage` 题考的是会不会看来源分类（`.memex/catalog.jsonl` 和来源页 frontmatter 里的证据强度、领域、时效），use_rule 写清正确做法。
- `negative` 题：看起来相关、但库里确实没有答案（在 `wiki/` 和 `raw/` 里换几种说法搜过确认）；gold_pages 可以写最容易被误认为有答案的页面，answerable=false。
- `real` 题：从 `$mined`（用户在过去会话里真实问过的问题，每行一个 JSON）里挑出和这个知识库内容相关的，去掉上下文依赖；
  库里有答案的标 answerable=true 并给金标准，库里没有但用户很可能来查的标 false。

来源的领域分布（份数）：
$domains

只读，不要改任何文件。严格按 JSON schema 输出 {"items": [...]}。
