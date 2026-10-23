// Launch only the locked, installed upstream server. stdout is reserved for MCP.
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";
import "./offline.mjs";
import { validateProfile, readJson } from "./policy.mjs";

const root = process.env.TASK_MASTER_PROJECT_ROOT;
if (!root || !path.isAbsolute(root)) throw new Error("An absolute Taskmaster project is required.");
await validateProfile(root);
process.chdir(root);
console.log = console.info = console.debug = (...args) => console.error(...args);
const require = createRequire(import.meta.url);
const manifest = require.resolve("task-master-ai/package.json");
if ((await readJson(manifest)).version !== "0.43.1") throw new Error("Unexpected Taskmaster version.");
await import(pathToFileURL(path.join(path.dirname(manifest), "dist/mcp-server.js")).href);
