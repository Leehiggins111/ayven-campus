# Changelog

## 1.0.0 — 2026-09-25

Intelligence engine. Architecture and local tests are in. Real Qwen validation is not.

- Planner, selective skills, structured tools, evidence research, claim ledger, critic, verifier
- Supervisor audit that can accept, return, take over, or escalate without copying the employee
- Manager orchestration with approval and escalation disabled by default
- Deterministic door scenarios that keep labour per-door versus per-job unresolved
- Model registry. Calculator for arithmetic. Frontier calls stay off when `AYVEN_ALLOW_ESCALATION=0`
- SQLite migrations add plans, skills, tool calls, claims, verification, and quality without dropping v0.4 rows
- GPU harness runs the same loop and archives results before the pod is stopped

## 0.2.1 — 2026-09-24

- In-world canvas billboard labels
- Plaza ring, radial paths, perimeter trees

## 0.2.0 — 2026-09-24

- Multi-building HQ campus (R3F)
- Camera focus, inspector, command board, approval chrome

## 0.1.1 — 2026-09-24

- Campus served at GET / and /campus

## 0.1.0 — 2026-09-24

- Initial monorepo: API + campus + docs
- Football-trips MVP path
