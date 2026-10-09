import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Repo root = the checkout this script lives in (docs/planning/tools/ -> ../../..).
// Set OCTOP_ROOT to check another checkout. Never hard-code a machine path here.
const repo = process.env.OCTOP_ROOT
  ? path.resolve(process.env.OCTOP_ROOT)
  : path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

const files = [
  path.join(repo, "dashboard/src/pages/Projects/index.tsx"),
  path.join(repo, "dashboard/src/pages/Projects/Detail/index.tsx"),
];
const en = JSON.parse(
  fs.readFileSync(path.join(repo, "dashboard/src/locales/en.json"), "utf8"),
);
const zh = JSON.parse(
  fs.readFileSync(path.join(repo, "dashboard/src/locales/zh.json"), "utf8"),
);

function get(obj, dotted) {
  let cur = obj;
  for (const part of dotted.split(".")) {
    if (cur === null || typeof cur !== "object" || !(part in cur)) {
      return undefined;
    }
    cur = cur[part];
  }
  return cur;
}

const used = new Set();
for (const file of files) {
  const src = fs.readFileSync(file, "utf8");
  for (const m of src.matchAll(/\bt\(\s*"([^"]+)"/g)) used.add(m[1]);
  for (const m of src.matchAll(/"((?:projects|common)\.[A-Za-z0-9_]+)"/g)) {
    used.add(m[1]);
  }
}

const missingEn = [...used].filter((k) => get(en, k) === undefined).sort();
const missingZh = [...used].filter((k) => get(zh, k) === undefined).sort();
console.log(`used keys: ${used.size}`);
console.log(`missing in en: ${missingEn.length ? missingEn.join(", ") : "none"}`);
console.log(`missing in zh: ${missingZh.length ? missingZh.join(", ") : "none"}`);
console.log(
  `sanity (en.common.actions -> ${JSON.stringify(get(en, "common.actions"))}, zh.projects.title -> ${JSON.stringify(get(zh, "projects.title"))})`,
);
