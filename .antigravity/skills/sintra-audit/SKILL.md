# Sintra Audit Skill

## Purpose
Audit and improve this codebase step by step without breaking the current flow.

## Use this skill when asked to:
- trace code flow
- map workflows
- find bugs
- improve request lifecycle
- stabilize chat, workflows, memory, and persistence

## Instructions
- First inspect the real call flow.
- Do not rewrite large parts before mapping dependencies.
- Prefer small, verifiable changes.
- Keep UI, orchestrator, DB, memory, and workflows aligned.
- After each change, explain what changed and what must be tested.

## Required output
For every audit or refactor task, return:
1. exact runtime flow
2. state ownership map
3. bug list
4. refactor plan
5. verification checklist