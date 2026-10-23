import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile, copyFile, rm } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { TOOLS } from "../../scripts/taskmaster/adapter.mjs";

const repo = fileURLToPath(new URL("../../", import.meta.url));
const task = (id, dependencies = []) => ({ id, title: `Synthetic task ${id}`, description: "Fixture only", details: "Produce a synthetic result", testStrategy: "Verify fixture bytes", priority: "high", status: "pending", dependencies, subtasks: [] });
const data = result => JSON.parse(result.content[0].text).data;

test("live MCP workflow is narrow, evidence-gated, and does not change the real backlog", { timeout: 60000 }, async t => {
  const original = await readFile(path.join(repo, ".taskmaster/tasks/tasks.json"), "utf8");
  const root = await mkdtemp(path.join(os.tmpdir(), "jangus-mcp-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, ".taskmaster/tasks"), { recursive: true });
  await mkdir(path.join(root, ".taskmaster/evidence"), { recursive: true });
  await copyFile(path.join(repo, ".taskmaster/config.json"), path.join(root, ".taskmaster/config.json"));
  await writeFile(path.join(root, ".taskmaster/tasks/tasks.json"), JSON.stringify({ master: { tasks: [task(1), task(2, [1])] } }));
  const client = new Client({ name: "jangus-smoke", version: "1.0.0" });
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [path.join(repo, "scripts/taskmaster/server.mjs"), "--project-root", root],
    cwd: root, stderr: "pipe",
  });
  t.after(() => client.close());
  await client.connect(transport);
  transport.stderr?.on("data", () => {});
  const tools = await client.listTools();
  assert.deepEqual(tools.tools.map(t => t.name).sort(), TOOLS.map(t => t.name).sort());
  const call = (name, args = {}) => client.callTool({ name, arguments: args });
  assert.equal(data(await call("get_tasks")).tasks.length, 2);
  assert.equal(data(await call("get_task", { id: 1 })).id, 1);
  assert.equal(data(await call("next_task")).nextTask.id, 1);
  assert.equal((await call("parse_prd", {})).isError, true);
  assert.equal((await call("get_tasks", { projectRoot: repo })).isError, true);
  assert.equal((await call("get_tasks", { constructor: "unexpected" })).isError, true);
  assert.equal((await call("get_task", { id: "1" })).isError, true);
  assert.equal((await call("set_task_status", { id: 2, status: "in-progress" })).isError, true);
  assert.equal((await call("set_task_status", { id: 1, status: "done" })).isError, true);
  assert.equal(data(await call("get_task", { id: 1 })).status, "pending");
  assert.ok(!(await call("set_task_status", { id: 1, status: "in-progress" })).isError);
  assert.ok(!(await call("set_task_status", { id: 1, status: "review" })).isError);
  await writeFile(path.join(root, "result.txt"), "verified synthetic result");
  const evidence = { taskId: 1, revision: "a".repeat(40), checks: [{ name: "Fixture output", result: "failed", artifact: "result.txt" }] };
  await writeFile(path.join(root, ".taskmaster/evidence/1.json"), JSON.stringify(evidence));
  assert.equal((await call("set_task_status", { id: 1, status: "done" })).isError, true);
  evidence.checks[0].result = "passed";
  await writeFile(path.join(root, ".taskmaster/evidence/1.json"), JSON.stringify(evidence));
  assert.ok(!(await call("set_task_status", { id: 1, status: "done" })).isError);
  assert.equal(data(await call("get_task", { id: 1 })).status, "done");
  assert.equal(data(await call("next_task")).nextTask.id, 2);
  assert.ok(!(await call("set_task_status", { id: 2, status: "in-progress" })).isError);
  assert.equal((await call("set_task_status", { id: 1, status: "pending" })).isError, true);
  await client.close();
  // Restart the real upstream server to verify persisted Taskmaster state.
  const again = new Client({ name: "jangus-reconnect", version: "1.0.0" });
  const againTransport = new StdioClientTransport({ command: process.execPath, args: [path.join(repo, "scripts/taskmaster/server.mjs"), "--project-root", root], cwd: root, stderr: "pipe" });
  t.after(() => again.close());
  await again.connect(againTransport);
  againTransport.stderr?.on("data", () => {});
  assert.equal(data(await again.callTool({ name: "get_task", arguments: { id: 1 } })).status, "done");
  assert.equal(await readFile(path.join(repo, ".taskmaster/tasks/tasks.json"), "utf8"), original);
  const bytes = Buffer.byteLength(JSON.stringify(TOOLS));
  console.log(JSON.stringify({ toolCount: TOOLS.length, schemaUtf8Bytes: bytes, roughTokenEstimate: Math.ceil(bytes / 4), basis: "bytes/4 estimate; not measured tokens" }));
});
