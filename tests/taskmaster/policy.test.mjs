import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, copyFile, writeFile, readFile, rm } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { cleanEnvironment, readBacklog, validateProfile, validateEvidence } from "../../scripts/taskmaster/policy.mjs";

const repo = fileURLToPath(new URL("../../", import.meta.url));
async function fixture(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), "jangus-policy-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, ".taskmaster/tasks"), { recursive: true });
  await mkdir(path.join(root, ".taskmaster/evidence"), { recursive: true });
  await copyFile(path.join(repo, ".taskmaster/config.json"), path.join(root, ".taskmaster/config.json"));
  return root;
}
const task = (id, dependencies = []) => ({ id, title: "Fixture", description: "Synthetic", details: "Test", testStrategy: "Assert", dependencies, status: "pending", subtasks: [] });

test("launcher strips credentials and tool overrides without changing home", () => {
  const original = process.env.TASK_MASTER_TOOLS;
  process.env.TASK_MASTER_TOOLS = "all";
  process.env.JANGUS_TEST_SECRET = "synthetic";
  const env = cleanEnvironment(repo);
  assert.equal(env.JANGUS_TEST_SECRET, undefined);
  assert.equal(env.TASK_MASTER_TOOLS, "get_task,get_tasks,next_task,set_task_status");
  assert.equal(env.HOME, process.env.HOME);
  if (original === undefined) delete process.env.TASK_MASTER_TOOLS; else process.env.TASK_MASTER_TOOLS = original;
  delete process.env.JANGUS_TEST_SECRET;
});

for (const mode of ["paid-fallback", "telemetry", "proxy", "env-file"]) {
  test(`reject unsafe profile: ${mode}`, async t => {
    const root = await fixture(t), file = path.join(root, ".taskmaster/config.json");
    const config = JSON.parse(await readFile(file, "utf8"));
    if (mode === "paid-fallback") config.models.fallback.provider = "anthropic";
    if (mode === "telemetry") config.global.anonymousTelemetry = true;
    if (mode === "proxy") config.global.enableProxy = true;
    if (mode === "env-file") await writeFile(path.join(root, ".env"), "SYNTHETIC=example");
    await writeFile(file, JSON.stringify(config));
    await assert.rejects(() => validateProfile(root));
  });
}

for (const mode of ["duplicate", "missing", "cycle", "missing-evidence"]) {
  test(`reject invalid backlog: ${mode}`, async t => {
    const root = await fixture(t);
    let tasks = [task(1), task(2, [1])];
    if (mode === "duplicate") tasks[1].id = 1;
    if (mode === "missing") tasks[1].dependencies = [3];
    if (mode === "cycle") tasks[0].dependencies = [2];
    if (mode === "missing-evidence") tasks[0].status = "done";
    await writeFile(path.join(root, ".taskmaster/tasks/tasks.json"), JSON.stringify({ master: { tasks } }));
    await assert.rejects(() => readBacklog(root));
  });
}

test("completion evidence cannot escape the project", async t => {
  const root = await fixture(t);
  const evidence = { taskId: 1, revision: "a".repeat(40), checks: [{ name: "Check", result: "passed", artifact: "../outside.txt" }] };
  await writeFile(path.join(root, ".taskmaster/evidence/1.json"), JSON.stringify(evidence));
  await assert.rejects(() => validateEvidence(root, 1));
});

test("offline preload blocks HTTP, fetch, and socket connections", () => {
  const script = `import "./scripts/taskmaster/offline.mjs";
    import http from "node:http"; import net from "node:net";
    let blocked=0;
    try {http.get("http://127.0.0.1:11434");} catch {blocked++;}
    try {await fetch("https://example.invalid");} catch {blocked++;}
    try {net.connect(443,"example.invalid");} catch {blocked++;}
    if(blocked!==3) process.exit(1);`;
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", script], { cwd: repo, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
});
