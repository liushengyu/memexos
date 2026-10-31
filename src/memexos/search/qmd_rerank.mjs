// Score candidate passages with qmd's bundled local reranker (Qwen3-Reranker, GGUF via node-llama-cpp).
// stdin: one JSON object per line {"id", "query", "docs": [{"file", "text"}], "instruction"?}
// stdout: one JSON object per line {"id", "model", "scores": {file: score}}
//
// The qmd package directory comes from QMD_PKG (found by memexos.search.qmd.package_dir). This reaches
// into qmd's distributed module rather than a public API, so it can break when qmd changes; MemexOS
// treats any failure as "no reranker" and keeps the fused order.
//
// instruction (optional) replaces the generic retrieval instruction in the reranker's prompt template.
// Asking whether the document "contains the specific information needed to answer", rather than whether
// it is merely on the same topic, ranks answer-bearing pages higher.
import * as readline from "node:readline";
import { pathToFileURL } from "node:url";
import { join } from "node:path";

const { getDefaultLlamaCpp } = await import(pathToFileURL(join(process.env.QMD_PKG, "dist", "llm.js")).href);
const llm = getDefaultLlamaCpp();
const CONTEXT = 4096;
const OVERHEAD = 512;
const contexts = new Map();

function template(ins) {
  return "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. " +
    "Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n" +
    `<Instruct>: ${ins}\n<Query>: {{query}}\n<Document>: {{document}}<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n`;
}

async function rankWith(ins, query, docs) {
  const model = await llm.ensureRerankModel();
  if (!contexts.has(ins)) {
    contexts.set(ins, await model.createRankingContext({ contextSize: CONTEXT, template: template(ins) }));
  }
  const maxDoc = CONTEXT - OVERHEAD - model.tokenize(query).length;
  const texts = docs.map((d) => {
    const toks = model.tokenize(d.text);
    return toks.length <= maxDoc ? d.text : model.detokenize(toks.slice(0, maxDoc));
  });
  const scores = await contexts.get(ins).rankAll(query, texts);
  return Object.fromEntries(docs.map((d, i) => [d.file, scores[i]]));
}

const rl = readline.createInterface({ input: process.stdin });
for await (const line of rl) {
  if (!line.trim()) continue;
  const t = JSON.parse(line);
  let scores, model = "qmd-reranker";
  if (t.instruction) {
    scores = await rankWith(t.instruction, t.query, t.docs);
  } else {
    const r = await llm.rerank(t.query, t.docs);
    scores = {};
    for (const x of r.results) scores[x.file] = x.score;
    model = r.model;
  }
  process.stdout.write(JSON.stringify({ id: t.id, model, scores }) + "\n");
}
process.exit(0);
