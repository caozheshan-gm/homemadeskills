#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


SKILL_LINE_RE = re.compile(r"^\s*-\s*(?P<name>[^:]+)\s*:\s*.*?\(file:\s*(?P<file>[^)]+)\)\s*$")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _canonical_skill_name(listed_name: str, skill_file: Path) -> str:
    normalized = listed_name.strip()
    parts = skill_file.as_posix().split("/.system/")
    if len(parts) == 2:
        maybe = parts[1]
        if maybe.endswith("/SKILL.md"):
            base = maybe[: -len("/SKILL.md")]
            if base:
                return f".system/{base}"
    return normalized


def _extract_frontmatter_description(skill_md: str) -> str | None:
    # Very small frontmatter parser: only aims to extract `description`.
    if not skill_md.startswith("---"):
        return None

    # Find the end of the first frontmatter block.
    end = skill_md.find("\n---", 3)
    if end == -1:
        return None
    fm = skill_md[3:end].splitlines()

    for i, line in enumerate(fm):
        if not line.startswith("description:"):
            continue

        value = line[len("description:") :].strip()
        if not value:
            return None

        # Handle folded/block scalars (`>` or `|`) with indented continuation lines.
        if value in (">", "|", ">-", "|-"):
            collected: list[str] = []
            for cont in fm[i + 1 :]:
                if cont.startswith("  ") or cont.startswith("\t"):
                    collected.append(cont.strip())
                else:
                    break
            joined = " ".join([s for s in collected if s])
            return joined or None

        # Strip optional quotes.
        return value.strip().strip('"').strip("'") or None

    return None


def _extract_documented_skill_headings(note_text: str) -> set[str]:
    documented: set[str] = set()
    for line in note_text.splitlines():
        if line.startswith("### "):
            documented.add(line[len("### ") :].strip())
    return documented


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate markdown stubs for skills missing from a Skill Encyclopedia note."
    )
    parser.add_argument("--agents-file", required=True, help="Path to a file containing the skills list export.")
    parser.add_argument("--note", required=True, help="Path to the encyclopedia markdown note.")
    args = parser.parse_args()

    agents_file = Path(args.agents_file).expanduser()
    note_file = Path(args.note).expanduser()

    agents_text = _read_text(agents_file)
    note_text = _read_text(note_file)

    documented = _extract_documented_skill_headings(note_text)

    parsed: list[tuple[str, Path]] = []
    for line in agents_text.splitlines():
        m = SKILL_LINE_RE.match(line)
        if not m:
            continue
        name = m.group("name").strip()
        file_path = Path(m.group("file").strip()).expanduser()
        parsed.append((name, file_path))

    if not parsed:
        print("No skill lines found in agents file. See references/agents-list-format.md for the expected format.")
        return 2

    missing_entries: list[str] = []
    missing_names: list[str] = []

    for listed_name, skill_file in parsed:
        canonical = _canonical_skill_name(listed_name, skill_file)
        if canonical in documented:
            continue

        desc = None
        if skill_file.exists() and skill_file.is_file():
            desc = _extract_frontmatter_description(_read_text(skill_file))

        missing_names.append(canonical)
        missing_entries.append(
            "\n".join(
                [
                    f"### {canonical}",
                    "",
                    f"- 适用：{desc or 'TODO（优先从 SKILL.md frontmatter description 提取）'}",
                    f"- 加载：`~/.codex/superpowers/.codex/superpowers-codex use-skill {canonical}`",
                    "- 用法：TODO",
                    "",
                ]
            )
        )

    if not missing_names:
        print("No missing skills found.")
        return 0

    print("Missing skills:")
    for name in missing_names:
        print(f"- {name}")

    print("\n---\n\nPaste stubs:\n")
    print("\n".join(missing_entries).rstrip() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

