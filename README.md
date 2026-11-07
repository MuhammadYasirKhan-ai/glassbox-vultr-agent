# GlassBox: a sandboxed, explainable-ML agent on Vultr

> Hackathon track: **Vultr Agent Rush, Challenge 1: Blast Radius Zero** (Pattern A: sandboxed code execution)

GlassBox takes a CSV and a plain-English goal. An LLM (Vultr Serverless Inference) writes a scikit-learn
explainability script. The script runs **only inside a throwaway, locked-down container on a Vultr VM**.
You get real executed results (model score vs baseline, permutation feature importance, chart), a
plain-English explanation whose **every number is claim-checked against the executed output**, and a
**receipt** (SHA-256 of code and data, the exact isolation flags, container destroyed: yes/no).

## Architecture

```
Browser ──> Caddy :80/443 ──> FastAPI control layer (Vultr VM, systemd)
                                │  1. LLM plans/writes code  ──> Vultr Serverless Inference
                                │  2. Policy gate (AST allow/deny)           [layer 1]
                                │  3. docker run (gVisor runsc if installed) [layer 2]
                                │       --network none --read-only --cap-drop ALL
                                │       --pids-limit 64 --memory 512m --cpus 1
                                │       --user nobody, no secrets, input mounted :ro
                                │  4. Errors fed back to the LLM (self-healing, max 3 tries)
                                │  5. Explanation generated from the verified JSON only
                                └  6. Claim check + receipt (container destroyed verified by docker inspect)
```

Why the API runs on the host (not in Docker with the socket mounted): mounting `/var/run/docker.sock` into
the API container would give it host-root equivalence. Running the control plane under systemd and the
untrusted code in per-run containers keeps the blast radius at zero.

## Containment checklist (matches the challenge brief)

| Requirement | Implementation |
|---|---|
| Process isolation | Per-run container, optional gVisor, non-root user, all caps dropped |
| Secret hygiene | API key lives only in the control-plane env; sandbox env is empty (demo proves it) |
| Resource limits | 25 s wall clock, 512 MB RAM (no swap), 1 CPU, 64 PIDs, 3 MB output cap, 96 MB tmpfs |
| Lifecycle discipline | `--rm` plus forced `docker rm -f`; `docker inspect` verifies it is gone; reported in receipt |
| Verifiable output | Receipt + claim checker flags any number not present in executed output |

**Containment moment for the video:** click *Run containment demo*. Six hostile programs (network egress,
write to `/etc`, read secrets from env, fork bomb, 4 GB memory bomb, infinite loop) run with the policy gate
bypassed. The table shows each contained and each container destroyed.

## Vultr services used
- **Vultr Cloud Compute** VM: control layer, sandbox host, public web app
- **Vultr Serverless Inference**: all agent LLM calls (code generation, explanation)
- (optional) gVisor runtime installed on the VM for stronger isolation

## Quick start (local, needs Docker running)
```bash
cp .env.example .env            # add GEMINI_API_KEY (free) or the Vultr key + model
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
./run.sh                        # Windows: run.bat   -> http://127.0.0.1:8000
pytest                          # 21 tests, incl. the real in-container harness with a known-good ML script
```
Windows tip: use Docker Desktop, never add `--reload` (it breaks subprocesses). WSL2 is even smoother.

### Free setup with Gemini (no billing)
1. Get a free key at https://aistudio.google.com/apikey and put it in `.env` as `GEMINI_API_KEY`.
2. Keep `DS_LLM_MODEL=gemini-3.5-flash-lite`. With `LLM_PROVIDER=auto` (default) Gemini is used until a
   Vultr inference key and model are set, then Vultr is used automatically.
3. Free tier is rate limited; the client retries 429/503 with backoff.

Challenge 1 judging expects agent LLM calls on **Vultr Serverless Inference**, so before the final demo
video set `VULTR_INFERENCE_API_KEY` and `VULTR_MODEL` (the $200 hackathon credit covers it).

Windows (cmd): `set GEMINI_API_KEY=your_key` and `set DS_LLM_MODEL=gemini-3.5-flash-lite`, then run uvicorn.

## Deploy on Vultr
1. Create an Ubuntu 24.04 Cloud Compute VM (2 vCPU / 4 GB is enough), SSH in as root.
2. `git clone <this repo> && cd glassbox && cp .env.example .env && nano .env`
3. `bash deploy/setup_vultr.sh` then open `http://<VM_IP>/` and check `/healthz`.

## What is in the repo
`app/` FastAPI control layer (orchestrator, policy gate, sandbox runner, LLM clients, claim checker, containment demo) ·
`sandbox/` locked-down image + in-container harness · `static/` UI (no inline JS/CSS, strict CSP) ·
`deploy/setup_vultr.sh` one-shot VM setup (Docker, gVisor if it works, systemd, Caddy with streaming) ·
`tests/` unit + API + streaming + harness e2e · `.github/workflows/ci.yml` ruff, mypy --strict, pytest.

## Limits and honesty notes
- Docker-dependent code paths (`app/sandbox.py`) are verified by the end-to-end run on the VM, not by unit tests.
- Permutation importance shows what the model relies on, not causation; the explanation says so.
- Sample data is synthetic (`samples/churn.csv`).
- The claim checker verifies numbers, not prose semantics.
- Rate limiting is in-memory and per-process.
