"""What the demo knowledge base is about: per domain, a topic, concepts, entities and the sources they came from.

Each line is "Title — one-sentence description". Sources are "type | platform | title — description"; their type
decides the raw file name (web, video, tweet, podcast, session, doc). Everything here is general knowledge written
for the demo; make_demo.py turns it into linked pages.
"""

DOMAINS = {
    "AI/Agents and tooling": {
        "topic": "Building reliable AI agents — how agents plan, use tools, keep memory and get evaluated",
        "concepts": """
Agent loop — a model repeatedly chooses an action, runs a tool and reads the result until the goal is met
Tool calling — the model emits a structured request that the host program executes and answers
Context engineering — choosing what goes into the model's limited context window at each step
Context window — the maximum amount of text a model can attend to in one request
Retrieval-augmented generation — fetching relevant documents at question time and putting them in the prompt
Agent memory — state an agent keeps across steps or sessions, from scratchpads to knowledge bases
Model Context Protocol — an open protocol for connecting models to tools and data sources
Sub-agent — a separate agent instance started for one sub-task with its own fresh context
Prompt injection — instructions hidden in data that try to take over an agent
Sandboxing — limiting which files, commands and network an agent may touch
Agent evaluation — measuring outcomes of agent runs on realistic tasks rather than single answers
LLM-as-judge — using a model to grade outputs against a rubric
Human in the loop — a person approves or corrects the agent at chosen points
Plan-and-execute — an agent writes a plan first and then carries out its steps
Self-correction — an agent checks its own output and repairs it before finishing
Long-horizon task — work that needs many steps and hours of autonomous progress
""",
        "entities": """
Claude Code — Anthropic's command-line coding agent
Codex CLI — OpenAI's command-line coding agent
LangGraph — a library for building agents as state graphs
SWE-bench — a benchmark of real GitHub issues for coding agents
""",
        "sources": """
web | anthropic | Building effective agents — patterns from simple workflows to autonomous agents, and when each is worth it
video | youtube | Context engineering for agents, a talk — why the context window is the agent's working memory
tweet | x | A thread on agent memory designs — scratchpad, summaries, vector stores and wikis compared
session | claude | Designing the review gate for a coding agent — a working session that settled on two independent checks
web | simonwillison | Prompt injection explained — why mixing instructions and data is the core risk
podcast | latentspace | Evaluating agents in production — outcome metrics, traces and graders
""",
    },
    "AI/Models and research": {
        "topic": "How large language models work — from tokens and attention to training and fine-tuning",
        "concepts": """
Transformer — a neural network architecture built on self-attention, introduced in 2017
Self-attention — each token weighs every other token when computing its representation
Tokenization — splitting text into the units a model reads and writes
Embedding — a vector that places text so that similar meanings are close together
Pretraining — learning to predict the next token on a very large text corpus
Fine-tuning — further training a pretrained model on narrower data
Reinforcement learning from human feedback — training a model toward outputs people prefer
Scaling laws — loss falls predictably as parameters, data and compute grow
Mixture of experts — a model that routes each token to a few specialised sub-networks
Quantization — storing weights with fewer bits to save memory and time
Hallucination — fluent output that is not supported by facts or sources
Reranker — a model that reorders retrieved candidates by relevance to the query
Chain of thought — writing intermediate reasoning steps before the answer
Distillation — training a small model to imitate a larger one
""",
        "entities": """
Attention Is All You Need — the 2017 paper that introduced the Transformer
Andrej Karpathy — AI researcher and educator, author of the LLM wiki idea
Hugging Face — a hub for sharing models and datasets
llama.cpp — a C/C++ runtime for running language models locally
""",
        "sources": """
video | youtube | Intro to large language models — an hour-long overview of how LLMs are trained and used
web | arxiv | Attention Is All You Need (arXiv 1706.03762) — the original Transformer paper
video | bilibili | Transformer, explained with drawings — attention computed step by step on a small example
web | huggingface | A guide to quantization — 8-bit and 4-bit formats and what they cost in quality
tweet | x | Notes on the LLM wiki pattern — an LLM maintains a markdown wiki from your sources
podcast | dwarkesh | Scaling laws and what comes next — a conversation on compute, data and limits
""",
    },
    "Software/Backend and systems": {
        "topic": "Building backend services — concurrency, networking and keeping systems up",
        "concepts": """
Idempotency — repeating an operation has the same effect as doing it once
Backpressure — slowing producers when consumers cannot keep up
Rate limiting — capping how many requests a client may make per time window
Circuit breaker — stop calling a failing dependency for a while instead of piling up errors
Retry with exponential backoff — wait longer after each failed attempt, with jitter
Event loop — one thread multiplexing many I/O operations
Connection pool — reusing open connections instead of opening one per request
Consensus — nodes agreeing on one value despite failures, as in Raft or Paxos
CAP theorem — under a network partition a system must give up consistency or availability
Message queue — a buffer that decouples producers from consumers
Load balancing — spreading requests across several servers
Graceful degradation — keeping core features working when parts fail
Observability — understanding a system from its logs, metrics and traces
File locking — coordinating processes that share files, e.g. with flock
""",
        "entities": """
Raft — a consensus algorithm designed to be understandable
Kafka — a distributed log used as a message bus
Nginx — a web server and reverse proxy
""",
        "sources": """
web | martinfowler | Circuit breaker, the pattern — the pattern and its states, closed, open and half-open
video | youtube | Designing data-intensive applications, chapter talks — replication, partitioning and consistency
web | aws | Exponential backoff and jitter — why random jitter prevents synchronized retries
session | codex | Debugging a job that hung on a lock — two processes waiting on each other, fixed with separate locks
""",
    },
    "Software/Data and storage": {
        "topic": "Storing and searching data — databases, indexes and full-text search",
        "concepts": """
B-tree — a balanced tree that keeps sorted keys for fast lookups on disk
Write-ahead log — recording changes before applying them so they survive crashes
Full-text search — finding documents by the words they contain
Inverted index — a map from each term to the documents that contain it
BM25 — a ranking function that scores documents by term frequency and rarity
Inverse document frequency — rare terms weigh more than common ones
Stemming — reducing words to a common root, e.g. running to run
Vector search — finding items whose embeddings are nearest to the query's
Hybrid search — combining keyword and vector results into one ranking
Reciprocal rank fusion — merging rankings by summing 1/(k + rank)
SQLite — an embedded SQL database stored in a single file
FTS5 — SQLite's full-text search extension
Transaction isolation — how much concurrent transactions see of each other
JSON Lines — one JSON object per line, easy to append and stream
""",
        "entities": """
PostgreSQL — an open-source relational database
Lucene — a search library behind Elasticsearch and Solr
DuckDB — an in-process analytical database
""",
        "sources": """
web | sqlite | SQLite FTS5 documentation — tokenizers, the bm25 function and auxiliary tables
web | elastic | Practical BM25 — how k1 and b shape term saturation and length normalisation
video | youtube | Vector databases in ten minutes — embeddings, approximate nearest neighbours and HNSW
doc | local | Notes on hybrid search experiments — keyword and vector results fused with reciprocal rank fusion
""",
    },
    "Software/Architecture and practice": {
        "topic": "Engineering practice — design, testing and running software well",
        "concepts": """
Test-driven development — writing a failing test before the code that passes it
Code review — a second person reads a change before it is merged
Continuous integration — building and testing every change automatically
Feature flag — turning behaviour on or off without a deploy
Technical debt — shortcuts that make later changes slower
Postmortem — a blameless write-up of what went wrong and what changes
Separation of concerns — each part of a system handles one responsibility
Twelve-factor app — practices for services that run well in the cloud
Semantic versioning — major.minor.patch numbers that signal compatibility
Monorepo — many projects kept in one repository
Trunk-based development — everyone merges small changes into one main branch
Staging environment — a copy of production for checking changes before release
""",
        "entities": """
Git — a distributed version control system
GitHub — a hosting service for git repositories
Martin Fowler — author on software design and refactoring
""",
        "sources": """
web | martinfowler | Trunk-based development in practice — short-lived branches and continuous integration
video | youtube | How Google does code review — small changes, fast responses, explicit standards
session | claude | Setting up a staging worktree for automated edits — every change is checked before it reaches main
web | 12factor | The twelve-factor app — config in the environment, stateless processes, logs as streams
""",
    },
    "Science/Mathematics and statistics": {
        "topic": "Thinking with numbers — probability, statistics and measurement",
        "concepts": """
Bayes' theorem — how to update a probability when new evidence arrives
Base rate — the prior frequency that is easy to ignore
Regression to the mean — extreme measurements tend to be followed by more typical ones
Confidence interval — a range that would contain the true value in a stated share of repeats
Statistical significance — a result unlikely under a null hypothesis
Survivorship bias — drawing conclusions only from the cases that made it through
Calibration — stated probabilities match observed frequencies
Expected value — the probability-weighted average of outcomes
Law of large numbers — averages settle near the expected value as samples grow
Precision and recall — how many found items are right, and how many right items are found
Mean reciprocal rank — the average of 1/rank of the first correct result
Power law — a few items account for most of the total
""",
        "entities": """
Thomas Bayes — 18th-century statistician behind Bayes' theorem
Daniel Kahneman — psychologist who studied judgment under uncertainty
""",
        "sources": """
video | youtube | Bayes' theorem, the geometry of changing beliefs — updating with a picture instead of a formula
web | wikipedia | Calibration (statistics) — reliability diagrams and the Brier score
podcast | econtalk | Why base rates matter — forecasting and the outside view
""",
    },
    "Business/Finance and economics": {
        "topic": "Investing basics — markets, risk and long-term returns",
        "concepts": """
Compound interest — returns that earn returns of their own
Index fund — a fund that holds a whole market index at low cost
Diversification — spreading money across assets that do not move together
Asset allocation — the split between stocks, bonds and cash
Dollar-cost averaging — investing a fixed amount at regular intervals
Expense ratio — the yearly fee a fund charges as a share of assets
Volatility — how widely an asset's price swings
Drawdown — the fall from a peak to a later low
Rebalancing — moving a portfolio back to its target allocation
Inflation — the general rise of prices over time
Opportunity cost — the value of the best alternative given up
Efficient market hypothesis — prices already reflect available information
Behavioral finance — how biases shape investors' decisions
""",
        "entities": """
John Bogle — founder of Vanguard and of the first index fund for individual investors
Warren Buffett — investor and chairman of Berkshire Hathaway
""",
        "sources": """
video | bilibili | Index funds for beginners — why low fees and broad holdings win over decades
web | investopedia | Asset allocation by goal and horizon — matching the stock and bond split to goals and time horizon
podcast | rationalreminder | Rebalancing, how often and why — calendar and threshold rules compared
tweet | x | A thread on drawdowns — what a 50 percent fall needs to recover
""",
    },
    "Business/Product and growth": {
        "topic": "Building products people use — discovery, metrics and growth",
        "concepts": """
Product-market fit — a product satisfies a strong market demand
Jobs to be done — customers hire a product to make progress in a situation
North star metric — the one number that best captures delivered value
Retention curve — the share of users still active over time
Activation — the moment a new user first gets real value
A/B test — comparing two variants on randomly split users
Minimum viable product — the smallest thing that tests a key assumption
Onboarding — the first steps that lead a new user to value
Dogfooding — a team using its own product daily
Open-source launch — publishing a project with docs, license and a clear first-run path
""",
        "entities": """
Y Combinator — a startup accelerator
Paul Graham — essayist and co-founder of Y Combinator
""",
        "sources": """
web | paulgraham | Do things that don't scale — early growth comes from manual, personal effort
video | youtube | How to launch an open-source project — README, demo and the first ten minutes
session | claude | Planning the open-source release of a side project — scope, privacy review and first-run test
""",
    },
    "Life/Learning and health": {
        "topic": "Learning that lasts — memory, practice and focus",
        "concepts": """
Active recall — retrieving an answer from memory instead of rereading
Interleaving — mixing problem types during practice
Deliberate practice — focused practice on weaknesses with feedback
Desirable difficulty — effort during learning that improves later recall
Feynman technique — explaining an idea simply to find gaps in understanding
Sleep and memory — sleep consolidates what was learned during the day
Deep work — long blocks of focus without distraction
Note-taking — recording ideas in a way that helps later use
Progressive summarization — highlighting notes in layers over several passes
Second brain — an external system for keeping and connecting what you learn
""",
        "entities": """
Cal Newport — author of Deep Work
Sönke Ahrens — author of How to Take Smart Notes
""",
        "sources": """
video | youtube | How to study with active recall — testing yourself beats rereading
podcast | hubermanlab | Sleep and learning — what happens to memories overnight
video | bilibili | Building a second brain, a review — capture, organise, distil, express
tweet | x | Interleaving in practice — a teacher's results from mixing problem sets
""",
    },
    "Life/Culture and history": {
        "topic": "Ideas that shaped computing — from the memex to the web",
        "concepts": """
Memex — Vannevar Bush's 1945 vision of a desk that stores and links everything one reads
Hypertext — text with links that let a reader jump between documents
Associative trails — Bush's term for recorded chains of linked documents
Personal computing — computers used by one person for their own work
The mother of all demos — Engelbart's 1968 demonstration of the mouse, hypertext and video calls
World Wide Web — a hypertext system on the internet, proposed in 1989
Wiki — a website its readers can edit, first built in 1995
Commonplace book — a personal notebook of quotes and ideas, kept for centuries
""",
        "entities": """
Vannevar Bush — engineer who described the memex in As We May Think (1945)
Douglas Engelbart — inventor of the computer mouse and pioneer of hypertext
Tim Berners-Lee — inventor of the World Wide Web
Ward Cunningham — creator of the first wiki
""",
        "sources": """
web | theatlantic | As We May Think — Vannevar Bush's 1945 essay introducing the memex
video | youtube | The mother of all demos, annotated — Engelbart's 1968 presentation with commentary
web | wikipedia | History of wikis — from WikiWikiWeb to Wikipedia
""",
    },
    "Creative/Design and media": {
        "topic": "Designing calm interfaces — type, colour and motion",
        "concepts": """
Visual hierarchy — arranging elements so the most important is seen first
Typographic scale — a fixed set of type sizes in consistent ratios
Colour contrast — the luminance difference that keeps text readable
Spring animation — motion driven by a physical spring model instead of fixed curves
Dark mode — a colour scheme with light text on dark backgrounds
Data visualisation — showing numbers as shapes so patterns are visible
Glassmorphism — translucent, blurred panels layered over a background
""",
        "entities": """
Edward Tufte — writer on information design
Dieter Rams — industrial designer known for ten principles of good design
""",
        "sources": """
video | youtube | Designing with springs — why physical motion feels natural
web | smashingmagazine | Contrast and readability in dark interfaces — choosing tones that hold up
""",
    },
}

