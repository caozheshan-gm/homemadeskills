---
name: business-card-intake
description: Use when processing business-card images from 商务/图/未处理名片, renaming files by detected person name, moving cards to 商务/图/名片, and creating person notes from 模版/人物介绍 with YAML extracted from the card.
---

# Business Card Intake

## Overview
Use this skill to batch-process business card images with script-first reliability.

Pipeline:
1. Read images from `商务/图/未处理名片`
2. OCR detect person name and rename file to that name
3. Move renamed image to `商务/图/名片`
4. Create note in `商务/人物/<人物名>.md` from `模版/人物介绍.md`
5. Fill YAML fields from OCR text when available, leave unknown fields empty
6. Insert card image under `## 照片`

## Run

```bash
python3 skills/business-card-intake/scripts/process_cards.py
```

Useful options:

```bash
python3 skills/business-card-intake/scripts/process_cards.py --dry-run
python3 skills/business-card-intake/scripts/process_cards.py --backend tesseract
python3 skills/business-card-intake/scripts/process_cards.py --overwrite-notes
```

## Paths
- Input: `商务/图/未处理名片`
- Output images: `商务/图/名片`
- Output notes: `商务/人物`
- Template: `模版/人物介绍.md`

## Outputs
The script writes:
- `商务/图/名片/card_intake_log_YYYYMMDD_HHMMSS.csv`
- `商务/图/名片/card_intake_undo_YYYYMMDD_HHMMSS.sh`

Use the undo script to roll back image moves/renames quickly if needed.
