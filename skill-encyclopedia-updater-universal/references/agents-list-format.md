# Skills list parsing format

The helper script supports **two** formats.

## Format A: Codex AGENTS.md export

Lines like:

```md
- skill-name: some description... (file: /abs/path/to/SKILL.md)
```

Notes:
- The script ignores lines that don’t match this pattern.
- If the `file:` path contains `/.system/<name>/SKILL.md`, the generated entry uses `.system/<name>` as the canonical skill name.

## Format B: Plain names list

One skill name per line, optionally with bullets:

```txt
skill-a
- skill-b
* skill-c
```
