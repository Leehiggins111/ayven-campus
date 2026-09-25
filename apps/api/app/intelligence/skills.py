"""Agent Skills loader. Metadata is cheap; full instructions load only when selected."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    version: str
    triggers: list[str]
    body: str
    path: Path
    references: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    loaded: bool = False


def skills_root() -> Path:
    override = os.environ.get("AYVEN_SKILLS_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[4] / "skills"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text.strip()
    meta: dict = {}
    current = None
    for line in parts[1].splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and current:
            meta.setdefault(current, [])
            if isinstance(meta[current], list):
                meta[current].append(line[4:].strip())
            continue
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            current = key.strip()
            meta[current] = value.strip() or []
    return meta, parts[2].strip()


def discover() -> list[Skill]:
    """Names, descriptions, and triggers only. Bodies stay unloaded."""
    root = skills_root()
    found: list[Skill] = []
    if not root.exists():
        return found
    for path in sorted(root.glob("*/SKILL.md")):
        meta, _body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        triggers = meta.get("triggers") if isinstance(meta.get("triggers"), list) else []
        found.append(
            Skill(
                name=str(meta.get("name") or path.parent.name),
                description=str(meta.get("description") or ""),
                version=str(meta.get("version") or "0"),
                triggers=[str(t) for t in triggers],
                body="",
                path=path,
                loaded=False,
            )
        )
    return found


def load_skill(name: str) -> Skill:
    root = skills_root()
    path = root / name / "SKILL.md"
    meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    references = sorted(str(p.relative_to(path.parent)) for p in (path.parent / "references").glob("*") if p.is_file()) if (path.parent / "references").exists() else []
    scripts = sorted(str(p.relative_to(path.parent)) for p in (path.parent / "scripts").glob("*") if p.is_file()) if (path.parent / "scripts").exists() else []
    triggers = meta.get("triggers") if isinstance(meta.get("triggers"), list) else []
    return Skill(
        name=str(meta.get("name") or name),
        description=str(meta.get("description") or ""),
        version=str(meta.get("version") or "0"),
        triggers=[str(t) for t in triggers],
        body=body,
        path=path,
        references=references,
        scripts=scripts,
        loaded=True,
    )


_TASK_SKILLS = {
    "internal_door_quote": ["internal-door-quoting", "calculation", "verify-claims"],
    "football_tickets": ["football-ticket-research", "research-web", "verify-claims"],
    "vending_prospects": ["vending-prospect-research", "business-research", "verify-claims"],
    "business_research": ["business-research", "research-web", "verify-claims"],
    "web_research": ["research-web", "verify-claims"],
    "calculation": ["calculation", "verify-claims"],
    "trivial": [],
}


def select_skills(task_class: str, objective: str) -> list[Skill]:
    """Load only the skills that match this task. Never the whole catalogue."""
    names = list(_TASK_SKILLS.get(task_class, ["research-web", "verify-claims"]))
    if not names:
        return []
    objective_l = objective.lower()
    catalogue = {skill.name: skill for skill in discover()}
    # A trigger match can add one extra skill, still capped.
    for skill in catalogue.values():
        if skill.name in names:
            continue
        if any(trigger.lower() in objective_l for trigger in skill.triggers):
            names.append(skill.name)
            break
    loaded = [load_skill(name) for name in names[:3] if (skills_root() / name / "SKILL.md").exists()]
    return loaded


def skill_prompt(skills: list[Skill]) -> str:
    if not skills:
        return ""
    blocks = []
    for skill in skills:
        blocks.append(f"### Skill {skill.name} v{skill.version}\n{skill.body}")
    return "\n\n".join(blocks)
