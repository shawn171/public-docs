#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import shutil
from pathlib import Path
from pypdf import PdfReader


def compute_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_parse_cache(cache_path: Path) -> dict[str, dict[str, object]]:
    if not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                str(k): v
                for k, v in data.items()
                if isinstance(v, dict)
                and isinstance(v.get("sha256"), str)
                and isinstance(v.get("records"), list)
            }
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_parse_cache(cache_path: Path, cache: dict[str, dict[str, object]]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def format_legislation(text: str) -> str:
    parts: list[str] = []
    if "2014/53/EU" in text:
        parts.append("RED 2014/53/EU")
    if "2014/30/EU" in text:
        parts.append("EMC 2014/30/EU")
    rohs_codes = [code for code in ("2011/65/EU", "2015/863/EU") if code in text]
    if rohs_codes:
        parts.append("RoHS " + " · ".join(rohs_codes))
    if not parts:
        directives = list(dict.fromkeys(re.findall(r"\b\d{4}/\d+/EU\b", text)))
        return " · ".join(directives)
    return " · ".join(parts)


def format_published_date(text: str) -> str:
    match = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})\b",
        text,
    )
    if not match:
        return ""
    parsed = dt.datetime.strptime(match.group(0), "%B %d, %Y").date()
    return f"{parsed.day} {parsed.strftime('%B %Y')}"


def extract_model_entries(text: str, fallback_stem: str) -> list[tuple[str, str]]:
    model_section = re.search(
        r"\bModel\s*[:：]?\s*\|?\s*([^\n]+)",
        text,
        flags=re.I,
    )
    if not model_section:
        fallback_code = fallback_stem.split("_")[0].upper()
        return [(fallback_code, f"{fallback_code} series")]

    raw_line = model_section.group(1).strip()
    parts = [p.strip() for p in re.split(r"\s*(?:/|,|\n)\s*", raw_line) if p.strip()]

    # 若最後一項帶有 "series" 尾碼，為前面純型號代碼的項目補上相同的 "series" 尾碼
    trailing_suffix = ""
    if parts:
        suffix_match = re.search(r"\b(series)\s*$", parts[-1], flags=re.I)
        if suffix_match:
            trailing_suffix = suffix_match.group(1).lower()

    entries: list[tuple[str, str]] = []
    seen_codes: set[str] = set()

    for idx, part in enumerate(parts):
        code_match = re.match(r"^([A-Za-z0-9][A-Za-z0-9.-]*)", part)
        if not code_match:
            continue
        code = code_match.group(1).upper()
        if code in seen_codes:
            continue
        seen_codes.add(code)

        label = part
        if (
            trailing_suffix
            and idx < len(parts) - 1
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", part)
        ):
            label = f"{part} {trailing_suffix}"

        entries.append((code, label))

    if entries:
        return entries

    fallback_code = fallback_stem.split("_")[0].upper()
    return [(fallback_code, raw_line or f"{fallback_code} series")]


def extract_family(text: str) -> str:
    prod_match = re.search(r"\bProduct\s*[:：]?\s*\|?\s*([^\n|]+)", text, flags=re.I)
    if prod_match:
        raw_prod = prod_match.group(1).strip()
        if re.search(r"\bRFID\s+Reader\b", raw_prod, flags=re.I):
            return "RFID Reader"
        if raw_prod:
            return raw_prod.title() if raw_prod.isupper() else raw_prod

    if re.search(r"\bRFID\s+Reader\b", text, flags=re.I):
        return "RFID Reader"
    if re.search(r"\bDISPLAY\b", text, flags=re.I):
        return "Display"
    return ""


def parse_pdf_to_records(pdf_path: Path) -> list[dict[str, str]]:
    text = extract_pdf_text(pdf_path)
    model_entries = extract_model_entries(text, pdf_path.stem)
    family = extract_family(text)
    published = format_published_date(text)
    legislation = format_legislation(text)
    document_name = f"GIGA-TMS_{pdf_path.stem}_EU_Doc"

    records: list[dict[str, str]] = []
    for model_code, full_label in model_entries:
        records.append({
            "models": model_code,
            "label": full_label,
            "family": family,
            "published": published,
            "applicable_legislation": legislation,
            "source_file": pdf_path.name,
            "download_file": pdf_path.name,
            "document": document_name,
        })
    return records


def render_model_article(rec: dict[str, str], web_doc_dir: str) -> str:
    esc = html.escape
    model = rec["models"].strip().upper()
    model_id = f"model-{esc(model.lower())}"
    letter = esc(model[0])
    label = esc(rec["label"])
    published = esc(rec["published"])
    legislation = esc(rec["applicable_legislation"])
    document = esc(rec["document"])
    family = esc(rec["family"])
    target_pdf = rec["source_file"]
    pdf_href = f"/{web_doc_dir.strip('/')}/{esc(target_pdf)}"

    return (
        f'          <article class="model-record" id="{model_id}">\n'
        f'            <span class="model-letter">{letter}</span>\n'
        f'            <div class="record-main">\n'
        f'              <div class="record-heading"><h3>{label}</h3><span class="record-status">SIGNED · APPROVED</span></div>\n'
        f'              <p>EU Declaration of Conformity · {published}</p>\n'
        f'              <dl class="record-meta"><div class="applicable-legislation"><dt>Applicable legislation</dt><dd>{legislation}</dd></div><div><dt>Document</dt><dd>{document}</dd></div><div><dt>Family</dt><dd>{family}</dd></div></dl>\n'
        f'            </div>\n'
        f'            <a class="record-download" href="{pdf_href}" download>Download PDF <b>↓</b></a>\n'
        f'          </article>'
    )


