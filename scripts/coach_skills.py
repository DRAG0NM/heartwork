#!/usr/bin/env python3
"""Deterministic skill registry and routing for coach tasks."""

from __future__ import annotations

import json
import os

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SKILLS_DIR = os.path.join(ROOT, "skills")


def registry():
    with open(os.path.join(SKILLS_DIR, "registry.json"), encoding="utf-8") as handle:
        return json.load(handle)["skills"]


def route(task, query=""):
    """Pick 1-2 skills: trigger hits first, then the task default, in stable order."""
    skills = registry()
    text = (query or "").lower()
    scored = []
    for name, spec in skills.items():
        if task not in spec["tasks"]:
            continue
        score = sum(1 for word in spec["triggers"] if word.lower() in text)
        if task in spec["default_for"]:
            score += 0.5
        if score > 0:
            scored.append((-score, name))
    scored.sort()
    picked = [name for _, name in scored[:2]]
    if not picked:
        picked = sorted(name for name, spec in skills.items() if task in spec["default_for"])[:1]
    if not picked:
        picked = ["coach-recovery"]
    return picked


def skill_text(name):
    if name not in registry():
        raise KeyError("unknown skill: %s" % name)
    with open(os.path.join(SKILLS_DIR, name, "SKILL.md"), encoding="utf-8") as handle:
        return handle.read()


def list_skills():
    return [{"name": name, "description": spec["description"], "tasks": spec["tasks"]}
            for name, spec in sorted(registry().items())]
