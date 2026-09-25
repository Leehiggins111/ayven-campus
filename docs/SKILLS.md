# Skills

Format follows the Agent Skills specification (`agentskills/agentskills`, Apache-2.0): `skills/<name>/SKILL.md` plus optional `scripts/`, `references/`, and `assets/`. Ayven wrote its own skill text. Nothing was copied from `anthropics/skills`.

Discovery loads name, description, and triggers only. The body is loaded for the skills selected for that task, at most three. Scripts are not executed automatically.

| Skill | Version | When it loads |
| --- | --- | --- |
| research-web | 1.0.0 | Web research and ticket research |
| verify-claims | 1.0.0 | Every non-trivial task |
| business-research | 1.0.0 | Vending and general commercial research |
| calculation | 1.0.0 | Quotes and arithmetic |
| internal-door-quoting | 1.0.0 | Internal door jobs |
| football-ticket-research | 1.0.0 | Ticket and club jobs |
| vending-prospect-research | 1.0.0 | Vending placement |

`internal-door-quoting` states the process: per-door versus per-job versus ambiguous labour, VAT unknown unless stated, measurements, thickness, frame, handing, hardware, hinge count, fitting, delivery, consumables, and customer confirmation. It does not contain a customer price. Ambiguous labour is never resolved in silence.

Status: **REAL** as instructions loaded into the employee prompt. They do not by themselves make a stub model smarter. The calculator and the ledger enforce the door rules even when the model is stubbed.
