# lablab.ai submission text (copy/paste)

**Project title:** GlassBox: Sandboxed Explainable-ML Agent

**Short description (<=160 chars):**
An agent that writes and runs ML explainability code inside throwaway Vultr sandboxes, then proves every number with a receipt.

**Long description:**
GlassBox turns a CSV and a plain-English question into a verified explainable-ML report. The agent (Vultr Serverless Inference) writes a scikit-learn script, a policy gate screens it, and it runs inside a disposable container on a Vultr VM: no network, read-only filesystem, no secrets, capped CPU/RAM/PIDs/time, optional gVisor. Failures are fed back so the agent self-heals. The explanation is generated only from the executed JSON, and a claim checker flags any number that does not trace back to real output. Every run ends with a receipt: SHA-256 of code and data, the exact isolation flags, and confirmation the container was destroyed. A built-in containment demo launches six hostile programs (egress, filesystem write, secret read, fork bomb, memory bomb, infinite loop) and shows each one contained.

**Tags:** Vultr, Serverless Inference, Docker, gVisor, FastAPI, scikit-learn, Explainable AI, Agents, Sandboxing

**Demo video script (~2.5 min):** 0:00 problem (agents that only chat) → 0:20 upload sample CSV, run → 1:00 timeline with a self-healed failure, importance chart, claim-checked explanation → 1:35 receipt → 1:50 CONTAINMENT MOMENT: run containment demo, narrate each row → 2:25 architecture slide + Vultr services.

**Team name:** Team GlassBox
**Team description (>=20 chars):** Building GlassBox, a sandboxed explainable-ML agent on Vultr (FastAPI, Docker/gVisor, Vultr Serverless Inference). Looking for 2 teammates: one frontend/UX and one DevOps/security reviewer.