# questions agents asked (shown on the usage page and as recent questions); each names the page it should find
QUESTIONS = [
    ("how should an agent decide what goes into its context window?", "Context engineering"),
    ("what's the difference between BM25 and vector search?", "Hybrid search"),
    ("why add jitter to retries?", "Retry with exponential backoff"),
    ("how often should I rebalance a portfolio?", "Rebalancing"),
    ("what did Vannevar Bush mean by associative trails?", "Associative trails"),
    ("does interleaving beat blocked practice?", "Interleaving"),
    ("how does SQLite's FTS5 rank results?", "FTS5"),
    ("what is a reranker and when is it worth it?", "Reranker"),
    ("how do I stop a prompt injection from a web page?", "Prompt injection"),
    ("what is the LLM wiki idea?", "Andrej Karpathy"),
    ("what makes a good postmortem?", "Postmortem"),
    ("how does the Leitner box system work?", "Leitner system"),
    ("what's a safe way to let an agent run shell commands?", "Sandboxing"),
    ("how long does a 50% drawdown take to recover?", "Drawdown"),
    ("which retrieval metric rewards the first correct hit?", "Mean reciprocal rank"),
    ("why is my vector index slow to build the first time?", "Embedding"),
]

# reports agents left for the owner (inbox)
INBOX = """# Inbox

## Two sources disagree on the year Anki was released
- The page on Anki says 2006; a newer video says 2007.
- Options: keep 2006 (the project's own changelog), or note the disagreement.

## New category proposal: Personal finance tools
- 6 recent sources are about budgeting apps and tax software; none of the existing categories fits well.

## Should liked videos about games be collected?
- Last week 9 liked videos were game playthroughs; all were rejected by admission.
- Options: keep rejecting, or stop reading the liked-videos list.

## The page on Raft cites only a talk
- Every claim on [[Raft]] comes from one conference talk; the paper itself is not in raw/.
- An agent could fetch the paper and check the page against it.
"""
