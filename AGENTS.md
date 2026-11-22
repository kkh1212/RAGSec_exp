# Independent rag_defense workspace

- Work only inside this project, including environments, caches, temporary files, and results.
- Never locate, read, import, modify, install into, or operate on the other RAG project.
- Do not change Git state, global configuration, system Python, or existing processes.
- Execute sequentially; no agents, parallel tests, servers, daemons, GPU installs, or model downloads.
- Use this project's `.venv/bin/python`. Keep caches in `.cache/`, temporary files in `.tmp/`, numerical threads at 1.
- Default to mock; never make live API calls without a later explicit user instruction.
- Synthetic/replay outcomes are software checks, not real attack mitigation measurements.
- Do not put answer keys or attack labels into engine inputs or adapter contracts.
- Keep generation settings fixed. Only validated, freshly reproduced, grounded responses may replace an initial refusal.
