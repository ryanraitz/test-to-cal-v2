#!/usr/bin/env python3
"""
build_report.py — Windows/macOS safe (relative paths)

Fixes:
- _num_from_token is defined at TOP LEVEL (no NameError)
- No hardcoded ~/Library/... paths (fixes FileNotFoundError on Windows)
- Always writes reports/output/report-data.js from reports/report.json
- Writes reports/output/latest.html from progress_report_template/template.html
- Injects tokens into HTML and injects a safe inline REPORT_DATA fallback
- Ensures latest.html loads ./report-data.js before any chart JS

Assumed project structure (relative to this file):
  <PROJECT_ROOT>/
    reports/report.json
    reports/output/
    progress_report_template/
      build_report.py   <-- this file
      template.html
      export.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import subprocess
from pathlib import Path


# -----------------------------
# Helpers (TOP LEVEL)
# -----------------------------
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

def _num_from_token(v) -> float:
    if v is None:
        return 0.0
    m = _NUM_RE.search(str(v))
    return float(m.group(0)) if m else 0.0

def _inject_tokens(html: str, tokens: dict) -> str:
    # Simple {{TOKEN}} replacement
    for k, v in (tokens or {}).items():
        html = html.replace("{{" + str(k) + "}}", str(v))
    return html

def _ensure_report_data_loader(html: str) -> str:
    loader = '<script src="./report-data.js"></script>'
    if loader in html:
        return html
    # Insert in <head> if possible; otherwise prepend
    if "<head" in html.lower():
        # insert right after first <head...>
        return re.sub(r"(<head[^>]*>)", r"\1\n" + loader, html, count=1, flags=re.I)
    return loader + "\n" + html


def main() -> None:
    script_dir = Path(__file__).resolve().parent                 # .../progress_report_template
    base_dir = script_dir.parent                                 # project root
    reports_dir = base_dir / "reports"
    output_dir = reports_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    report_json = Path(os.environ.get("REPORT_JSON", str(reports_dir / "report.json"))).resolve()
    template_html = Path(os.environ.get("TEMPLATE_HTML", str(script_dir / "template.html"))).resolve()
    export_py = script_dir / "export.py"
    latest_html = output_dir / "latest.html"
    report_data_js = output_dir / "report-data.js"

    if not report_json.exists():
        raise FileNotFoundError(f"Missing report.json at: {report_json}")
    if not template_html.exists():
        raise FileNotFoundError(f"Missing template.html at: {template_html}")

    report_data = json.loads(report_json.read_text(encoding="utf-8"))
    tokens = report_data.get("tokens", {}) or {}

    # ------------------------------------------------------------
    # FORCE header + section titles
    # (HTML uses {{REPORT_TITLE}}, {{REPORT_SUBTITLE}}, {{MACROS_TITLE}}, {{BARS_TITLE}})
    # This prevents old/default titles in report.json from overriding your basehtml.
    # ------------------------------------------------------------
    tokens["REPORT_TITLE"] = "Progress Report"
    tokens["REPORT_SUBTITLE"] = "A coach-generated results summary for the client, including macros, adherence, and trend lines."
    tokens["MACROS_TITLE"] = "Macronutrient Breakdown"
    tokens["BARS_TITLE"] = "Composition Changes"
    report_data["tokens"] = tokens

    # ------------------------------------------------------------
    # FORCE section titles (HTML uses {{MACROS_TITLE}} / {{BARS_TITLE}})
    # This prevents old/default titles in report.json from overriding your basehtml.
    # ------------------------------------------------------------
    tokens["MACROS_TITLE"] = "Macronutrient Breakdown"
    tokens["BARS_TITLE"] = "Composition Changes"
    report_data["tokens"] = tokens

    # ------------------------------------------------------------
    # Guarantee macro grams for Dietary Changes bars (bar / prev_bar)
    # Derived from tokens ROW_#_MID / ROW_#_VALUE (strings like "150 g")
    # ------------------------------------------------------------
    report_data["bar"] = {
        "protein_g": _num_from_token(tokens.get("ROW_1_VALUE")),
        "carbs_g":   _num_from_token(tokens.get("ROW_2_VALUE")),
        "fat_g":     _num_from_token(tokens.get("ROW_3_VALUE")),
    }
    report_data["prev_bar"] = {
        "protein_g": _num_from_token(tokens.get("ROW_1_MID")),
        "carbs_g":   _num_from_token(tokens.get("ROW_2_MID")),
        "fat_g":     _num_from_token(tokens.get("ROW_3_MID")),
    }



    # ------------------------------------------------------------
    # Recomp KPIs (Fat change / Lean mass / Strength)
    # Source of truth: report_data["bars"] prev/curr (same as "Composition changes")
    # ------------------------------------------------------------
    def _norm_label(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(s or "").strip().lower())

    def _find_idx(labels, *candidates) -> int:
        lab = [_norm_label(x) for x in (labels or [])]
        for c in candidates:
            cn = _norm_label(c)
            if cn in lab:
                return lab.index(cn)
        return -1

    bars_obj = report_data.get("bars") or {}
    labels = bars_obj.get("labels") or []
    prev = bars_obj.get("prev") or []
    curr = bars_obj.get("curr") or []

    # Indices (support your common label spellings)
    i_wt   = _find_idx(labels, "Weight", "Current Weight")
    i_bf   = _find_idx(labels, "Bodyfat %", "Body Fat %", "Bodyfat", "Body Fat")
    i_pull = _find_idx(labels, "Pull-Ups", "Pullups", "Pull Ups", "Pullups (max)")
    i_push = _find_idx(labels, "Push-Ups", "Pushups", "Push Ups", "Pushups (max)")

    def _safe_at(arr, idx, default=0.0):
        try:
            return float(arr[idx])
        except Exception:
            return float(default)

    wt_prev = _safe_at(prev, i_wt, 0.0) if i_wt >= 0 else 0.0
    wt_curr = _safe_at(curr, i_wt, 0.0) if i_wt >= 0 else 0.0
    bf_prev = _safe_at(prev, i_bf, 0.0) if i_bf >= 0 else 0.0
    bf_curr = _safe_at(curr, i_bf, 0.0) if i_bf >= 0 else 0.0

    # Fat mass + lean mass (lb)
    fat_prev = wt_prev * (bf_prev / 100.0) if wt_prev > 0 and bf_prev > 0 else 0.0
    fat_curr = wt_curr * (bf_curr / 100.0) if wt_curr > 0 and bf_curr > 0 else 0.0
    lean_prev = max(wt_prev - fat_prev, 0.0)
    lean_curr = max(wt_curr - fat_curr, 0.0)

    # Convention:
    # - fat_change_lb is NEGATIVE when fat is LOST (e.g. -6.8 lb)
    # - lean_change_lb is POSITIVE when lean mass is GAINED (e.g. +2.1 lb)
    fat_change_lb = round(fat_curr - fat_prev, 1)
    lean_change_lb = round(lean_curr - lean_prev, 1)

    # Strength % (blend pull-ups + push-ups % change; robust when prev is 0)
    pull_prev = _safe_at(prev, i_pull, 0.0) if i_pull >= 0 else 0.0
    pull_curr = _safe_at(curr, i_pull, 0.0) if i_pull >= 0 else 0.0
    push_prev = _safe_at(prev, i_push, 0.0) if i_push >= 0 else 0.0
    push_curr = _safe_at(curr, i_push, 0.0) if i_push >= 0 else 0.0

    def _pct_change(a, b):
        # returns percent change from a -> b
        denom = max(abs(a), 1.0)
        return ((b - a) / denom) * 100.0

    # If only one is present, use it; otherwise average both
    pct_pull = _pct_change(pull_prev, pull_curr) if (i_pull >= 0) else None
    pct_push = _pct_change(push_prev, push_curr) if (i_push >= 0) else None
    pct_vals = [x for x in (pct_pull, pct_push) if x is not None]
    strength_pct = round(sum(pct_vals) / len(pct_vals), 0) if pct_vals else 0.0

    # Save into report_data for template JS to bind
    report_data["recomp"] = {
        "fat_change_lb": fat_change_lb,
        "lean_change_lb": lean_change_lb,
        "strength_pct": strength_pct,
        "inputs": {
            "weight_prev": wt_prev, "weight_curr": wt_curr,
            "bf_prev": bf_prev, "bf_curr": bf_curr,
            "pull_prev": pull_prev, "pull_curr": pull_curr,
            "push_prev": push_prev, "push_curr": push_curr,
        }
    }

    # ------------------------------------------------------------
    # FORCE donut (macros) colors + ensure label exists
    # ------------------------------------------------------------
    macros = report_data.get("macros")
    if isinstance(macros, list):
        for m in macros:
            if not isinstance(m, dict):
                continue
            key = (m.get("key") or m.get("label") or "").strip().lower()

            if key == "protein":
                m["color"] = "#4CAF50"
            elif key in ("carbs", "carb", "carbohydrates"):
                m["color"] = "#F4C430"
            elif key in ("fat", "fats"):
                m["color"] = "#E57373"

            if "label" not in m and "key" in m:
                m["label"] = m.get("key")

    # ------------------------------------------------------------
    # ENRICH macros with Prev/New grams for Dietary Changes bars
    # tokens hold grams as strings like "150 g"
    # ------------------------------------------------------------
    p_prev = _num_from_token(tokens.get("ROW_1_MID"))
    p_new  = _num_from_token(tokens.get("ROW_1_VALUE"))
    c_prev = _num_from_token(tokens.get("ROW_2_MID"))
    c_new  = _num_from_token(tokens.get("ROW_2_VALUE"))
    f_prev = _num_from_token(tokens.get("ROW_3_MID"))
    f_new  = _num_from_token(tokens.get("ROW_3_VALUE"))

    if isinstance(macros, list):
        for m in macros:
            if not isinstance(m, dict):
                continue
            k = (m.get("key") or m.get("label") or "").strip().lower()
            if k == "protein":
                m["prev_g"] = p_prev
                m["new_g"]  = p_new
            elif k in ("carbs", "carb", "carbohydrates"):
                m["prev_g"] = c_prev
                m["new_g"]  = c_new
            elif k in ("fat", "fats"):
                m["prev_g"] = f_prev
                m["new_g"]  = f_new

    # ------------------------------------------------------------
    # Write report-data.js (source of truth for browser)
    # ------------------------------------------------------------
    report_data_js.write_text(
        "window.REPORT_DATA = " + json.dumps(report_data, ensure_ascii=False) + ";\n",
        encoding="utf-8"
    )
    print("[build_report] wrote:", report_data_js)

    # ------------------------------------------------------------
    # Build latest.html
    # ------------------------------------------------------------
    html = template_html.read_text(encoding="utf-8")
    html = _ensure_report_data_loader(html)
    html = _inject_tokens(html, tokens)

    # Inline fallback (won't overwrite external if it loaded)
    inline = "<script>window.REPORT_DATA = window.REPORT_DATA || " + json.dumps(report_data, ensure_ascii=False) + ";</script>"
    if "</body>" in html.lower():
        html = re.sub(r"</body>", inline + "\n</body>", html, count=1, flags=re.I)
    else:
        html += "\n" + inline + "\n"

    latest_html.write_text(html, encoding="utf-8")
    print("[build_report] wrote:", latest_html)

    # ------------------------------------------------------------
    # Optional export step (kept compatible with your pipeline)
    # ------------------------------------------------------------
    if export_py.exists():
        try:
            subprocess.run([sys.executable, str(export_py), str(latest_html)], check=True)
        except Exception as e:
            print("[build_report] export.py failed (continuing):", e)


if __name__ == "__main__":
    main()
