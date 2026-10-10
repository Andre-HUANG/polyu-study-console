"""Local PDF table reconstruction using pdfplumber (MIT).

Run in a separate process so a damaged file cannot stop the course scanner.
Native PDFKit/Vision remain responsible for all-page text and image coverage.
Only the student's private cache contains the reconstructed cells.
"""
from __future__ import annotations

import json
import logging
import re
import sys

ACADEMIC = re.compile(r"assess|exam|mid[\s‐‑–-]*term|quiz|test|weight|schedule|timetable|deadline|due\b|presentation|assign|考[核试試]|期[中末]|评分|評分|日程|时间表|時間表", re.I)


def read_layout(path, selected):
    import pdfplumber
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    results = {}
    with pdfplumber.open(path) as pdf:
        for number in selected:
            if number < 1 or number > len(pdf.pages):
                continue
            page = pdf.pages[number - 1]
            try:
                text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
                result = {"text": text, "tables": []}
                if ACADEMIC.search(text):
                    seen = set()
                    # Actual ruling lines preserve merged/multiline cells. A
                    # second alignment pass handles borderless lecture tables.
                    strategies = [({}, "表格边界"), ({"vertical_strategy": "text", "horizontal_strategy": "text", "min_words_vertical": 3}, "文字列对齐")]
                    for settings, method in strategies:
                        if settings and result["tables"]:
                            break
                        # Thousands of plot edges are a chart, not an assessment table.
                        if not settings and len(page.edges) > 2500:
                            continue
                        for table in page.find_tables(settings):
                            cells = [[re.sub(r"\s+", " ", cell or "").strip() for cell in row] for row in table.extract()]
                            cells = [row for row in cells if any(row)]
                            if len(cells) < 2 or max((sum(bool(c) for c in row) for row in cells), default=0) < 2:
                                continue
                            rows = ["\t".join(row) for row in cells]
                            body = "\n".join(rows)
                            if not ACADEMIC.search(body) or body in seen:
                                continue
                            # A text-only grid must have an explicit table header;
                            # otherwise a prose paragraph could become a fake table.
                            if settings and not re.search(r"week|date|weight|%|assessment|日期|周次|比重|考核", " ".join(rows[:3]), re.I):
                                continue
                            if settings:
                                nonempty = [c for row in cells for c in row if c]
                                fragments = sum(bool(re.fullmatch(r"[A-Za-z]|[A-Za-z][.,]", c)) for c in nonempty)
                                if fragments > max(2, len(nonempty)*.08):
                                    # Imaginary text columns can cut words into
                                    # letters on a two-column slide. Retain the
                                    # PDFKit/OCR reading instead of inventing cells.
                                    continue
                            seen.add(body)
                            above = page.crop((0, max(0, table.bbox[1]-65), page.width, table.bbox[1])).extract_text() if table.bbox[1] > 0 else ""
                            context = [line for line in (above or "").splitlines() if ACADEMIC.search(line)]
                            result["tables"].append({"rows": rows, "cells": cells, "context": context[-2:], "bbox": list(table.bbox), "method": method})
                results[str(number)] = result
            except Exception as error:
                results[str(number)] = {"issue": "PDF 表格结构读取未完成（" + type(error).__name__ + "）"}
            finally:
                page.close()
    return results


if __name__ == "__main__":
    try:
        print(json.dumps(read_layout(sys.argv[1], json.loads(sys.argv[2])), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"error": "PDF 表格解析器：" + type(error).__name__}, ensure_ascii=False))
        sys.exit(1)
