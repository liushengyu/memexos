
---

## 本次任务

- 当前目录是一个知识库的编译结果（git 仓库）。编译起点：`BASE = $base`
- 本次的 raw 来源（只读）：
$raws
- 被改动的 wiki 文件：
$changed

常用命令：`git diff $base -- wiki index.md`（本次改动）、`git show $base:<路径>`（改动前原文）、`cat`、`rg`。

按标准完成审校，严格按给定的 JSON schema 输出最终结果。
$fix_note
