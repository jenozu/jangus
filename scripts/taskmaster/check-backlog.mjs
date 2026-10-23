import { fileURLToPath } from "node:url";
import { readBacklog, validateProfile } from "./policy.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
await validateProfile(root);
const tasks = await readBacklog(root);
for (const [prefix, count, key] of [["FR", 16, "requirements"], ["NFR", 8, "requirements"], ["A", 17, "acceptance"]]) {
  for (let number = 1; number <= count; number++) {
    const id = prefix + String(number).padStart(2, "0");
    if (!tasks.some(t => t.metadata?.[key]?.includes(id))) throw new Error(`Missing backlog coverage: ${id}`);
  }
}
console.log(`PASS: ${tasks.length} tasks, valid dependency DAG, completion evidence, all FR/NFR/MVP references covered.`);
