import { readFile, realpath, access, stat } from "node:fs/promises";
import path from "node:path";

export const UPSTREAM_TOOLS = ["get_task", "get_tasks", "next_task", "set_task_status"];
export const STATUSES = ["pending", "in-progress", "review", "done", "blocked", "deferred", "cancelled"];
export class PolicyError extends Error {}

export function taskId(value) {
  const number = typeof value === "string" && /^[1-9][0-9]*$/.test(value) ? Number(value) : value;
  if (!Number.isSafeInteger(number) || number < 1) throw new PolicyError("Task IDs must be positive integers.");
  return number;
}

export async function readJson(file) {
  try { return JSON.parse(await readFile(file, "utf8")); }
  catch { throw new PolicyError("Required project JSON is missing or invalid."); }
}

export async function insideFile(root, relative) {
  if (path.isAbsolute(relative)) throw new PolicyError("Expected a repository-relative evidence file.");
  const target = await realpath(path.resolve(root, relative)).catch(() => null);
  const base = await realpath(root);
  if (!target || path.relative(base, target).startsWith("..") || path.isAbsolute(path.relative(base, target)) || !(await stat(target)).isFile()) {
    throw new PolicyError("Evidence must be an existing file inside the project.");
  }
  return target;
}

export async function validateProfile(root) {
  const config = await readJson(path.join(root, ".taskmaster/config.json"));
  if (config.global?.anonymousTelemetry !== false || config.global?.enableProxy !== false ||
      config.storage?.type !== "file" || config.global?.defaultTag !== "master") {
    throw new PolicyError("Taskmaster requires telemetry/proxy disabled and local file storage.");
  }
  for (const role of ["main", "research", "fallback"]) {
    const model = config.models?.[role];
    if (model?.provider !== "ollama" ||
        model.baseURL !== "http://127.0.0.1:11434/api" ||
        typeof model.modelId !== "string" || !model.modelId.trim()) {
      throw new PolicyError("All planning roles must use an explicit local Ollama configuration.");
    }
  }
  if (config.global.ollamaBaseURL !== "http://127.0.0.1:11434/api") {
    throw new PolicyError("Taskmaster endpoint must remain loopback.");
  }
  for (const file of [".env", ".taskmasterconfig"]) {
    if (await access(path.join(root, file)).then(() => true, () => false)) {
      throw new PolicyError("Remove automatic environment/legacy configuration from the project.");
    }
  }
}

export async function validateEvidence(root, id) {
  const file = await insideFile(root, `.taskmaster/evidence/${id}.json`);
  const evidence = await readJson(file);
  if (evidence.taskId !== id || !/^[0-9a-f]{40}$/.test(evidence.revision ?? "") ||
      !Array.isArray(evidence.checks) || evidence.checks.length === 0) {
    throw new PolicyError("Completion requires task-matched evidence, a commit, and passed checks.");
  }
  for (const check of evidence.checks) {
    if (typeof check.name !== "string" || !check.name.trim() ||
        check.result !== "passed" || typeof check.artifact !== "string") {
      throw new PolicyError("Every completion check needs a name, passed result, and artifact.");
    }
    if (!check.artifact.startsWith("https://github.com/jenozu/jangus/")) {
      await insideFile(root, check.artifact);
    }
  }
  return evidence;
}

export async function readBacklog(root, { evidence = true } = {}) {
  const document = await readJson(path.join(root, ".taskmaster/tasks/tasks.json"));
  const rawTasks = document.master?.tasks;
  if (!Array.isArray(rawTasks) || rawTasks.length === 0) throw new PolicyError("Backlog must contain master tasks.");
  // v0.43.1 writes numeric IDs as strings; normalize only this in-memory view.
  const tasks = rawTasks.map(task => ({ ...task, id: taskId(task.id),
    dependencies: Array.isArray(task.dependencies) ? task.dependencies.map(taskId) : task.dependencies }));
  const ids = new Set();
  for (const task of tasks) {
    if (!Number.isSafeInteger(task.id) || task.id < 1 || ids.has(task.id)) throw new PolicyError("Task IDs must be unique positive integers.");
    ids.add(task.id);
    for (const field of ["title", "description", "details", "testStrategy"]) {
      if (typeof task[field] !== "string" || !task[field].trim()) throw new PolicyError("Tasks need scope and acceptance/test instructions.");
    }
    if (!STATUSES.includes(task.status) || !Array.isArray(task.dependencies) ||
        !Array.isArray(task.subtasks)) throw new PolicyError("Invalid task status, dependencies, or subtasks.");
  }
  const visiting = new Set(), visited = new Set(), byId = new Map(tasks.map(t => [t.id, t]));
  function visit(id) {
    if (visiting.has(id)) throw new PolicyError("Task dependencies contain a cycle.");
    if (visited.has(id)) return;
    visiting.add(id);
    for (const dep of byId.get(id).dependencies) {
      if (!ids.has(dep)) throw new PolicyError("Task dependency does not exist.");
      visit(dep);
    }
    visiting.delete(id); visited.add(id);
  }
  for (const task of tasks) {
    visit(task.id);
    if (task.status === "done") {
      if (task.dependencies.some(id => byId.get(id).status !== "done")) throw new PolicyError("Completed task has unfinished dependencies.");
      if (task.subtasks.some(t => t.status !== "done")) throw new PolicyError("Completed task has unfinished subtasks.");
      if (evidence) await validateEvidence(root, task.id);
    }
  }
  return tasks;
}

export function cleanEnvironment(root) {
  const permitted = new Set(["path", "home", "userprofile", "appdata", "localappdata", "systemroot", "windir", "comspec", "temp", "tmp", "lang", "lc_all"]);
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => permitted.has(key.toLowerCase())));
  return { ...env, TASK_MASTER_PROJECT_ROOT: root, TASK_MASTER_TOOLS: UPSTREAM_TOOLS.join(","),
    TASKMASTER_TAG: "master", TASK_MASTER_ALLOW_METADATA_UPDATES: "false", NODE_ENV: "production" };
}
