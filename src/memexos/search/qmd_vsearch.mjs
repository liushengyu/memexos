// Vector similarity search on a qmd index, without query expansion.
//   node qmd_vsearch.mjs <db path> <config yml> <collection> <n>   (the question on stdin)
// stdout: [{"file", "title", "score", "body"}] best first
//
// `qmd vsearch` first rewrites every question with a 1.7B language model (query expansion) and searches each
// rewrite: that needs another 1.3 GB model and takes seconds on a GPU, tens of seconds on a CPU. MemexOS asks the
// agent for keywords and fuses vectors with keyword search instead, so it calls qmd's library directly for plain
// vector similarity. The qmd package directory comes from QMD_PKG (memexos.search.qmd.package_dir); any failure
// makes MemexOS fall back to the qmd command line.
import { pathToFileURL } from "node:url";
import { join } from "node:path";
import { readFileSync } from "node:fs";

const [dbPath, configPath, collection, n] = process.argv.slice(2);
const query = readFileSync(0, "utf8").trim();
const { createStore } = await import(pathToFileURL(join(process.env.QMD_PKG, "dist", "index.js")).href);
const store = await createStore({ dbPath, configPath });
try {
  const hits = await store.searchVector(query, { limit: Number(n) || 20, collection });
  process.stdout.write(JSON.stringify(hits.map((h) => ({
    file: h.filepath || h.file, title: h.title, score: h.score, body: (h.body || "").slice(0, 2000),
  }))) + "\n");
} finally {
  await store.close();
}
process.exit(0);
