import path from "node:path";
import { fileURLToPath } from "node:url";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { connectAdapter, TOOLS } from "./adapter.mjs";
import { PolicyError } from "./policy.mjs";

const args = process.argv.slice(2);
if (args.length && (args.length !== 2 || args[0] !== "--project-root" || !path.isAbsolute(args[1]))) {
  throw new Error("Usage: node server.mjs [--project-root ABSOLUTE_PROJECT]");
}
const root = args[1] ?? fileURLToPath(new URL("../../", import.meta.url));
const adapter = await connectAdapter(root);
const server = new Server({ name: "jangus-taskmaster", version: "0.1.0" }, { capabilities: { tools: {} } });
server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));
server.setRequestHandler(CallToolRequestSchema, async request => {
  try { return await adapter.call(request.params.name, request.params.arguments); }
  catch (error) {
    return { content: [{ type: "text", text: error instanceof PolicyError ? error.message : "Taskmaster operation failed; check the local setup." }], isError: true };
  }
});
let closing = false;
async function close() {
  if (closing) return;
  closing = true;
  await adapter.close();
  await server.close();
}
process.on("SIGINT", () => { void close().then(() => process.exit(0)); });
process.on("SIGTERM", () => { void close().then(() => process.exit(0)); });
process.stdin.on("end", () => { void close().then(() => process.exit(0)); });
await server.connect(new StdioServerTransport());
