"""Shared schedule categories. Mixed exam titles remain general tests."""
import re

KINDS = {"midterm", "final", "test", "assignment"}
MIDTERM_RE = re.compile(r"期中|\bmid[\s‐‑–-]*(?:term|test)\b", re.I)
FINAL_PATTERN = r"\bfinal[\s‐‑–-]+(?:exam(?:ination)?s?|tests?)\b|\bend[\s‐‑–-]+of[\s‐‑–-]+term[\s‐‑–-]+exam(?:ination)?\b|期末(?:考[试試]|测[试試]|測試|考核)"
FINAL_RE = re.compile(FINAL_PATTERN, re.I)
EXAM_RE = re.compile(r"考[试試]|测[验試试]|測[驗試]|\bexam(?:ination)?s?\b|\btests?\b|\bquiz(?:zes)?\b", re.I)


def classify(title):
    midterm, final = bool(MIDTERM_RE.search(title)), bool(FINAL_RE.search(title))
    if midterm and final:
        return "test"
    if midterm:
        return "midterm"
    if final:
        return "final"
    return "test" if EXAM_RE.search(title) else "assignment"
