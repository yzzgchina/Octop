import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Repo root = the checkout this script lives in (docs/planning/tools/ -> ../../..).
// Set OCTOP_ROOT to check another checkout. Never hard-code a machine path here.
const repo = process.env.OCTOP_ROOT
  ? path.resolve(process.env.OCTOP_ROOT)
  : path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

function keys(obj, prefix = "") {
  const out = [];
  for (const [k, v] of Object.entries(obj)) {
    const keyPath = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) out.push(...keys(v, keyPath));
    else out.push(keyPath);
  }
  return out;
}

for (const locale of ["en", "zh"]) {
  const rel = `dashboard/src/locales/${locale}.json`;
  const head = JSON.parse(
    execFileSync("git", ["show", `HEAD:${rel}`], { cwd: repo, encoding: "utf8" }),
  );
  const cur = JSON.parse(fs.readFileSync(path.join(repo, rel), "utf8"));
  const H = new Set(keys(head));
  const C = new Set(keys(cur));
  const added = [...C].filter((k) => !H.has(k)).sort();
  const removed = [...H].filter((k) => !C.has(k)).sort();
  console.log(
    `[${locale}] keys head=${H.size} current=${C.size} added=${added.length} removed=${removed.length}`,
  );
  console.log(`  added: ${added.join(", ")}`);
  if (removed.length) console.log(`  REMOVED: ${removed.join(", ")}`);
}
