#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.rename_business_cards import (  # noqa: E402
    OCR_SWIFT,
    IMAGE_EXTS,
    OCRObs,
    detect_tesseract_langs,
    ensure_vision_ocr_binary,
    ocr_image_tesseract,
    ocr_image_vision,
    pick_name,
    sanitize_filename,
)

DEFAULT_INPUT = ROOT / "商务" / "图" / "未处理名片"
LEGACY_INPUT = ROOT / "商务" / "未处理名片"
DEFAULT_OUTPUT = ROOT / "商务" / "图" / "名片"
DEFAULT_PEOPLE = ROOT / "商务" / "人物"
DEFAULT_TEMPLATE = ROOT / "模版" / "人物介绍.md"
WIKI_IMAGE_PREFIX = "商务/图/名片"


@dataclass
class CardResult:
    src: Path
    dst: Path
    person_name: str
    backend: str
    method: str
    confidence: float
    bbox_h: float
    note_path: Path


def choose_input_dir(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise SystemExit(f"Input dir not found: {p}")
        return p
    for candidate in (DEFAULT_INPUT, LEGACY_INPUT):
        if candidate.exists():
            return candidate
    raise SystemExit(f"Input dir not found: {DEFAULT_INPUT} (or legacy {LEGACY_INPUT})")


def unique_path(dir_path: Path, stem: str, suffix: str, reserved: set[Path]) -> Path:
    index = 1
    while True:
        if index == 1:
            candidate = dir_path / f"{stem}{suffix}"
        else:
            candidate = dir_path / f"{stem}_{index}{suffix}"
        if candidate not in reserved and not candidate.exists():
            reserved.add(candidate)
            return candidate
        index += 1


def normalize_phone(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s


def shell_quote_yaml(value: str) -> str:
    if not value:
        return ""
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'


def compact_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def collect_text_lines(observations: Iterable[OCRObs]) -> list[str]:
    lines: list[str] = []
    for obs in observations:
        line = compact_line(obs.text)
        if not line:
            continue
        if line not in lines:
            lines.append(line)
    return lines


def extract_contact_fields(lines: list[str], person_name: str) -> dict[str, str]:
    fields = {
        "company": "",
        "branch": "",
        "name": person_name,
        "email": "",
        "web": "",
        "phone": "",
        "mobile": "",
        "fax": "",
        "address": "",
        "国籍": "",
        "职位": "",
        "性别": "",
        "年龄": "",
    }

    joined = "\n".join(lines)

    emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", joined)
    if emails:
        fields["email"] = emails[0]

    web_candidates: list[str] = []
    web_candidates += re.findall(r"(?:https?://)?(?:www\.)?[A-Za-z0-9.-]+\.(?:com|net|org|cn|co|io|biz|us)", joined, flags=re.I)
    if web_candidates:
        web = web_candidates[0]
        if fields["email"] and fields["email"].lower().endswith(web.lower()):
            pass
        else:
            fields["web"] = web

    label_map = {
        "phone": ["phone", "tel", "电话", "office", "直线"],
        "mobile": ["mobile", "cell", "手机"],
        "fax": ["fax", "传真"],
    }

    phone_pattern = re.compile(r"\+?\d[\d\s().-]{6,}\d")

    def assign_if_empty(target_key: str, value: str) -> None:
        if not fields[target_key]:
            fields[target_key] = normalize_phone(value)

    for line in lines:
        low = line.lower()
        nums = phone_pattern.findall(line)
        if not nums:
            continue
        for num in nums:
            num_clean = normalize_phone(num)
            assigned = False
            for key, keywords in label_map.items():
                if any(k in low for k in keywords):
                    assign_if_empty(key, num_clean)
                    assigned = True
                    break
            if not assigned:
                assign_if_empty("phone", num_clean)

    addr_keywords = ["address", "suite", "road", "row", "street", "avenue", "blvd", "地址", "省", "市", "区"]
    address_lines = [ln for ln in lines if any(k in ln.lower() for k in addr_keywords)]
    if address_lines:
        fields["address"] = " ; ".join(address_lines[:2])

    role_keywords = [
        "sales", "manager", "director", "engineer", "consultant", "president", "ceo",
        "销售", "经理", "总监", "工程师", "顾问", "总裁", "主任", "老板",
    ]
    for ln in lines:
        low = ln.lower()
        if any(k in low for k in role_keywords) and not re.search(r"phone|mobile|fax|email|地址|address", low):
            fields["职位"] = ln
            break

    nationality_map = ["usa", "canada", "mexico", "china", "japan", "korea", "德国", "法国", "中国", "美国"]
    for ln in lines:
        low = ln.lower()
        if "@" in low or "http" in low or re.search(r"\d", low):
            continue
        if any(k in low for k in nationality_map):
            fields["国籍"] = ln
            break

    def maybe_company(line: str) -> bool:
        low = line.lower()
        if person_name and person_name.lower() in low:
            return False
        if fields["email"] and fields["email"].lower() in low:
            return False
        if re.search(r"@|\d{3,}", line):
            return False
        blocked = ["phone", "mobile", "fax", "address", "suite", "road", "row", "tel", "邮箱", "电话", "网址"]
        if any(k in low for k in blocked):
            return False
        return len(line) >= 3

    company_candidates = [ln for ln in lines[:6] if maybe_company(ln)]
    if company_candidates:
        fields["company"] = company_candidates[0]

    if not fields["address"]:
        zip_addr = [ln for ln in lines if re.search(r"\b\d{5}(?:-\d{4})?\b", ln)]
        if zip_addr:
            fields["address"] = zip_addr[0]

    return fields


def fill_frontmatter(template_text: str, fields: dict[str, str]) -> str:
    lines = template_text.splitlines()
    output: list[str] = []
    in_fm = False
    fm_started = False
    fm_ended = False

    for line in lines:
        if line.strip() == "---":
            if not fm_started:
                fm_started = True
                in_fm = True
            elif in_fm:
                in_fm = False
                fm_ended = True
            output.append(line)
            continue

        if in_fm:
            m = re.match(r"^([^:]+):(.*)$", line)
            if m:
                key = m.group(1).strip()
                if key in fields:
                    val = fields[key].strip()
                    output.append(f"{key}: {shell_quote_yaml(val)}" if val else f"{key}:")
                    continue
        output.append(line)

    if not (fm_started and fm_ended):
        fm = ["---"]
        for k in ["type", "tags", "aliases", "company", "branch", "name", "email", "web", "phone", "mobile", "fax", "address", "国籍", "职位", "性别", "年龄"]:
            if k in fields:
                val = fields[k].strip()
                fm.append(f"{k}: {shell_quote_yaml(val)}" if val else f"{k}:")
            else:
                fm.append(f"{k}:")
        fm.append("---")
        return "\n".join(fm + [""] + lines).rstrip() + "\n"

    return "\n".join(output).rstrip() + "\n"


def inject_image_under_photo(template_text: str, embed_line: str) -> str:
    lines = template_text.splitlines()
    out: list[str] = []
    inserted = False
    for i, line in enumerate(lines):
        out.append(line)
        stripped = line.strip()
        if not inserted and (stripped.startswith("## 照片") or stripped.startswith("## 图片")):
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if next_line != embed_line:
                out.append(embed_line)
            inserted = True

    if not inserted:
        out.append("")
        out.append("## 照片:")
        out.append(embed_line)

    return "\n".join(out).rstrip() + "\n"


def choose_backend(preferred: str) -> tuple[str, str | None, Path | None, str]:
    from shutil import which

    tesseract_path = which("tesseract")
    vision_bin = ensure_vision_ocr_binary()

    backend = preferred
    if backend == "auto":
        backend = "tesseract" if tesseract_path else "vision"

    if backend == "tesseract" and not tesseract_path:
        backend = "vision"
    if backend == "vision" and not (vision_bin or OCR_SWIFT.exists()):
        backend = "tesseract"

    tess_lang = "eng"
    if backend == "tesseract" and tesseract_path:
        langs = detect_tesseract_langs(tesseract_path)
        if "chi_sim" in langs:
            tess_lang = "chi_sim+eng"
        elif "chi_tra" in langs:
            tess_lang = "chi_tra+eng"

    return backend, tesseract_path, vision_bin, tess_lang


def ocr_observations(img: Path, backend: str, tesseract_path: str | None, vision_bin: Path | None, tess_lang: str) -> list[OCRObs]:
    if backend == "tesseract":
        return ocr_image_tesseract(img, tesseract_path or "tesseract", tess_lang)
    if vision_bin:
        return ocr_image_vision(img, [str(vision_bin)])
    return ocr_image_vision(img, ["swift", str(OCR_SWIFT)])


def main() -> int:
    parser = argparse.ArgumentParser(description="Process business cards into renamed images and person notes")
    parser.add_argument("--input-dir", default=None, help="Input cards dir (default: 商务/图/未处理名片)")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--people-dir", default=str(DEFAULT_PEOPLE))
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--backend", default="auto", choices=["auto", "tesseract", "vision"])
    parser.add_argument("--overwrite-notes", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    input_dir = choose_input_dir(args.input_dir)
    output_dir = Path(args.output_dir)
    people_dir = Path(args.people_dir)
    template_path = Path(args.template)

    if not template_path.exists():
        raise SystemExit(f"Template not found: {template_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    people_dir.mkdir(parents=True, exist_ok=True)

    images = sorted(
        p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS and not p.name.startswith(".")
    )
    if not images:
        print(f"No images found in {input_dir}")
        return 0

    backend, tesseract_path, vision_bin, tess_lang = choose_backend(args.backend)
    template_text = template_path.read_text(encoding="utf-8")
    today = dt.date.today().isoformat()

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = output_dir / f"card_intake_log_{ts}.csv"
    undo_path = output_dir / f"card_intake_undo_{ts}.sh"

    reserved_targets: set[Path] = set()
    results: list[CardResult] = []

    for src in images:
        try:
            obs = ocr_observations(src, backend, tesseract_path, vision_bin, tess_lang)
            picked_name, method, conf, bbox_h = pick_name(obs)
        except Exception:
            obs = []
            picked_name, method, conf, bbox_h = None, "error", 0.0, 0.0

        if not picked_name:
            picked_name = src.stem

        person_name = sanitize_filename(picked_name)
        dst = unique_path(output_dir, person_name, src.suffix.lower(), reserved_targets)
        note_path = Path(people_dir) / f"{dst.stem}.md"

        lines = collect_text_lines(obs)
        fields = extract_contact_fields(lines, dst.stem)
        fields["name"] = dst.stem

        content = template_text.replace("{{date}}", today)
        content = fill_frontmatter(content, fields)
        embed = f"![[{WIKI_IMAGE_PREFIX}/{dst.name}]]"
        content = inject_image_under_photo(content, embed)

        results.append(
            CardResult(
                src=src,
                dst=dst,
                person_name=dst.stem,
                backend=backend,
                method=method,
                confidence=conf,
                bbox_h=bbox_h,
                note_path=note_path,
            )
        )

        print(f"PLAN {src.name} -> {dst.name} / NOTE {note_path.name}")

        if args.dry_run:
            continue

        src.rename(dst)
        if note_path.exists() and not args.overwrite_notes:
            continue
        note_path.write_text(content, encoding="utf-8")

    with log_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "src", "dst", "name", "backend", "method", "confidence", "bbox_h", "note_path"
        ])
        for row in results:
            w.writerow([
                str(row.src),
                str(row.dst),
                row.person_name,
                row.backend,
                row.method,
                f"{row.confidence:.4f}",
                f"{row.bbox_h:.4f}",
                str(row.note_path),
            ])

    with undo_path.open("w", encoding="utf-8") as f:
        f.write("#!/bin/sh\nset -e\n")
        for row in results:
            f.write(f"mv {shlex.quote(str(row.dst))} {shlex.quote(str(row.src))}\n")
            f.write(f"rm -f {shlex.quote(str(row.note_path))}\n")
    os.chmod(undo_path, 0o755)

    if args.dry_run:
        print(f"\nDry run only. Log: {log_path}")
        print(f"Undo script: {undo_path}")
        return 0

    print(f"\nDone. Processed: {len(results)}")
    print(f"Log: {log_path}")
    print(f"Undo script: {undo_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
