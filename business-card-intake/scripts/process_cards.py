#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from shutil import which

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = ROOT / "商务" / "图" / "未处理名片"
LEGACY_INPUT = ROOT / "商务" / "未处理名片"
DEFAULT_OUTPUT = ROOT / "商务" / "图" / "名片"
DEFAULT_PEOPLE = ROOT / "商务" / "人物"
DEFAULT_TEMPLATE = ROOT / "模版" / "人物介绍.md"
WIKI_IMAGE_PREFIX = "商务/图/名片"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


@dataclass(frozen=True)
class OCRObs:
    text: str
    confidence: float
    bbox_h: float


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


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


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
        candidate = dir_path / f"{stem}{suffix}" if index == 1 else dir_path / f"{stem}_{index}{suffix}"
        if candidate not in reserved and not candidate.exists():
            reserved.add(candidate)
            return candidate
        index += 1


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]", " ", name)
    name = re.sub(r"\s+", " ", name).strip().strip(". ")
    return name or "未识别"


def detect_tesseract_langs(tesseract_path: str) -> set[str]:
    proc = run([tesseract_path, "--list-langs"])
    if proc.returncode != 0:
        return set()
    out: set[str] = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("List of available languages"):
            continue
        out.add(line)
    return out


def ocr_image_tesseract(path: Path, tesseract_path: str, lang: str) -> list[OCRObs]:
    proc = run([tesseract_path, str(path), "-", "-l", lang, "--psm", "6", "tsv"])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "tesseract failed")

    rows = [r for r in proc.stdout.splitlines() if r.strip()]
    if not rows:
        return []
    header = rows[0].split("\t")
    idx = {name: i for i, name in enumerate(header)}
    required = {"level", "block_num", "par_num", "line_num", "word_num", "left", "height", "conf", "text"}
    if not required.issubset(idx):
        raise RuntimeError("Unexpected tesseract TSV format")

    lines: dict[tuple[int, int, int], list[tuple[int, float, float, str]]] = {}
    for r in rows[1:]:
        cols = r.split("\t")
        try:
            if int(cols[idx["level"]]) != 5:
                continue
            key = (int(cols[idx["block_num"]]), int(cols[idx["par_num"]]), int(cols[idx["line_num"]]))
            word_num = int(cols[idx["word_num"]])
            left = int(cols[idx["left"]])
            height = float(cols[idx["height"]])
            conf = float(cols[idx["conf"]])
            text = cols[idx["text"]].strip()
        except Exception:
            continue
        if not text:
            continue
        lines.setdefault(key, []).append((left + word_num, height, conf, text))

    observations: list[OCRObs] = []
    for words in lines.values():
        words.sort(key=lambda x: x[0])
        kept = [t for _, _, c, t in words if c >= 35.0]
        text = " ".join(kept).strip() or " ".join(t for _, _, _, t in words).strip()
        if not text:
            continue
        heights = [h for _, h, _, _ in words]
        confs = [c for _, _, c, _ in words if c >= 0]
        observations.append(
            OCRObs(
                text=text,
                confidence=(sum(confs) / len(confs) / 100.0) if confs else 0.0,
                bbox_h=max(heights) if heights else 0.0,
            )
        )
    return observations


def looks_like_company_or_role(s: str) -> bool:
    up = s.upper()
    bad = [
        "有限公司", "公司", "集团", "科技", "经理", "总监", "工程师", "顾问", "销售",
        "PHONE", "MOBILE", "FAX", "EMAIL", "ADDRESS", "SUITE", "ROAD", "ROW", "STREET",
        "INC", "LLC", "LTD", "COMPANY", "GROUP", "ENGINEER", "MANAGER", "DIRECTOR", "SALES",
    ]
    return any(x in up for x in bad)


def extract_chinese_name(text: str) -> str | None:
    candidates = re.findall(r"[\u4e00-\u9fff]{2,4}", text)
    candidates = [c for c in candidates if not looks_like_company_or_role(c)]
    return min(candidates, key=len) if candidates else None


