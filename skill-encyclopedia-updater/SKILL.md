---
name: skill-encyclopedia-updater
description: Use when updating an Obsidian “skill百科全书/Skill Encyclopedia” markdown note after installing/removing skills or changing skill paths/descriptions, and you want a consistent entry per skill (heading + 适用/加载/用法) without adding extra notes.
---

# Skill Encyclopedia Updater

## Overview

Keep `skill百科全书.md` in sync with the skills currently available in your Codex session (the “Available skills” list in `AGENTS.md` or a pasted export).

## Inputs

- Target encyclopedia note (Obsidian Markdown), default in this vault:
  - `embrace chaos/skill百科全书.md`
- Skills source (pick one):
  - Preferred: a file that contains the “Available skills” bullet list (often `AGENTS.md`)
  - Fallback: paste the list into a temporary text file and point the script at it

## Workflow (fast)

### Step 1: Generate “missing skill” stubs (no file edits)

Run:

```bash
python skills/skill-encyclopedia-updater/scripts/generate_missing_entries.py \
  --agents-file "<path-to-agents-or-export.txt>" \
  --note "embrace chaos/skill百科全书.md"
```

This prints:
- Which skills are missing from the encyclopedia note (based on `### <skill-name>` headings)
- Markdown stubs you can paste in

### Step 2: Paste stubs into the right section

Rules:
- Only touch the encyclopedia note unless the user explicitly asks for more.
- Preserve existing style; add the smallest possible text for each new entry.
- Prefer using the skill’s own `SKILL.md` frontmatter `description` for `- 适用：…` (avoid guessing).

Recommended entry template:

```md
### <skill-name>

- 适用：<from SKILL.md description, or 1 sentence you verify>
- 加载：`~/.codex/superpowers/.codex/superpowers-codex use-skill <skill-name>`
- 用法：<1–3 bullets, only if you can state them confidently; otherwise leave TODO>
```

### Step 3: Final consistency check

Re-run Step 1. Expected: “No missing skills found.”

Also verify:
- Frontmatter `date:` is today (or remove the field if you don’t want it drifting)
- No duplicate `### <skill>` headings
- Code fences are balanced

## Common pitfalls

- **Wrong name for `.system/*` skills**: if the skill file lives under `.../.system/<name>/SKILL.md`, the encyclopedia heading and `use-skill` name should be `.system/<name>`.
- **Inventing “用法”**: if you didn’t read the skill’s docs, keep it minimal (or leave a TODO).
- **Creating extra plan notes**: don’t add `docs/plans/*` unless explicitly requested.
