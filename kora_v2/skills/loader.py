"""Skill loader for Kora V2.

Reads skill YAML files from the skills directory and provides
tool gating: given a list of active skill names, return the union
of all tools those skills expose.
"""

from __future__ import annotations

from pathlib import Path

import structlog
import yaml
from pydantic import BaseModel

logger = structlog.get_logger()

_DEFAULT_SKILLS_DIR = Path(__file__).parent


class Skill(BaseModel):
    """A loaded skill definition."""

    name: str
    display_name: str = ""
    tools: list[str] = []
    discovery_tools: list[str] = []
    guidance: str = ""
    agent: str | None = None  # which on-demand agent this activates


class SkillLoader:
    """Load skill YAML files and provide tool gating.

    Usage::

        loader = SkillLoader()
        loader.load_all()

        # Get tools for the currently active skills
        tools = loader.get_active_tools(["web_research", "life_management"])
    """

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._skills_dir = skills_dir or _DEFAULT_SKILLS_DIR
        self._skills: dict[str, Skill] = {}

    # ── Loading ───────────────────────────────────────────────────────

    def load_all(self) -> None:
        """Load every ``*.yaml`` file in the skills directory."""
        if not self._skills_dir.is_dir():
            logger.warning(
                "skills.dir_missing",
                path=str(self._skills_dir),
            )
            return

        for path in sorted(self._skills_dir.glob("*.yaml")):
            try:
                self.load_skill(path)
            except Exception:  # noqa: BLE001
                logger.exception("skills.load_failed", path=str(path))

    def load_skill(self, path: Path) -> Skill:
        """Load a single skill YAML file and register it.

        Returns the loaded Skill model.
        """
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}

        skill = Skill(
            name=data.get("name", path.stem),
            display_name=data.get("display_name", ""),
            tools=data.get("tools", []),
            discovery_tools=data.get("discovery_tools", []),
            guidance=data.get("guidance", ""),
            agent=data.get("agent"),
        )

        self._skills[skill.name] = skill
        logger.debug(
            "skills.loaded",
            skill=skill.name,
            tools=len(skill.tools),
            agent=skill.agent,
        )
        return skill

    # ── Lookups ───────────────────────────────────────────────────────

    def get_skill(self, name: str) -> Skill | None:
        """Return a skill by name, or None."""
        return self._skills.get(name)

    def get_all_skills(self) -> list[Skill]:
        """Return every loaded skill."""
        return list(self._skills.values())

    def get_active_tools(self, active_skills: list[str]) -> list[str]:
        """Return the union of tool names for the given active skills.

        Includes both regular tools and discovery tools.
        """
        tools: list[str] = []
        seen: set[str] = set()

        for skill_name in active_skills:
            skill = self._skills.get(skill_name)
            if skill is None:
                logger.warning("skills.unknown_active", skill=skill_name)
                continue

            for tool_name in skill.tools + skill.discovery_tools:
                if tool_name not in seen:
                    seen.add(tool_name)
                    tools.append(tool_name)

        return tools

    def get_skill_for_agent(self, agent_name: str) -> Skill | None:
        """Find the skill whose ``agent`` field matches *agent_name*."""
        for skill in self._skills.values():
            if skill.agent == agent_name:
                return skill
        return None

    def get_guidance(self, skill_name: str) -> str:
        """Return guidance text for a skill, or empty string."""
        skill = self._skills.get(skill_name)
        return skill.guidance if skill else ""
