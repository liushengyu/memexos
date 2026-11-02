// Download (or check) qmd's local models without touching ones already downloaded.
//   node qmd_models.mjs pull  <model> ...   download what is missing, with a progress bar
//   node qmd_models.mjs check <model> ...   print {"model": path-or-null} without downloading
// A model is an hf: URI, or embed / rerank / generate for qmd's own defaults.
//
// `qmd pull` is not used: when it cannot confirm a cached model's version online it deletes the model before
// downloading it again, so a flaky network can leave a working search without its models. node-llama-cpp's
// resolveModelFile (the same code qmd uses on first use, same cache directory and file names) only downloads
// what is missing. The qmd package directory comes from QMD_PKG (memexos.search.qmd.package_dir).
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { join } from "node:path";

const [mode, ...uris] = process.argv.slice(2);
const req = createRequire(join(process.env.QMD_PKG, "package.json"));
const { resolveModelFile } = await import(pathToFileURL(req.resolve("node-llama-cpp")).href);
const llm = await import(pathToFileURL(join(process.env.QMD_PKG, "dist", "llm.js")).href);
const { DEFAULT_MODEL_CACHE_DIR } = llm;
// "embed", "rerank", "generate" stand for qmd's own default models
const ALIAS = { embed: llm.DEFAULT_EMBED_MODEL_URI, rerank: llm.DEFAULT_RERANK_MODEL_URI, generate: llm.DEFAULT_GENERATE_MODEL_URI };

const out = {};
let failed = false;
for (const name of uris) {
  const uri = ALIAS[name] || name;
  try {
    out[name] = await resolveModelFile(uri, { directory: DEFAULT_MODEL_CACHE_DIR, download: mode === "pull" ? "auto" : false,
                                             cli: mode === "pull" && process.stderr.isTTY });
  } catch (e) {
    out[name] = null;
    if (mode === "pull") {
      failed = true;
      console.error(`${uri}: ${e.message || e}`);
    }
  }
}
console.log(JSON.stringify(out));
process.exit(failed ? 1 : 0);
