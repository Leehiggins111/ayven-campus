"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const runner = require("./milo-package-runner");

test("the existing twelve package checks cannot qualify the full product", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "full-acceptance-"));
  try {
    assert.equal(runner.fullProductAccepted(dir), false);
    fs.writeFileSync(path.join(dir, "acceptance.json"), JSON.stringify({ acceptance_scope: "existing-package-regressions", full_product_accepted: false, results: { "1": { status: "PASS" } } }));
    assert.equal(runner.fullProductAccepted(dir), false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("a zero process exit cannot hide a failed acceptance check", () => {
  assert.equal(runner.acceptancePassed({ status: 0, stdout: "PASS 1\nFAIL 2", stderr: "" }), false);
  assert.equal(runner.acceptancePassed({ status: 1, stdout: "PASS 1", stderr: "" }), false);
  assert.equal(runner.acceptancePassed({ status: 0, stdout: "PASS 1", stderr: "FAIL acceptance" }), false);
  assert.equal(runner.acceptancePassed({ status: 0, stdout: "PASS 1", stderr: "" }), true);
});

test("the download checksum changes when archive bytes change", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "archive-checksum-"));
  try {
    const file = path.join(dir, "package.zip");
    fs.writeFileSync(file, "first archive");
    const first = runner.sha256File(file);
    fs.writeFileSync(file, "changed archive");
    assert.notEqual(runner.sha256File(file), first);
    assert.equal(runner.sha256File(file), require("crypto").createHash("sha256").update("changed archive").digest("hex"));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

const WORKFLOW = `
name: windows-package
on: workflow_dispatch
jobs:
  package:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup Node
        uses: actions/setup-node@v4
      - name: Install Electron dependencies
        working-directory: desktop
        run: npm ci
      - name: Build unpacked Electron
        shell: pwsh
        run: |
          npx electron-builder --win dir
          echo done
      - name: Embeddable Python
        shell: pwsh
        run: |
          Expand-Archive python.zip -DestinationPath python-embed
      - name: Acceptance
        run: ./scripts/windows/Invoke-Acceptance.ps1
      - name: Upload evidence
        uses: actions/upload-artifact@v4
`;

test("prelude keeps the electron and python steps only", () => {
  const selected = runner.selectSteps(runner.parseWorkflowSteps(WORKFLOW));
  assert.deepEqual(selected.map((step) => step.name), [
    "Install Electron dependencies",
    "Build unpacked Electron",
    "Embeddable Python",
  ]);
  assert.equal(selected[0].cwd, "desktop");
  assert.match(selected[1].run, /electron-builder --win dir/);
  assert.equal(selected[1].shell, "pwsh");
  assert.match(selected[2].run, /python-embed/);
  assert.doesNotMatch(selected.map((step) => step.run).join("\n"), /Invoke-Acceptance/);
});

test("expressions are substituted and logs stay narrow", () => {
  const script = runner.substitute("cd ${{ github.workspace }}\\milo\n${{ github.sha }}", {
    workspace: "D:\\src",
    miloSha: "1d04499b8d05819f2743bf54b7d1a9f791b157ca",
  });
  assert.equal(script.includes("${{"), false);
  assert.match(script, /1d04499b8d05819f2743bf54b7d1a9f791b157ca/);
  assert.equal(runner.releaseTag("abcdef1234567890", "1d04499b8d05819f2743bf54b7d1a9f791b157ca"), "ayven-milo-abcdef1-1d04499");
  assert.equal(runner.publicLine("PASS assemble"), "PASS assemble");
  assert.equal(runner.publicLine("the plan text"), "");
  assert.equal(runner.redact("token ghp_abcdefghijklmnopqrstuvwxyz and lee@example.com"), "token *** and ***");
  const lines = runner.diagnosticLines("noise\n\u001b[31mERROR: Could not open requirements file: [Errno 2] No such file\n20 | if ($LASTEXITCODE -ne 0) { throw \"Campus requirements failed\" }\nCampus requirements failed\n");
  assert.deepEqual(lines, [
    "ERROR: Could not open requirements file: [Errno 2] No such file",
    "Campus requirements failed",
  ]);
  assert.equal(lines.some((line) => line.includes("$")), false);
});

test("a vendored campus tree is replaced by the checkout, and a stale render.py fails", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "milo-link-"));
  const campus = path.join(root, "campus");
  const milo = path.join(root, "milo");
  const render = path.join(campus, "apps", "api", "app", "intelligence");
  fs.mkdirSync(render, { recursive: true });
  fs.mkdirSync(path.join(milo, "campus", "old"), { recursive: true });
  const source = "def _business_plan():\n    return 'Facts come from the request or from a page that was opened\\nTarget customer'\n";
  fs.writeFileSync(path.join(render, "render.py"), source);
  fs.writeFileSync(path.join(milo, "campus", "render.py"), "old briefing\n");
  const linked = runner.linkCampus(milo, campus);
  assert.equal(linked.length > 0, true);
  assert.equal(fs.readFileSync(path.join(milo, "campus", "apps", "api", "app", "intelligence", "render.py"), "utf8"), source);
  const packaged = path.join(root, "package");
  fs.mkdirSync(path.join(packaged, "app"), { recursive: true });
  fs.writeFileSync(path.join(packaged, "app", "render.py"), "old briefing\n");
  const stale = runner.campusRenderMatches(packaged, campus);
  assert.equal(stale.ok, false);
  fs.writeFileSync(path.join(packaged, "app", "render.py"), source);
  const fresh = runner.campusRenderMatches(packaged, campus);
  assert.equal(fresh.ok, true);
  fs.rmSync(root, { recursive: true, force: true });
});

test("sample database arguments are files the seed script can replace", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "seed-args-"));
  const [miloDb, campusDb] = runner.seedArguments(
    "fs.unlinkSync(miloArg);\nfs.copyFileSync('templates/milo-sample.db', miloArg);\nfs.copyFileSync('templates/campus-sample.db', campusArg);\n",
    dir,
  );
  assert.equal(path.basename(miloDb), "milo-sample.db");
  assert.equal(path.basename(campusDb), "campus-sample.db");
  assert.equal(fs.statSync(miloDb).isFile(), true);
  fs.rmSync(dir, { recursive: true, force: true });
});