def update_index_html(index_path: Path, records: list[dict[str, str]], web_doc_dir: str) -> None:
    content = index_path.read_text(encoding="utf-8")

    grouped: dict[str, list[str]] = {ch: [] for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
    for rec in sorted(records, key=lambda r: r["models"].casefold()):
        first_ch = rec["models"].strip().upper()[0]
        if first_ch in grouped:
            grouped[first_ch].append(render_model_article(rec, web_doc_dir))

    blocks: list[str] = []
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        letter_id = f"letter-{ch.lower()}"
        articles = grouped[ch]
        if articles:
            joined = "\n".join(articles)
            blocks.append(f'        <div id="{letter_id}">\n{joined}\n        </div>')
        else:
            blocks.append(f'        <div id="{letter_id}"></div>')

    letters_html = "\n".join(blocks)
    pattern = re.compile(
        r'(<nav class="az-nav" aria-label="A-Z model index">.*?</nav>\s*)(.*?)(\s*</section>)',
        flags=re.S,
    )
    if not pattern.search(content):
        raise RuntimeError(f"找不到 .az-nav 與 </section> 區段：{index_path}")

    updated = pattern.sub(rf"\g<1>{letters_html}\g<3>", content, count=1)
    index_path.write_text(updated, encoding="utf-8")


def _record_priority(pdf_path: Path, recs: list[dict[str, str]]) -> int:
    is_multi = len(recs) > 1
    has_underscore = "_" in pdf_path.stem
    if is_multi and has_underscore:
        return 3
    if is_multi:
        return 2
    if has_underscore:
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse PDFs in doc/eu-doc-red/, split multi-model PDFs, and update JSON + index.html")
    parser.add_argument("--doc-dir", type=Path, default=Path("doc/eu-doc-red"))
    parser.add_argument("--json-out", type=Path, default=Path("data/eu_doc_records.json"))
    parser.add_argument("--index-html", type=Path, default=Path("doc/eu-doc-red/index.html"))
    parser.add_argument("--cache-file", type=Path, default=Path("data/.eu_doc_parse_cache.json"))
    parser.add_argument("--web-doc-dir", type=str, default="doc/eu-doc-red")
    args = parser.parse_args()

    cache = load_parse_cache(args.cache_file)
    next_cache: dict[str, dict[str, object]] = {}

    # 1. 依 SHA-256 判斷是否需要重新解析 PDF，並記錄來源是否有更新
    pdf_files = sorted(args.doc_dir.glob("*.pdf"))
    parsed_by_file: dict[Path, list[dict[str, str]]] = {}
    source_changed_by_file: dict[Path, bool] = {}

    parsed_count = 0
    cached_count = 0

    for pdf_path in pdf_files:
        digest = compute_sha256(pdf_path)
        cached_entry = cache.get(pdf_path.name)
        is_unchanged = (
            cached_entry is not None
            and cached_entry.get("sha256") == digest
            and isinstance(cached_entry.get("records"), list)
        )

        if is_unchanged:
            recs = [dict(r) for r in cached_entry["records"]]  # type: ignore[union-attr]
            source_changed = False
            cached_count += 1
        else:
            recs = parse_pdf_to_records(pdf_path)
            source_changed = True
            parsed_count += 1

        parsed_by_file[pdf_path] = recs
        source_changed_by_file[pdf_path] = source_changed
        next_cache[pdf_path.name] = {
            "sha256": digest,
            "records": recs,
        }

    # 2. 依優先權合併紀錄；僅在來源 PDF 有更新或目標別名檔不存在時才複製 <MODEL>.pdf
    generated_aliases: set[str] = set()
    copied_count = 0
    by_model: dict[str, tuple[int, dict[str, str]]] = {}

    ordered_items = sorted(
        parsed_by_file.items(),
        key=lambda item: (_record_priority(item[0], item[1]), item[0].name.casefold()),
    )

    for pdf_path, recs in ordered_items:
        prio = _record_priority(pdf_path, recs)
        source_changed = source_changed_by_file.get(pdf_path, True)

        if len(recs) > 1:
            for rec in recs:
                model_code = rec["models"].strip().upper()
                alias_path = args.doc_dir / f"{model_code}.pdf"
                if alias_path.resolve() != pdf_path.resolve():
                    if source_changed or not alias_path.exists():
                        shutil.copyfile(pdf_path, alias_path)
                        copied_count += 1
                    generated_aliases.add(alias_path.name.casefold())

        for rec in recs:
            model_code = rec["models"].strip().upper()
            current = by_model.get(model_code)
            if current is None or prio >= current[0]:
                by_model[model_code] = (prio, rec)

    records = [by_model[k][1] for k in sorted(by_model, key=str.casefold)]

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    save_parse_cache(args.cache_file, next_cache)
    update_index_html(args.index_html, records, args.web_doc_dir)
    print(
        f"Processed {len(pdf_files)} PDF(s) (parsed={parsed_count}, cached={cached_count}), "
        f"copied {copied_count} split file(s) ({len(generated_aliases)} alias(es) tracked), "
        f"total {len(records)} record(s)."
    )


if __name__ == "__main__":
    main()
