# MemexOS

**给你和你的 Agent 用的记忆系统。** MemexOS 维护一个由 Agent 写、也由 Agent 读的个人知识库：资料收进来，编写 Agent
把它编译成互相链接的 wiki；发布之前，另一个模型对照来源逐句审校；你用的任何 Agent 都能检索它。

> 状态：早期开发中（0.1.0.dev0），接口还可能变。

[English](README.md)

## 为什么做这个

Agent 很会读、也很会写，但一次会话里学到的东西到下一次就没了，你上个月读过的东西也会忘。知识库能解决这个问题，
前提是不靠你手工整理它也一直可信。MemexOS 就是围绕这一点设计的：

- **没审过的内容不发布。** 每次编译都在一份暂存副本里进行，先过程序检查（门 1）、自查，再由第二个模型逐句审校
  （门 2），通过了才合并。审校模型暂时用不了时，编译就等着。
- **每句话都能追到来源。** 原始资料只增不改；每个页面都列出它依据的资料；按证据强度措辞（“据第三方转述……”、
  “作者认为……”）。
- **你只做决定。** Agent 定不下来的事进收件箱，附带选项和建议；查资料、核实、更正由 Agent 去办，同样要过检查。
- **任何 Agent 都能用。** 一套命令行（`memex search`、`memex get`、`memex help`），Claude Code、Codex 或任何能跑
  命令的 Agent 都能用；附带引用规则，以及“库里没有就直说”的规则。
- **看得见它在干什么。** 本机控制台展示知识图谱、流水线每一步、收件箱、Agent 怎么用知识库，以及花了多少钱。

思路来自 Andrej Karpathy 的“LLM wiki”模式（由语言模型根据你的资料维护一个 wiki）；名字取自 Vannevar Bush 1945 年
设想的 *memex*：一张能存下并链接主人读过的一切的书桌。

## 安装

需要 Python 3.11+、git，以及至少一个模型后端：已登录的 [Claude Code](https://github.com/anthropics/claude-code)
或 [Codex CLI](https://github.com/openai/codex)，或者 Anthropic API 密钥（`pip install "memexos[anthropic]"`）。

在本仓库的克隆里：

```sh
uv tool install .          # 或：pipx install .
memex doctor               # 检查哪些可用、缺什么、怎么补
```

可选：[qmd](https://github.com/tobi/qmd)（向量检索）、`yt-dlp` + `ffmpeg` + whisper.cpp（视频）、`pdftotext`
（poppler，读 PDF）。`memex doctor` 会逐项检查。

## 快速开始

```sh
memex init --sample                    # 在 ~/memex 建库，带三份已编译好的示例资料（英文）
memex search "how often should I review flashcards"
memex get "Spaced repetition"
memex console                          # 控制台：http://127.0.0.1:8765
memex eval retrieval l1                # 检索能不能找到对的页面（本机运行，不花钱）
```

加入你自己的资料并编译（会调用你配置的模型）：

```sh
memex add-url https://example.com/some-article
memex ingest                           # 编写、检查、审校、发布
memex status                           # 看发生了什么
```

无人值守运行：

```sh
memex schedule install                 # 每小时编译，每晚采集和维护，控制台常驻
```

不带 `--sample` 的 `memex init` 建一个空库。`memex init --lang zh` 让库的规则、提示词、日志和控制台都用中文。

## 让你的 Agent 用上它

在 Agent 的全局指令里加一小段（例如 `~/.claude/CLAUDE.md` 或 `~/.codex/AGENTS.md`）：

```md
## 知识库
我有一个个人知识库。问题涉及我读过、做过或决定过的事情时，先运行一次 `memex help` 并照做：用 `memex search` 查，
说明用到了哪些页面，库里没有就直说。
```

`memex help` 用一屏讲清规则，`memex <命令> --help` 有细节。

## 命令

| | |
|---|---|
| 读 | `search`、`get`、`catalog`、`help` |
| 加资料 | `add-url`、`add-video`、`feedback` |
| 编译 | `ingest`、`fix`、`correct`、`lint`、`pause`、`resume` |
| 采集 | `intake`、`sessions`、`cookies`（插件） |
| 做决定 | `inbox`、`console` |
| 观察 | `status`、`usage`、`eval` |
| 设置 | `init`、`doctor`、`config`、`secret`、`schedule` |

## 配置

设置分层：内置默认值 < 库里的 `.memex/config.toml` < `~/.config/memexos/config.toml` < 环境变量
（`MEMEX_<分组>__<键>`）。每一项都在 [`default_config.toml`](src/memexos/default_config.toml) 里有说明。
API 密钥从环境变量或系统钥匙串读取（`memex secret set ANTHROPIC_API_KEY`）。

## 哪些东西会离开你的电脑

- 发给你配置的模型后端（Claude Code、Codex、Anthropic API）的文本，和这些工具平时一样。
- 可选服务，只有你开启时才用：抓网页用的阅读服务、在线的判断模型。
- 除此之外没有。控制台只监听 127.0.0.1。需要你登录状态的采集源（X、B 站）是默认关闭的插件。

## 文档

- [架构](docs/architecture.md)：库的结构、流水线、两道检查、模型和定时任务
- [测评](docs/evals.md)：检索质量和门 2 校准
- [控制台](docs/console.md)：数据来源、安全和设计

（文档目前是英文。）

## 许可证

MIT。控制台自带的字体使用 SIL Open Font License 1.1（见 `src/memexos/console/static/fonts/OFL.txt`）。
