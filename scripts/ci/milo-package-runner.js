"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const KEEP = /electron|unpacked|embed|python|npm|builder|win32|win-unpacked/i;
const DROP = /acceptance|upload|release|artifact|assemble|seed|verify|checkout|deploy|secret|test/i;

function parseWorkflowSteps(text) {
  const lines = String(text || "").split(/\r?\n/);
  const steps = [];
  let current = null;
  let inRun = false;
  let runIndent = 0;
  const runLines = [];

  function finishRun() {
    if (!current) return;
    const content = runLines.splice(0, runLines.length);
    const indents = content.filter((line) => line.trim()).map((line) => line.match(/^\s*/)[0].length);
    const cut = indents.length ? Math.min(...indents) : 0;
    current.run = content.map((line) => (line.trim() ? line.slice(cut) : "")).join("\n").trim();
    inRun = false;
  }

  function flush() {
    if (inRun) finishRun();
    if (current && (current.run || current.uses)) steps.push(current);
    current = null;
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (inRun) {
      if (line.trim() === "") {
        runLines.push("");
        continue;
      }
      const indent = line.match(/^\s*/)[0].length;
      if (indent <= runIndent) {
        finishRun();
        index -= 1;
        continue;
      }
      runLines.push(line);
      continue;
    }
    const named = line.match(/^\s*-\s+name:\s*(.+?)\s*$/);
    const used = line.match(/^\s*-\s+uses:\s*(\S+)\s*$/);
    if (named || used) {
      flush();
      current = { name: (named ? named[1] : used[1]).replace(/^['"]|['"]$/g, ""), run: "", uses: used ? used[1] : "", cwd: "", shell: "" };
      continue;
    }
    if (!current) continue;
    const uses = line.match(/^\s+uses:\s*(\S+)\s*$/);
    const runBlock = line.match(/^(\s*)run:\s*[|>][-+]?\s*$/);
    const runInline = line.match(/^\s*run:\s+([^|>].*)$/);
    const cwd = line.match(/^\s*working-directory:\s*(.+?)\s*$/);
    const shell = line.match(/^\s*shell:\s*(\S+)\s*$/);
    if (uses && !current.run) current.uses = uses[1];
    else if (runBlock) {
      inRun = true;
      runIndent = runBlock[1].length;
      runLines.length = 0;
    } else if (runInline) current.run = runInline[1].trim();
    else if (cwd) current.cwd = cwd[1].replace(/^['"]|['"]$/g, "");
    else if (shell) current.shell = shell[1];
  }
  flush();
  return steps;
}

function selectSteps(steps) {
  return (steps || []).filter((step) => {
    if (!step.run || step.uses) return false;
    if (DROP.test(step.name || "")) return false;
    if (/secrets\.|MILO_BUILD_TOKEN|GITHUB_TOKEN/.test(step.run)) return false;
    return KEEP.test(`${step.name}\n${step.run}`);
  });
}

function substitute(script, vars) {
  const values = vars || {};
  return String(script || "")
    .replaceAll("${{ github.workspace }}", values.workspace || "")
    .replaceAll("${{ github.sha }}", values.miloSha || "")
    .replaceAll("${{ runner.temp }}", values.temp || "")
    .replaceAll("${{ runner.os }}", "Windows");
}

function releaseTag(campusSha, miloSha) {
  return `ayven-milo-${String(campusSha || "").slice(0, 7)}-${String(miloSha || "").slice(0, 7)}`;
}

function publicLine(line) {
  const text = String(line || "").replace(/\r/g, "").trim();
  if (/^(PASS|FAIL)\b/i.test(text)) return text;
  if (/^(CAMPUS|MILO|CHECKSUM|RELEASE)\b/.test(text)) return text;
  return "";
}

function redact(line) {
  return String(line || "")
    .replace(/\u001b\[[0-9;]*m/g, "")
    .replace(/github_pat_[A-Za-z0-9_]+/g, "***")
    .replace(/ghp_[A-Za-z0-9]+/g, "***")
    .replace(/Bearer\s+\S+/gi, "Bearer ***")
    .replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, "***")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 180);
}

function diagnosticLines(output) {
  const kept = [];
  for (const raw of String(output || "").split(/\r?\n/)) {
    const line = redact(raw);
    if (!line) continue;
    if (/^(def |class |function |const |import )/.test(line)) continue;
    if (/ERROR:|error:|Failed|Could not|No matching|No module named|not recognized|requirements|Cannot find|exit code/i.test(line)) {
      kept.push(line);
    }
  }
  return kept.slice(-12);
}

function childEnv() {
  const env = { ...process.env };
  delete env.MILO_BUILD_TOKEN;
  delete env.GITHUB_TOKEN;
  return env;
}

function say(line) {
  const text = publicLine(line);
  if (text) process.stdout.write(`${text}\n`);
}

function fail(scope, detail) {
  say(`FAIL ${scope}: ${redact(detail || "exit")}`);
}

function runPwsh(args, cwd, extraEnv) {
  const env = { ...childEnv(), ...(extraEnv || {}) };
  return spawnSync("pwsh", ["-NoProfile", "-NonInteractive", ...args], {
    cwd,
    env,
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024,
  });
}

function runNode(args, cwd) {
  return spawnSync(process.execPath, args, {
    cwd,
    env: childEnv(),
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024,
  });
}

function forwardOutput(result) {
  const combined = `${result.stdout || ""}\n${result.stderr || ""}`;
  for (const line of combined.split(/\r?\n/)) say(line);
  return combined;
}

function reasonFrom(output) {
  const lines = String(output || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const line = redact(lines[index]);
    if (!line) continue;
    if (/^(PASS|FAIL)\b/i.test(line)) return line;
    if (line.length < 160 && !/function |const |import |^def /.test(line)) return line;
  }
  return "exit";
}

function chooseWorkflow(milo) {
  const dir = path.join(milo, ".github", "workflows");
  if (!fs.existsSync(dir)) return { selected: [], names: [] };
  const names = fs.readdirSync(dir).filter((name) => name.endsWith(".yml") || name.endsWith(".yaml"));
  let best = [];
  let bestScore = -1;
  for (const name of names) {
    const text = fs.readFileSync(path.join(dir, name), "utf8");
    const selected = selectSteps(parseWorkflowSteps(text));
    let score = selected.length;
    if (/Assemble-Package/.test(text)) score += 5;
    if (/embed/i.test(text)) score += 2;
    if (score > bestScore) {
      best = selected;
      bestScore = score;
    }
  }
  return { selected: best, names };
}

function scriptNames(milo) {
  const dir = path.join(milo, "scripts", "windows");
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter((name) => name.endsWith(".ps1") || name.endsWith(".js"));
}

function runPrelude(milo, vars) {
  const chosen = chooseWorkflow(milo);
  if (!chosen.selected.length) {
    const scripts = scriptNames(milo);
    fail("prelude", `workflows ${chosen.names.join(",") || "none"} scripts ${scripts.join(",") || "none"}`);
    return false;
  }
  for (const step of chosen.selected) {
    const script = substitute(step.run, vars);
    const file = path.join(vars.temp, `prelude-${chosen.selected.indexOf(step)}.ps1`);
    fs.writeFileSync(file, script, "utf8");
    const cwd = step.cwd ? path.resolve(milo, step.cwd) : milo;
    const result = runPwsh(["-File", file], cwd, {
      CAMPUS_DIR: vars.campus || process.env.CAMPUS_DIR || "",
      CAMPUS_SRC: vars.campus || process.env.CAMPUS_DIR || "",
    });
    fs.rmSync(file, { force: true });
    const output = forwardOutput(result);
    if (result.status !== 0) {
      const lines = diagnosticLines(output);
      if (!lines.length) fail(step.name || "prelude", reasonFrom(output) || `exit ${result.status}`);
      for (const line of lines) fail(step.name || "prelude", line);
      return false;
    }
    say(`PASS ${step.name || "prelude"}`);
  }
  return true;
}

function walk(dir, visitor, depth) {
  if (!dir || depth > 8 || !fs.existsSync(dir)) return;
  let entries = [];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    if (entry.name === "node_modules" || entry.name === ".git") continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name.toLowerCase() === "win-unpacked") visitor({ unpacked: full });
      walk(full, visitor, depth + 1);
    } else if (entry.name.toLowerCase() === "python.exe") {
      visitor({ python: path.dirname(full) });
    }
  }
}

function discover(roots) {
  const found = { unpacked: [], python: [] };
  for (const root of roots) {
    walk(root, (item) => {
      if (item.unpacked) found.unpacked.push(item.unpacked);
      if (item.python) found.python.push(item.python);
    }, 0);
  }
  const python = found.python.find((dir) => fs.readdirSync(dir).some((name) => /^python\d*._pth$/i.test(name))) || found.python[0] || "";
  const unpacked = found.unpacked.sort((left, right) => fs.statSync(right).mtimeMs - fs.statSync(left).mtimeMs)[0] || "";
  return { unpacked, python };
}

function verifyName(milo) {
  const dir = path.join(milo, "scripts", "windows");
  if (!fs.existsSync(dir)) return "";
  const names = fs.readdirSync(dir);
  return names.find((name) => name.toLowerCase() === "verify-sample-databases.js")
    || names.find((name) => /verify/i.test(name) && name.endsWith(".js"))
    || "";
}

function sha256File(file) {
  const hash = crypto.createHash("sha256");
  const fd = fs.openSync(file, "r");
  const buffer = Buffer.alloc(1024 * 1024);
  try {
    let position = 0;
    while (true) {
      const read = fs.readSync(fd, buffer, 0, buffer.length, position);
      if (!read) break;
      hash.update(buffer.subarray(0, read));
      position += read;
    }
  } finally {
    fs.closeSync(fd);
  }
  return hash.digest("hex");
}

function checksumHex(file) {
  if (!file || !fs.existsSync(file)) return "";
  const raw = fs.readFileSync(file, "utf8");
  const match = raw.match(/\b[a-fA-F0-9]{64}\b/);
  return match ? match[0].toLowerCase() : "";
}

function zipPath(source, dest) {
  fs.rmSync(dest, { force: true });
  const result = spawnSync("tar", ["-a", "-c", "-f", dest, "-C", path.dirname(source), path.basename(source)], {
    encoding: "utf8",
  });
  return result.status === 0 && fs.existsSync(dest);
}

async function github(token, method, apiPath, body) {
  const response = await fetch(`https://api.github.com${apiPath}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "ayven-milo-package",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  let parsed = {};
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = {};
    }
  }
  return { ok: response.ok, status: response.status, body: parsed };
}

async function uploadAsset(token, uploadUrl, filePath, name) {
  const endpoint = uploadUrl.replace("{?name,label}", `?name=${encodeURIComponent(name)}`);
  const stat = fs.statSync(filePath);
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/octet-stream",
      "Content-Length": String(stat.size),
      "User-Agent": "ayven-milo-package",
    },
    body: fs.createReadStream(filePath),
    duplex: "half",
  });
  if (!response.ok) {
    await response.arrayBuffer().catch(() => {});
    throw new Error(`HTTP ${response.status}`);
  }
}

function evidenceFiles(dir) {
  const saved = [];
  if (!dir || !fs.existsSync(dir)) return saved;
  const walkFiles = (current) => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        walkFiles(full);
        continue;
      }
      if (!/alba|kitchen|coffee|software|deliverable|acceptance|main\.py$|test_main\.py$|sha256/i.test(entry.name)) continue;
      if (fs.statSync(full).size > 5_000_000) continue;
      saved.push(full);
    }
  };
  walkFiles(dir);
  return saved.slice(0, 40);
}

async function publishRelease(token, tag, miloSha, notes, files) {
  const repo = "/repos/Leehiggins111/Milo";
  const listed = await github(token, "GET", `${repo}/releases?per_page=100`);
  const items = Array.isArray(listed.body) ? listed.body : [];
  let release = items.find((item) => item.tag_name === tag);
  if (!release) {
    const created = await github(token, "POST", `${repo}/releases`, {
      tag_name: tag,
      target_commitish: miloSha,
      name: tag,
      draft: true,
      make_latest: "false",
      body: notes,
    });
    if (!created.ok) throw new Error(`HTTP ${created.status}`);
    release = created.body;
  }
  if (!release || !release.upload_url) throw new Error("HTTP release");
  const existing = new Map((release.assets || []).map((asset) => [asset.name, asset.id]));
  for (const file of files) {
    const name = file.name || path.basename(file.path);
    if (existing.has(name)) {
      const removed = await github(token, "DELETE", `${repo}/releases/assets/${existing.get(name)}`);
      if (!removed.ok && removed.status !== 404) throw new Error(`HTTP ${removed.status}`);
    }
    await uploadAsset(token, release.upload_url, file.path, name);
  }
  return release.tag_name || tag;
}

async function main() {
  const token = process.env.MILO_BUILD_TOKEN || "";
  if (token) process.stdout.write(`::add-mask::${token}\n`);
  const campusSha = process.env.CAMPUS_SHA || "";
  const miloSha = process.env.MILO_SHA || "";
  const milo = process.env.MILO_DIR || "";
  const campus = process.env.CAMPUS_DIR || "";
  const work = process.env.WORK_DIR || path.join(process.cwd(), "work");
  const temp = process.env.RUNNER_TEMP || process.env.TEMP || work;
  fs.mkdirSync(work, { recursive: true });
  fs.mkdirSync(temp, { recursive: true });
  say(`CAMPUS ${campusSha}`);
  say(`MILO ${miloSha}`);
  if (!token) {
    fail("token", "missing");
    process.exit(1);
  }
  const vars = { workspace: path.dirname(milo), miloSha, temp, campus };
  const campusLink = path.join(milo, "campus");
  if (campus && milo && !fs.existsSync(campusLink)) {
    try {
      fs.symlinkSync(campus, campusLink, "junction");
    } catch {
      // The embed step can still use CAMPUS_DIR when the link cannot be created.
    }
  }
  const preludeOk = runPrelude(milo, vars);
  const discovered = discover([work, milo, campus, temp, process.env.RUNNER_TEMP || ""]);
  if (!preludeOk || !discovered.unpacked || !discovered.python) {
    if (!discovered.unpacked) fail("unpacked", "win-unpacked was not produced");
    if (!discovered.python) fail("python", "embeddable python was not produced");
    process.exit(1);
  }
  const packageDir = path.join(work, "package");
  const evidence = path.join(work, "evidence");
  const checksum = path.join(work, "PACKAGE_SHA256.txt");
  fs.mkdirSync(evidence, { recursive: true });
  const assembled = runPwsh([
    "-File", path.join(milo, "scripts", "windows", "Assemble-Package.ps1"),
    "-RepoRoot", milo,
    "-Unpacked", discovered.unpacked,
    "-CampusSrc", campus,
    "-PythonDir", discovered.python,
    "-Destination", packageDir,
    "-LicencesFile", path.join(milo, "docs", "WINDOWS_PACKAGE_LICENCES.md"),
    "-PatchScript", path.join(milo, "scripts", "windows", "patch-campus-bundle.js"),
  ], milo);
  const assembleOut = forwardOutput(assembled);
  if (assembled.status !== 0) {
    fail("assemble", reasonFrom(assembleOut));
    process.exit(1);
  }
  const seeded = runNode([
    path.join(milo, "scripts", "windows", "seed-sample-databases.js"),
    "--milo", milo,
    "--campus", campus,
  ], milo);
  forwardOutput(seeded);
  if (seeded.status !== 0) {
    fail("seed", reasonFrom(`${seeded.stdout || ""}\n${seeded.stderr || ""}`));
    process.exit(1);
  }
  say("PASS seed");
  const verify = verifyName(milo);
  if (!verify) {
    fail("verify", `scripts ${scriptNames(milo).join(",") || "none"}`);
    process.exit(1);
  }
  const verified = runNode([path.join(milo, "scripts", "windows", verify), "--milo", milo, "--campus", campus], milo);
  forwardOutput(verified);
  if (verified.status !== 0) {
    fail("verify", reasonFrom(`${verified.stdout || ""}\n${verified.stderr || ""}`));
    process.exit(1);
  }
  say("PASS verify");
  const accepted = runPwsh([
    "-File", path.join(milo, "scripts", "windows", "Invoke-Acceptance.ps1"),
    "-RepoRoot", milo,
    "-PackageRoot", packageDir,
    "-EvidenceDir", evidence,
    "-ChecksumFile", checksum,
    "-Scorer", path.join(milo, "scripts", "windows", "score-deliverable.js"),
    "-JobMinutes", "35",
  ], milo);
  const acceptOut = forwardOutput(accepted);
  const packageZip = path.join(work, "ayven-milo-package.zip");
  const evidenceZip = path.join(work, "evidence.zip");
  const zipSource = fs.existsSync(packageDir) ? packageDir : "";
  if (zipSource && fs.statSync(zipSource).isDirectory()) zipPath(zipSource, packageZip);
  if (fs.existsSync(evidence)) zipPath(evidence, evidenceZip);
  let hex = checksumHex(checksum);
  if (!hex && fs.existsSync(packageZip)) {
    hex = sha256File(packageZip);
    fs.writeFileSync(checksum, `${hex}\n`, "utf8");
  }
  say(`CHECKSUM ${hex || "missing"}`);
  const tag = releaseTag(campusSha, miloSha);
  const notes = `Campus ${campusSha}\nMilo ${miloSha}\nChecksum ${hex || "missing"}\n`;
  const uploads = [];
  const usedNames = new Set();
  const addUpload = (filePath) => {
    if (!fs.existsSync(filePath)) return;
    let name = path.basename(filePath);
    if (usedNames.has(name)) name = `${uploads.length}-${name}`;
    usedNames.add(name);
    uploads.push({ path: filePath, name });
  };
  addUpload(packageZip);
  addUpload(checksum);
  addUpload(evidenceZip);
  for (const file of evidenceFiles(evidence)) addUpload(file);
  try {
    const published = await publishRelease(token, tag, miloSha, notes, uploads);
    say(`RELEASE ${published}`);
  } catch (error) {
    fail("release", error.message || "upload failed");
    process.exit(1);
  }
  const acceptanceFailed = acceptOut.split(/\r?\n/).some((line) => /^FAIL\b/i.test(line.trim()));
  if (accepted.status !== 0 || acceptanceFailed) process.exit(1);
}

if (require.main === module) {
  main().catch((error) => {
    fail("package", error.message || "failed");
    process.exit(1);
  });
}

module.exports = {
  parseWorkflowSteps,
  selectSteps,
  substitute,
  releaseTag,
  publicLine,
  redact,
  checksumHex,
  diagnosticLines,
};
