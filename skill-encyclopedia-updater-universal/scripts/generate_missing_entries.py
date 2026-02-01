#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


CODEX_AGENTS_LINE_RE = re.compile(
    r"^\s*-\s*(?P<name>[^:]+)\s*:\s*.*?\(file:\s*(?P<file>[^)]+)\)\s*$"
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _canonical_skill_name(listed_name: str, skill_file: Path | None) -> str:
    normalized = listed_name.strip()
    if not skill_file:
        return normalized
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


def _parse_skills_source_auto(skills_text: str) -> list[tuple[str, Path | None]]:
    """
    Returns a list of (skill_name, skill_doc_path?) tuples.

    Supported formats:
    - Codex AGENTS.md style:
      `- skill-name: ... (file: /abs/path/to/SKILL.md)`
    - Plain names list (one per line), including bullets:
      `skill-name`
      `- skill-name`
      `* skill-name`
    """
    parsed: list[tuple[str, Path | None]] = []

    # Prefer Codex format if at least one line matches.
    codex_matches = 0
    for line in skills_text.splitlines():
        m = CODEX_AGENTS_LINE_RE.match(line)
        if not m:
            continue
        codex_matches += 1
        name = m.group("name").strip()
        file_path = Path(m.group("file").strip()).expanduser()
        parsed.append((name, file_path))

    if codex_matches:
        return parsed

    for raw in skills_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue

        # Drop simple bullet markers.
        if line.startswith(("- ", "* ")):
            line = line[2:].strip()

        # Drop trailing punctuation commonly used in lists.
        line = line.rstrip(":").strip()

        if not line:
            continue

        parsed.append((line, None))

    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate markdown stubs for skills missing from a Skill Encyclopedia note."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--skills-file",
        help="Path to a skills list (Codex AGENTS.md export OR a plain list of skill names).",
    )
    group.add_argument(
        "--agents-file",
        help="DEPRECATED alias for --skills-file (kept for backwards compatibility).",
    )
    parser.add_argument("--note", required=True, help="Path to the encyclopedia markdown note.")
    parser.add_argument(
        "--include-codex-load",
        action="store_true",
        help="Include a Codex/superpowers `- 加载：... use-skill <name>` line in generated stubs.",
    )
    args = parser.parse_args()

    skills_file = Path((args.skills_file or args.agents_file)).expanduser()
    note_file = Path(args.note).expanduser()

    skills_text = _read_text(skills_file)
    note_text = _read_text(note_file)

    documented = _extract_documented_skill_headings(note_text)

    parsed = _parse_skills_source_auto(skills_text)

    if not parsed:
        print("No skills found. See references/agents-list-format.md for supported formats and examples.")
        return 2

    missing_entries: list[str] = []
    missing_names: list[str] = []

    for listed_name, skill_file in parsed:
        canonical = _canonical_skill_name(listed_name, skill_file)
        if canonical in documented:
            continue

        desc = None
        if skill_file and skill_file.exists() and skill_file.is_file():
            desc = _extract_frontmatter_description(_read_text(skill_file))

        missing_names.append(canonical)
        doc_line = f"- 文档：`{skill_file}`" if skill_file else "- 文档：TODO（补充文档路径/链接）"
        load_line = (
            f"- 加载：`~/.codex/superpowers/.codex/superpowers-codex use-skill {canonical}`"
            if args.include_codex_load
            else None
        )
        lines = [
            f"### {canonical}",
            "",
            f"- 适用：{desc or 'TODO（优先从技能文档/说明里提取一句话）'}",
            doc_line,
        ]
        if load_line:
            lines.append(load_line)
        lines.extend(["- 用法：TODO", ""])
        missing_entries.append(
            "\n".join(lines)
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
