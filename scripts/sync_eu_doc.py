#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import shutil
from pathlib import Path
from pypdf import PdfReader


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
    entries: list[tuple[str, str]] = []
    seen_codes: set[str] = set()

    for part in re.split(r"\s*/\s*", raw_line):
        label = part.strip()
        if not label:
            continue
        code_match = re.match(r"^([A-Za-z0-9][A-Za-z0-9.-]*)", label)
        if not code_match:
            continue
        code = code_match.group(1).upper()
        if code not in seen_codes:
            seen_codes.add(code)
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
            "source_file": pdf_path.name,
            "download_file": pdf_path.name,
            "document": document_name,
            "models": model_code,
            "label": full_label,
            "family": family,
            "published": published,
            "applicable_legislation": legislation,
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse PDFs in doc/eu-doc-red/, split multi-model PDFs, and update JSON + index.html")
    parser.add_argument("--doc-dir", type=Path, default=Path("doc/eu-doc-red"))
    parser.add_argument("--json-out", type=Path, default=Path("data/eu_doc_records.json"))
    parser.add_argument("--index-html", type=Path, default=Path("doc/eu-doc-red/index.html"))
    parser.add_argument("--web-doc-dir", type=str, default="doc/eu-doc-red")
    args = parser.parse_args()

    # 1. 先解析所有當層 PDF
    pdf_files = sorted(args.doc_dir.glob("*.pdf"))
    parsed_by_file: dict[Path, list[dict[str, str]]] = {
        pdf_path: parse_pdf_to_records(pdf_path) for pdf_path in pdf_files
    }

    # 2. 優先處理多型號合併檔：複製出 <MODEL>.pdf，並記錄產出的別名檔清單
    generated_aliases: set[str] = set()
    by_model: dict[str, dict[str, str]] = {}

    for pdf_path, recs in parsed_by_file.items():
        if len(recs) > 1:
            for rec in recs:
                model_code = rec["models"].strip().upper()
                alias_path = args.doc_dir / f"{model_code}.pdf"
                if alias_path.resolve() != pdf_path.resolve():
                    shutil.copyfile(pdf_path, alias_path)
                    generated_aliases.add(alias_path.name.casefold())
                by_model[model_code] = rec

    # 3. 再處理獨立的單型號 PDF（跳過剛剛由合併檔複製出來的別名檔）
    for pdf_path, recs in parsed_by_file.items():
        if len(recs) == 1:
            if pdf_path.name.casefold() in generated_aliases:
                continue
            rec = recs[0]
            model_code = rec["models"].strip().upper()
            by_model[model_code] = rec

    records = [by_model[k] for k in sorted(by_model, key=str.casefold)]

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    update_index_html(args.index_html, records, args.web_doc_dir)
    print(f"Processed {len(pdf_files)} PDF(s), generated {len(generated_aliases)} split copy(s), total {len(records)} record(s).")


if __name__ == "__main__":
    main()
