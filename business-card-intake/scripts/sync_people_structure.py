#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TEMPLATE = ROOT / "模版" / "人物介绍.md"
DEFAULT_PEOPLE = ROOT / "商务" / "人物"
INLINE_FIELD_RE = re.compile(r"^\s*##\s*(.+?)\s*::\s*(.*)\s*$")
LEGACY_FIELD_RE = re.compile(r"^\s*##\s*(.+?)\s*:\s*$")


@dataclass
class Frontmatter:
    start: int
    end: int
    lines: list[str]


def parse_frontmatter(lines: list[str]) -> Frontmatter | None:
    if not lines or lines[0].strip() != "---":
        return None
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None
    return Frontmatter(start=0, end=end, lines=lines[1:end])


def parse_key_values(fm_lines: list[str]) -> tuple[list[str], dict[str, str]]:
    order: list[str] = []
    values: dict[str, str] = {}
    for raw in fm_lines:
        m = re.match(r"^([^:]+):(.*)$", raw)
        if not m:
            continue
        key = m.group(1).strip()
        value = m.group(2).strip()
        order.append(key)
        values[key] = value
    return order, values


def parse_rename_pairs(items: list[str], flag_name: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in items:
        if ":" not in item:
            raise SystemExit(f"Invalid {flag_name} pair '{item}', expected old:new")
        old, new = item.split(":", 1)
        old = old.strip()
        new = new.strip()
        if not old or not new:
            raise SystemExit(f"Invalid {flag_name} pair '{item}', expected old:new")
        mapping[old] = new
    return mapping


def validate_rename_targets(
    yaml_renames: dict[str, str],
    inline_renames: dict[str, str],
    tmpl_yaml_keys: list[str],
    tmpl_inline_titles: list[str],
) -> None:
    yaml_key_set = set(tmpl_yaml_keys)
    inline_title_set = set(tmpl_inline_titles)

    for old, new in yaml_renames.items():
        if new not in yaml_key_set:
            raise SystemExit(
                f"--yaml-rename target '{new}' not found in template YAML keys: {', '.join(tmpl_yaml_keys)}"
            )
        if old == new:
            continue

    for old, new in inline_renames.items():
        if new not in inline_title_set:
            raise SystemExit(
                f"--inline-rename target '{new}' not found in template inline fields: {', '.join(tmpl_inline_titles)}"
            )
        if old == new:
            continue


def template_key_order_values(template_path: Path) -> tuple[list[str], dict[str, str]]:
    lines = template_path.read_text(encoding="utf-8").splitlines()
    fm = parse_frontmatter(lines)
    if fm is None:
        raise SystemExit(f"Template frontmatter not found: {template_path}")
    return parse_key_values(fm.lines)


def template_inline_fields(template_path: Path) -> list[tuple[str, str]]:
    lines = template_path.read_text(encoding="utf-8").splitlines()
    fm = parse_frontmatter(lines)
    if fm is None:
        raise SystemExit(f"Template frontmatter not found: {template_path}")
    body = lines[fm.end + 1 :]
    fields: list[tuple[str, str]] = []
    for line in body:
        m = INLINE_FIELD_RE.match(line)
        if not m:
            continue
        title = m.group(1).strip()
        default_value = m.group(2).strip()
        fields.append((title, default_value))
    return fields


def reorder_frontmatter(
    original_fm_lines: list[str],
    tmpl_order: list[str],
    tmpl_values: dict[str, str],
    yaml_renames: dict[str, str],
) -> list[str]:
    old_order, old_values = parse_key_values(original_fm_lines)
    if yaml_renames:
        renamed_order: list[str] = []
        renamed_values: dict[str, str] = {}
        for key in old_order:
            target = yaml_renames.get(key, key)
            renamed_order.append(target)
            value = old_values.get(key, "")
            # Prefer explicit target value if already present and non-empty.
            if target in renamed_values and renamed_values[target]:
                continue
            renamed_values[target] = value
        old_order, old_values = renamed_order, renamed_values

    final_order = list(tmpl_order)
    out: list[str] = []
    for key in final_order:
        if key in old_values:
            value = old_values[key]
        else:
            value = tmpl_values.get(key, "")
        out.append(f"{key}: {value}" if value else f"{key}:")
    return out


def sync_inline_field_structure(
    body_lines: list[str],
    tmpl_inline_fields: list[tuple[str, str]],
    inline_renames: dict[str, str],
) -> list[str]:
    if not tmpl_inline_fields:
        return body_lines

    existing_values: dict[str, str] = {}
    kept: list[str] = []
    first_insert_pos: int | None = None
    i = 0
    while i < len(body_lines):
        line = body_lines[i]
        m = INLINE_FIELD_RE.match(line)
        if m:
            if first_insert_pos is None:
                first_insert_pos = len(kept)
            title = m.group(1).strip()
            value = m.group(2).strip()
            title = inline_renames.get(title, title)
            existing_values[title] = value
            i += 1
            continue

        m2 = LEGACY_FIELD_RE.match(line)
        if m2:
            if first_insert_pos is None:
                first_insert_pos = len(kept)
            title = m2.group(1).strip()
            title = inline_renames.get(title, title)
            value = ""
            if i + 1 < len(body_lines):
                nxt = body_lines[i + 1].strip()
                if nxt.startswith("![[") and nxt.endswith("]]"):
                    value = nxt
                    i += 2
                    existing_values[title] = value
                    continue
            existing_values[title] = value
            i += 1
            continue

        kept.append(line)
        i += 1

    synced_lines: list[str] = []
    for title, default_value in tmpl_inline_fields:
        value = existing_values.get(title, default_value)
        synced_lines.append(f"## {title}:: {value}".rstrip())

    if first_insert_pos is None:
        for idx, line in enumerate(kept):
            if line.strip() == "---":
                first_insert_pos = idx
                break
    if first_insert_pos is None:
        first_insert_pos = len(kept)

    return kept[:first_insert_pos] + synced_lines + kept[first_insert_pos:]


def process_note(
    path: Path,
    tmpl_order: list[str],
    tmpl_values: dict[str, str],
    tmpl_inline_fields: list[tuple[str, str]],
    yaml_renames: dict[str, str],
    inline_renames: dict[str, str],
    apply: bool,
) -> bool:
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines()
    fm = parse_frontmatter(lines)
    if fm is None:
        return False

    new_fm_lines = reorder_frontmatter(
        fm.lines,
        tmpl_order,
        tmpl_values,
        yaml_renames=yaml_renames,
    )
    body_lines = lines[fm.end + 1 :]
    new_body_lines = sync_inline_field_structure(
        body_lines,
        tmpl_inline_fields,
        inline_renames=inline_renames,
    )

    rebuilt = ["---", *new_fm_lines, "---", *new_body_lines]
    new_text = "\n".join(rebuilt).rstrip() + "\n"
    changed = new_text != original
    if changed and apply:
        path.write_text(new_text, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync existing people notes structure from 模版/人物介绍 (strict YAML keys + inline fields ## 标题::)"
    )
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--people-dir", default=str(DEFAULT_PEOPLE))
    parser.add_argument(
        "--yaml-rename",
        action="append",
        default=[],
        metavar="OLD:NEW",
        help="Rename YAML key before sync; repeatable",
    )
    parser.add_argument(
        "--inline-rename",
        action="append",
        default=[],
        metavar="OLD:NEW",
        help="Rename inline field title (## 标题::) before sync; repeatable",
    )
    parser.add_argument("--apply", action="store_true", help="Write changes in-place")
    args = parser.parse_args()

    template = Path(args.template)
    people_dir = Path(args.people_dir)
    if not template.exists():
        raise SystemExit(f"Template not found: {template}")
    if not people_dir.exists():
        raise SystemExit(f"People dir not found: {people_dir}")

    order, tmpl_values = template_key_order_values(template)
    inline_fields = template_inline_fields(template)
    yaml_renames = parse_rename_pairs(args.yaml_rename, "--yaml-rename")
    inline_renames = parse_rename_pairs(args.inline_rename, "--inline-rename")
    validate_rename_targets(
        yaml_renames=yaml_renames,
        inline_renames=inline_renames,
        tmpl_yaml_keys=order,
        tmpl_inline_titles=[title for title, _ in inline_fields],
    )
    notes = sorted(p for p in people_dir.glob("*.md") if p.is_file())
    changed = 0
    for note in notes:
        if process_note(
            note,
            order,
            tmpl_values,
            tmpl_inline_fields=inline_fields,
            yaml_renames=yaml_renames,
            inline_renames=inline_renames,
            apply=args.apply,
        ):
            changed += 1
            print(f"UPDATE {note}")

    mode = "Applied" if args.apply else "Dry-run"
    print(f"{mode}: changed {changed} / {len(notes)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
