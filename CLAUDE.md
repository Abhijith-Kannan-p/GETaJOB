# GETaJOB - working rules
- Free tier only. Never add a paid dependency. Free-tier LLM prompts may be used by the provider, so never send personal data.
- Never print, read, or commit secrets. Do not open .env files; refer to env var names only.
- Do not invent features, data, users, metrics or logos. If something doesn't exist in the code, say so and propose how to build it.
- Target host: Render free web service (512 MB RAM, 0.1 CPU, ephemeral disk, spins down). One uvicorn worker. No local embedding/ML models in the runtime image.
- Every LLM call goes through one wrapper with a timeout, bounded retries and token logging.
- Tests must never call a real LLM or embedding API (use fakes).
- Write everything fresh in this repo. Do not read or copy from other projects on this machine.
- Git: never run git write commands (add, commit, push, branch, checkout, switch, reset, rebase, merge, tag, stash, remote). Read-only git (status, diff, log) is fine. Never add Co-Authored-By lines or "Generated with Claude" text anywhere. After each checkpoint, print the exact commit-and-push commands for me to run myself, starting with a root check: git rev-parse --show-toplevel must print C:/Users/kabhi/GETaJOB.
