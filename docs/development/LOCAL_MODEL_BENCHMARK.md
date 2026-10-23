# Phase 5 — local model selection (Taskmaster task 12)

Task 12 requires **real target-hardware measurements**, not a mock or CI success. Phase 4 remains the verified deterministic baseline. Do not select a model yet.

## Measure on Windows

Confirm installed RAM, free RAM, CPU, GPU/VRAM, disk space, Windows version, Ollama version and model digest (`ollama list`). Pull an exact candidate text-model tag. Use local Ollama with no cloud provider credentials; keep one inference active.

Run from the checkout in PowerShell:

```powershell
uv sync --locked
ollama list
uv run --locked python scripts/benchmark_ollama.py --model "EXACT_LOCAL_MODEL_TAG" --output "$env:USERPROFILE\jangus-local-benchmark.json"
```

The script sends nine bounded synthetic JSON requests sequentially to loopback `127.0.0.1:11434`. It checks only exact response JSON, records elapsed time and Ollama-reported token/duration counts when available, and never saves model response text. It cannot measure machine-wide peak RAM/VRAM or demonstrate a real multi-step task. Failed requests count as failures, not missing samples. Keep output outside Git.

Record actual peak process memory, system RAM/VRAM pressure, model footprint, first-token/cold-load behavior, processor use, swapping, stability and timeouts **separately** with the same candidate/context/settings. Compare correctness first and then latency and resource needs. Include exact model digest and machine environment in private evidence. Do not call estimates measured values.

Taskmaster AI planning is not enabled by this script. Evaluate it separately on a disposable backlog only after explicit approval. Conclude task 12 only after real hardware evidence, a reviewed choice of model and placement, and a Taskmaster completion record; tasks 13–17 and A09–A13 remain pending. No paid/cloud fallback.
