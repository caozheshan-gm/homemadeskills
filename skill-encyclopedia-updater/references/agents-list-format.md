# Skills list parsing format

The helper script expects a text file containing lines like:

```md
- skill-name: some description... (file: /abs/path/to/SKILL.md)
```

Notes:
- The script ignores lines that don’t match this pattern.
- If the `file:` path contains `/.system/<name>/SKILL.md`, the generated entry uses `.system/<name>` as the canonical skill name.

