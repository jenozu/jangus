import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { fileURLToPath } from "node:url";
import { cleanEnvironment, validateProfile, readBacklog, validateEvidence, UPSTREAM_TOOLS, STATUSES, PolicyError, taskId } from "./policy.mjs";

export const TOOLS = [
  { name: "get_tasks", description: "Read Jangus development tasks, optionally by status.",
    inputSchema: { type: "object", properties: { status: { type: "string", enum: STATUSES } }, additionalProperties: false } },
  { name: "get_task", description: "Read one Jangus development task by its top-level ID.",
    inputSchema: { type: "object", properties: { id: { type: "integer", minimum: 1 } }, required: ["id"], additionalProperties: false } },
  { name: "next_task", description: "Find ready development work using Taskmaster dependencies. Phase authorization still applies.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false } },
  { name: "set_task_status", description: "Record development progress. Done requires passed evidence in .taskmaster/evidence/ID.json and completed dependencies.",
    inputSchema: { type: "object", properties: { id: { type: "integer", minimum: 1 }, status: { type: "string", enum: STATUSES } }, required: ["id", "status"], additionalProperties: false } },
];

export async function connectAdapter(root) {
  await validateProfile(root);
  await readBacklog(root);
  const client = new Client({ name: "jangus-taskmaster-adapter", version: "0.1.0" });
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [fileURLToPath(new URL("./upstream.mjs", import.meta.url))],
    cwd: root, env: cleanEnvironment(root), stderr: "pipe",
  });
  // Drain provider logs locally without forwarding payloads to the MCP client.
  transport.stderr?.on("data", () => {});
  try {
    await client.connect(transport, { timeout: 30000 });
    transport.stderr?.on("data", () => {});
    const listed = await client.listTools();
    if (JSON.stringify(listed.tools.map(t => t.name).sort()) !== JSON.stringify([...UPSTREAM_TOOLS].sort())) {
      throw new PolicyError("Upstream MCP tool list changed; refusing expanded access.");
    }
  } catch (error) { await client.close(); throw error; }
  let queue = Promise.resolve();
  async function call(name, args = {}) {
    const tool = TOOLS.find(t => t.name === name);
    if (!tool || !args || typeof args !== "object" || Array.isArray(args) ||
        Object.keys(args).some(k => !Object.hasOwn(tool.inputSchema.properties, k))) {
      throw new PolicyError("Unsupported tool or arguments.");
    }
    if ("id" in tool.inputSchema.properties && (!Number.isSafeInteger(args.id) || args.id < 1)) throw new PolicyError("A positive integer task ID is required.");
    if ((name === "set_task_status" || args.status !== undefined) && !STATUSES.includes(args.status)) throw new PolicyError("Unsupported task status.");
    await validateProfile(root);
    const tasks = await readBacklog(root);
    const task = tasks.find(t => t.id === args.id);
    if (args.id && !task) throw new PolicyError("Task does not exist.");
    if (name === "set_task_status") {
      if (["in-progress", "done"].includes(args.status) &&
          task.dependencies.some(id => tasks.find(t => t.id === id).status !== "done")) {
        throw new PolicyError("Finish dependencies before starting or completing this task.");
      }
      if (args.status === "done") {
        if (task.subtasks.some(t => t.status !== "done")) throw new PolicyError("Finish subtasks before completion.");
        await validateEvidence(root, args.id);
      } else if (task.status === "done" && tasks.some(t => t.dependencies.includes(task.id) && ["in-progress", "done"].includes(t.status))) {
        throw new PolicyError("Reopening this dependency requires reviewing active dependents first.");
      }
    }
    const result = await client.callTool({
      name, arguments: { ...args, ...(args.id ? { id: String(args.id) } : {}), projectRoot: root, tag: "master" },
    }, undefined, { timeout: 30000 });
    if (result.isError) throw new PolicyError("Taskmaster rejected the operation.");
    const payload = JSON.parse(result.content.find(block => block.type === "text").text);
    let data = payload.data;
    if (name === "get_tasks") {
      data = { tasks: data.tasks.map(({ id, title, status, dependencies, metadata }) => ({
        id: taskId(id), title, status, dependencies: dependencies.map(taskId), phase: metadata?.phase,
      })), stats: data.stats };
    } else if (name === "next_task") {
      // Do not forward upstream workflow suggestions for tools outside this profile.
      data = { nextTask: data.nextTask ? { ...data.nextTask, id: taskId(data.nextTask.id) } : null, isSubtask: data.isSubtask };
    } else if (name === "get_task") {
      data = { ...data, id: taskId(data.id), dependencies: data.dependencies.map(taskId) };
    }
    return { content: [{ type: "text", text: JSON.stringify({ data, taskmasterVersion: "0.43.1" }) }] };
  }
  return {
    call(name, args) {
      const result = queue.then(() => call(name, args));
      queue = result.catch(() => {});
      return result;
    },
    close: () => client.close(),
  };
}
