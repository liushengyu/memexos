你是这个知识库的维护者。先完整读 AGENTS.md，然后按其中 §2.1「自动模式」编译下面这些 raw 文件（一次处理完，不要等人确认，没有人在线）：

$files

这些来源的分类（程序提供，标准见 .memex/taxonomy.md）。按 AGENTS.md §2.1.4 写进来源页 frontmatter、在 index 里登记到对应领域下，引用时按证据强度措辞：
$classifications

硬约束：
1. 绝不修改或删除 raw/ 下的任何文件。
2. 只在 wiki/、index.md、log.md、inbox/ 这几个位置写文件。
3. 需要人判断的事（与已有内容的矛盾、拿不准的归类、⚠️ 待核实的关键断言、建议的 schema 调整）不要自己拍板，汇总写进 inbox/$date-ingest.md，没有这类事就不要创建这个文件。
4. 每个文件都要在 log.md 追加一条 ingest 记录。

这几种情况程序会自己处理，照下面做，不用写进 inbox：
- 写着“（还没有分类）”的来源：按 .memex/taxonomy.md 暂填 frontmatter（分类程序之后会补上并同步）。
- 转写稿 frontmatter 里有 coverage（只拿到一部分）或 asr_gaps（某些时段没转出内容）：来源页写明覆盖到哪里，不要当成系统问题报。

完成后只输出一行摘要：处理了哪些文件、新建/更新了哪些页、inbox 有没有新事项。
