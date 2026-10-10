"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const runner = require("./milo-package-runner");

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
  const lines = runner.diagnosticLines("noise\n\u001b[31mERROR: Could not open requirements file: [Errno 2] No such file\nCampus requirements failed\n");
  assert.deepEqual(lines, [
    "ERROR: Could not open requirements file: [Errno 2] No such file",
    "Campus requirements failed",
  ]);
});