def extract_english_name(text: str) -> str | None:
    cleaned = re.sub(r"[^A-Za-z .'-]", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None
    words = cleaned.split(" ")
    stop = {"Phone", "Mobile", "Fax", "Email", "Tel", "Office", "Direct"}
    while words and words[-1] in stop:
        words.pop()

    def is_token(w: str) -> bool:
        return bool(
            re.fullmatch(r"[A-Z][a-z]+(?:[-'][A-Za-z]+)?", w)
            or re.fullmatch(r"[A-Z]{2,}", w)
            or re.fullmatch(r"[A-Z]\.?,?", w)
        )

    name_tokens: list[str] = []
    for w in words:
        if w in stop or not is_token(w):
            break
        name_tokens.append(w.rstrip(","))
        if len(name_tokens) >= 3:
            break
    if len(name_tokens) < 2:
        return None
    candidate = " ".join(name_tokens)
    return None if looks_like_company_or_role(candidate) else candidate


def english_name_quality(name: str) -> int:
    count = len([x for x in name.split(" ") if x])
    if count == 2:
        return 10
    if count == 3:
        return 9
    return 0


def pick_name(observations: list[OCRObs]) -> tuple[str | None, str, float, float]:
    best_ch: tuple[str, float, float, float] | None = None
    best_en: tuple[str, float, float, float] | None = None

    for obs in observations:
        line = obs.text.strip()
        if not line:
            continue
        if _CJK_RE.search(line):
            name = extract_chinese_name(line)
            if name:
                score = 10000.0 + obs.bbox_h + obs.confidence * 100.0
                if not best_ch or score > best_ch[3]:
                    best_ch = (name, obs.confidence, obs.bbox_h, score)
        else:
            name = extract_english_name(line)
            if name:
                score = english_name_quality(name) * 1000.0 + obs.bbox_h + obs.confidence * 100.0
                if not best_en or score > best_en[3]:
                    best_en = (name, obs.confidence, obs.bbox_h, score)

    if best_ch:
        return best_ch[0], "chinese", best_ch[1], best_ch[2]
    if best_en:
        return best_en[0], "english", best_en[1], best_en[2]
    return None, "none", 0.0, 0.0


def normalize_phone(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


def shell_quote_yaml(value: str) -> str:
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'


def compact_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def collect_text_lines(observations: list[OCRObs]) -> list[str]:
    out: list[str] = []
    for obs in observations:
        text = compact_line(obs.text)
        if text and text not in out:
            out.append(text)
    return out


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

    webs = re.findall(r"(?:https?://)?(?:www\.)?[A-Za-z0-9.-]+\.(?:com|net|org|cn|co|io|biz|us)", joined, flags=re.I)
    if webs:
        first = webs[0]
        if not (fields["email"] and fields["email"].lower().endswith(first.lower())):
            fields["web"] = first

    label_map = {
        "phone": ["phone", "tel", "电话", "office", "直线"],
        "mobile": ["mobile", "cell", "手机"],
        "fax": ["fax", "传真"],
    }
    phone_pattern = re.compile(r"\+?\d[\d\s().-]{6,}\d")

    for line in lines:
        low = line.lower()
        nums = phone_pattern.findall(line)
        for num in nums:
            target = "phone"
            for key, keys in label_map.items():
                if any(k in low for k in keys):
                    target = key
                    break
            if not fields[target]:
                fields[target] = normalize_phone(num)

    addr_keywords = ["address", "suite", "road", "row", "street", "avenue", "blvd", "地址", "省", "市", "区"]
    addrs = [ln for ln in lines if any(k in ln.lower() for k in addr_keywords)]
    if addrs:
        fields["address"] = " ; ".join(addrs[:2])

    role_keywords = ["sales", "manager", "director", "engineer", "consultant", "president", "ceo", "经理", "总监", "工程师", "销售"]
    for ln in lines:
        low = ln.lower()
        if any(k in low for k in role_keywords) and not re.search(r"phone|mobile|fax|email|address|地址", low):
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
        return not any(k in low for k in blocked) and len(line) >= 3

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
    out: list[str] = []
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
            out.append(line)
            continue

        if in_fm:
            m = re.match(r"^([^:]+):(.*)$", line)
            if m:
                key = m.group(1).strip()
                if key in fields:
                    val = fields[key].strip()
                    out.append(f"{key}: {shell_quote_yaml(val)}" if val else f"{key}:")
                    continue
        out.append(line)

    if not (fm_started and fm_ended):
        keys = ["type", "tags", "aliases", "company", "branch", "name", "email", "web", "phone", "mobile", "fax", "address", "国籍", "职位", "性别", "年龄"]
        fm = ["---"]
        for key in keys:
            val = fields.get(key, "").strip()
            fm.append(f"{key}: {shell_quote_yaml(val)}" if val else f"{key}:")
        fm.append("---")
        return "\n".join(fm + [""] + lines).rstrip() + "\n"

    return "\n".join(out).rstrip() + "\n"


def inject_image_under_photo(text: str, embed: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    inserted = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        is_photo_field = (
            stripped.startswith("## 照片")
            or stripped.startswith("## 图片")
            or stripped.startswith("照片::")
            or stripped.startswith("图片::")
        )

        if not inserted and is_photo_field:
            # Keep inline-field behavior: image goes immediately after `::` on the same line.
            if "::" in line:
                prefix = line.split("::", 1)[0] + "::"
                out.append(f"{prefix} {embed}")
            else:
                # Backward compatibility with old `照片:` style templates.
                out.append(re.sub(r":\s*$", "::", line) + f" {embed}")
            inserted = True

            # If an older version already inserted the image on the next line, skip it.
            if i + 1 < len(lines) and lines[i + 1].strip().startswith("![["):
                i += 2
                continue
            i += 1
            continue

        out.append(line)
        i += 1

    if not inserted:
        out.extend(["", f"## 照片:: {embed}"])
    return "\n".join(out).rstrip() + "\n"


def choose_backend(preferred: str) -> tuple[str, str | None, str]:
    tesseract_path = which("tesseract")
    backend = preferred
    if backend == "auto":
        backend = "tesseract" if tesseract_path else "none"
    if backend == "tesseract" and not tesseract_path:
        raise SystemExit("tesseract not found; install tesseract or use existing filenames")

    lang = "eng"
    if backend == "tesseract" and tesseract_path:
        langs = detect_tesseract_langs(tesseract_path)
        if "chi_sim" in langs:
            lang = "chi_sim+eng"
        elif "chi_tra" in langs:
            lang = "chi_tra+eng"
    return backend, tesseract_path, lang


def main() -> int:
    parser = argparse.ArgumentParser(description="Process business cards into renamed images and person notes")
    parser.add_argument("--input-dir", default=None)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--people-dir", default=str(DEFAULT_PEOPLE))
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--backend", default="auto", choices=["auto", "tesseract"])
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

    images = sorted(p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS and not p.name.startswith("."))
    if not images:
        print(f"No images found in {input_dir}")
        return 0

    backend, tesseract_path, tess_lang = choose_backend(args.backend)
    template_text = template_path.read_text(encoding="utf-8")
    today = dt.date.today().isoformat()

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = output_dir / f"card_intake_log_{ts}.csv"
    undo_path = output_dir / f"card_intake_undo_{ts}.sh"

    reserved_targets: set[Path] = set()
    results: list[CardResult] = []

    for src in images:
        obs: list[OCRObs] = []
        method = "none"
        conf = 0.0
        bbox_h = 0.0
        picked_name: str | None = None

        try:
            if backend == "tesseract":
                obs = ocr_image_tesseract(src, tesseract_path or "tesseract", tess_lang)
                picked_name, method, conf, bbox_h = pick_name(obs)
        except Exception:
            picked_name = None
            method = "error"

        if not picked_name:
            picked_name = src.stem

        person_name = sanitize_filename(picked_name)
        dst = unique_path(output_dir, person_name, src.suffix.lower(), reserved_targets)
        note_path = people_dir / f"{dst.stem}.md"

        lines = collect_text_lines(obs)
        fields = extract_contact_fields(lines, dst.stem)
        fields["name"] = dst.stem

        content = template_text.replace("{{date}}", today)
        content = fill_frontmatter(content, fields)
        embed = f"![[{WIKI_IMAGE_PREFIX}/{dst.name}]]"
        content = inject_image_under_photo(content, embed)

        results.append(CardResult(src, dst, dst.stem, backend, method, conf, bbox_h, note_path))
        print(f"PLAN {src.name} -> {dst.name} / NOTE {note_path.name}")

        if args.dry_run:
            continue

        src.rename(dst)
        if note_path.exists() and not args.overwrite_notes:
            continue
        note_path.write_text(content, encoding="utf-8")

    with log_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["src", "dst", "name", "backend", "method", "confidence", "bbox_h", "note_path"])
        for row in results:
            w.writerow([str(row.src), str(row.dst), row.person_name, row.backend, row.method, f"{row.confidence:.4f}", f"{row.bbox_h:.4f}", str(row.note_path)])

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
