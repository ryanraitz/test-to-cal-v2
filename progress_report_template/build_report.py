#!/usr/bin/env python3
"""
build_report.py (Recomp estimate wiring)

- Reads ../reports/report.json
- Prefers InBody metrics if present in report["bars"]:
    - Body Fat Mass (BFM) / "Body Fat Mass"  -> fat loss
    - Skeletal Muscle Mass (SMM) / "Skeletal Muscle Mass" -> lean muscle
- Otherwise uses Weight + Bodyfat % to compute fat mass change, then estimates lean muscle change
  from strength progression (pull-ups + push-ups) and recomp conditions.

Ethical model:
- Fat loss is computed from measured BF% when BFM is not available.
- Lean muscle is ESTIMATED (and labeled as such in template) when SMM is not available.
- We cap estimated muscle gain per check-in to avoid unrealistic numbers.
"""

from __future__ import annotations
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
REPORTS_DIR = (SCRIPT_DIR.parent / "reports").resolve()
REPORT_JSON = REPORTS_DIR / "report.json"
OUTPUT_DIR = REPORTS_DIR / "output"
OUTPUT_HTML = OUTPUT_DIR / "latest.html"
TEMPLATE_HTML = SCRIPT_DIR / "template.html"
EXPORT_PY = SCRIPT_DIR / "export.py"

# ------------------------- parsing helpers -------------------------

def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
    if x is None:
        return default
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return default
        s = s.replace(",", "")
        s = re.sub(r"[^0-9\.\-]+", "", s)
        if not s or s in {"-", ".", "-."}:
            return default
        try:
            return float(s)
        except Exception:
            return default
    return default

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def _fmt_signed_lb(x: float) -> str:
    sign = "+" if x > 0 else ""
    return f"{sign}{x:.1f} lb"

def _fmt_signed_pct(x: float) -> str:
    sign = "+" if x > 0 else ""
    return f"{sign}{x:.0f}%"

def _pretty_int(n: Optional[float], fallback: str = "—") -> str:
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return fallback
    try:
        return f"{int(round(float(n))):,}"
    except Exception:
        return fallback

# ------------------------- bars mapping -------------------------

def _find_label_index(labels: List[Any], candidates: List[str]) -> int:
    cand = {c.strip().lower(): True for c in candidates}
    for i, raw in enumerate(labels or []):
        s = str(raw).strip().lower()
        if s in cand:
            return i
    return -1

def _get_bars_triplet(report: Dict[str, Any]) -> Tuple[List[Any], List[Any], List[Any]]:
    bars = report.get("bars")
    if isinstance(bars, dict):
        labels = bars.get("labels") or []
        prev = bars.get("prev") or []
        curr = bars.get("curr") or []
        if isinstance(labels, list) and isinstance(prev, list) and isinstance(curr, list):
            return labels, prev, curr
    return [], [], []

# ------------------------- strength model -------------------------

def compute_strength_pct(labels: List[Any], prev: List[Any], curr: List[Any]) -> float:
    idx_pull = _find_label_index(labels, ["Pull-Ups", "Pullups", "Pull Ups"])
    idx_push = _find_label_index(labels, ["Push-Ups", "Pushups", "Push Ups"])
    eps = 3.0  # avoids huge % when baseline is tiny

    p0 = _num(prev[idx_pull], 0.0) if 0 <= idx_pull < len(prev) else 0.0
    p1 = _num(curr[idx_pull], 0.0) if 0 <= idx_pull < len(curr) else 0.0
    s0 = _num(prev[idx_push], 0.0) if 0 <= idx_push < len(prev) else 0.0
    s1 = _num(curr[idx_push], 0.0) if 0 <= idx_push < len(curr) else 0.0

    pull_rel = (p1 + eps) / (p0 + eps)
    push_rel = (s1 + eps) / (s0 + eps)
    strength_rel = 0.6 * pull_rel + 0.4 * push_rel
    return (strength_rel - 1.0) * 100.0

# ------------------------- recomp calculations -------------------------

def compute_fat_loss_lb(labels: List[Any], prev: List[Any], curr: List[Any]) -> Tuple[float, bool]:
    """
    Returns (fat_change_lb, used_inbody_bfm).
    Negative means fat loss.
    Prefers InBody Body Fat Mass if present; otherwise uses Weight + Bodyfat %.
    """
    # InBody Body Fat Mass
    idx_bfm = _find_label_index(labels, ["Body Fat Mass", "BFM", "Body Fat Mass (lb)", "Body Fat Mass (lbs)"])
    if 0 <= idx_bfm < len(prev) and 0 <= idx_bfm < len(curr):
        b0 = _num(prev[idx_bfm], None)
        b1 = _num(curr[idx_bfm], None)
        if b0 is not None and b1 is not None:
            return (b1 - b0), True

    # Fallback: Weight + Bodyfat % -> fat mass
    idx_w = _find_label_index(labels, ["Weight", "Bodyweight"])
    idx_bf = _find_label_index(labels, ["Bodyfat %", "Body Fat %", "Bodyfat", "Body Fat"])

    w0 = _num(prev[idx_w], 0.0) if 0 <= idx_w < len(prev) else 0.0
    w1 = _num(curr[idx_w], 0.0) if 0 <= idx_w < len(curr) else 0.0
    bf0 = _num(prev[idx_bf], None) if 0 <= idx_bf < len(prev) else None
    bf1 = _num(curr[idx_bf], None) if 0 <= idx_bf < len(curr) else None

    if bf0 is not None and bf1 is not None and 2.0 <= bf0 <= 60.0 and 2.0 <= bf1 <= 60.0 and w0 > 0 and w1 > 0:
        fat0 = w0 * (bf0 / 100.0)
        fat1 = w1 * (bf1 / 100.0)
        return (fat1 - fat0), False

    # If even BF% missing, return 0 (keeps report generating)
    return (0.0, False)

def compute_estimated_lean_muscle_lb(labels: List[Any], prev: List[Any], curr: List[Any], strength_pct: float, fat_change_lb: float) -> Tuple[float, bool]:
    """
    Returns (lean_muscle_change_lb, used_inbody_smm).
    Negative means muscle loss.
    Prefers InBody Skeletal Muscle Mass (SMM) if present.
    Otherwise produces an estimate that is:
      - Encouraging but plausible
      - Anchored to strength progression and recomp context
      - Hard-capped to avoid unrealistic claims
    """
    # InBody Skeletal Muscle Mass
    idx_smm = _find_label_index(labels, ["Skeletal Muscle Mass", "SMM", "Skeletal Muscle Mass (lb)", "Skeletal Muscle Mass (lbs)"])
    if 0 <= idx_smm < len(prev) and 0 <= idx_smm < len(curr):
        m0 = _num(prev[idx_smm], None)
        m1 = _num(curr[idx_smm], None)
        if m0 is not None and m1 is not None:
            return (m1 - m0), True

    # Estimate from mass balance + strength:
    idx_w = _find_label_index(labels, ["Weight", "Bodyweight"])
    w0 = _num(prev[idx_w], 0.0) if 0 <= idx_w < len(prev) else 0.0
    w1 = _num(curr[idx_w], 0.0) if 0 <= idx_w < len(curr) else 0.0
    dw = w1 - w0

    # Non-fat mass change implied by fat change
    nonfat_change = dw - fat_change_lb  # can be positive even if weight down (recomp), or negative (water/glycogen)

    # Strength-based "muscle signal" (0..3 lb typical per check-in)
    # Convert strength_pct into 0..~2.5 lb baseline, then blend with nonfat_change if positive.
    strength_score = _clamp(strength_pct / 100.0, 0.0, 2.5)  # 100% => 1.0, 200% => 2.0
    baseline_gain = _clamp(1.2 * strength_score, 0.0, 2.8)   # 129% -> ~1.55 lb

    # If nonfat_change is positive (true recomp), allow the estimate to track it partially
    if nonfat_change > 0:
        # attribute 40–75% of nonfat gain to muscle depending on strength
        frac = _clamp(0.40 + 0.20 * strength_score, 0.40, 0.75)
        muscle_from_nonfat = nonfat_change * frac
        est = max(baseline_gain, muscle_from_nonfat)
    else:
        # If nonfat_change is negative, we can still show muscle gain if strength rose,
        # attributing the remainder to water/glycogen shifts.
        # Keep the estimate modest.
        est = baseline_gain

    # Recomp guardrails:
    # - If fat didn't drop and weight didn't drop, be conservative
    fat_dropped = (fat_change_lb < -0.2)
    weight_dropped = (dw < -0.2)
    if not fat_dropped and not weight_dropped:
        est *= 0.6

    # Hard caps per check-in (keeps ethical):
    # Small check-in: 0..3.0 lb muscle gain estimate; allow small negative if strength fell.
    est = _clamp(est, 0.0, 3.0)

    # If strength is negative, allow modest negative muscle estimate (do not force positivity)
    if strength_pct < -5:
        est = _clamp(nonfat_change * 0.2, -2.0, 0.0)

    return (float(est), False)

# ------------------------- HTML patching for recomp tiles -------------------------

def _class_for_delta(delta: float, positive_is_good: bool = True) -> str:
    # For Fat loss: negative is good => positive_is_good=False
    if positive_is_good:
        return "pos" if delta >= 0 else "neg"
    return "pos" if delta <= 0 else "neg"

def patch_recomp_tiles(html: str, fat_change_lb: float, lean_muscle_lb: float, strength_pct: float,
                      cico_str: str, weight_str: str, steps_str: str) -> str:
    tile_map = {
        "Fat loss": (_fmt_signed_lb(fat_change_lb), _class_for_delta(fat_change_lb, positive_is_good=False)),
        # In template, label is "Lean muscle" (with est tag). Regex matches just the text "Lean muscle" in the key div.
        "Lean muscle": (_fmt_signed_lb(lean_muscle_lb), _class_for_delta(lean_muscle_lb, positive_is_good=True)),
        "Strength": (_fmt_signed_pct(strength_pct), _class_for_delta(strength_pct, positive_is_good=True)),
        "Calorie goal": (cico_str, "pos"),
        "Weight": (weight_str, "pos"),
        "Daily steps": (steps_str, "pos"),
    }

    for key, (val, cls) in tile_map.items():
        # Match recomp-kpi by key text; allow extra markup inside the key div (like (est.))
        pattern = re.compile(
            r'(<div\s+class="recomp-kpi"\s*>.*?<div\s+class="k"\s*>\s*' + re.escape(key) +
            r'(?:\s*<[^>]+>.*?</[^>]+>\s*)?\s*</div>\s*<div\s+class="v\s+)([^"]*)(">\s*)(.*?)(\s*</div>)',
            re.IGNORECASE | re.DOTALL
        )
        def _repl(m: re.Match) -> str:
            # preserve approx span if present by replacing only inner content while keeping leading "~" if any
            inner = m.group(4)
            if 'class="approx"' in inner:
                inner = re.sub(r'(<span\s+class="approx">.*?</span>)\s*.*', r'\1' + val.lstrip("+"), inner, flags=re.DOTALL)
                # If val includes '+' we keep it; approx span already implies estimate
                if val.startswith("+"):
                    inner = re.sub(r'(<span\s+class="approx">.*?</span>)', r'\1+', inner)
            else:
                inner = val
            return m.group(1) + cls + m.group(3) + inner + m.group(5)
        html = pattern.sub(_repl, html, count=1)

    return html

# ------------------------- REPORT_DATA injection -------------------------

def inject_report_data(html: str, report_data: Dict[str, Any]) -> str:
    js = "window.REPORT_DATA = " + json.dumps(report_data, ensure_ascii=False) + ";"
    pattern = re.compile(r"window\.REPORT_DATA\s*=\s*\{.*?\};", re.DOTALL)
    if pattern.search(html):
        return pattern.sub(js, html)

    insert_point = html.lower().find("</head>")
    if insert_point != -1:
        return html[:insert_point] + f"\n<script>\n{js}\n</script>\n" + html[insert_point:]
    return html + f"\n<script>\n{js}\n</script>\n"

def main() -> None:
    if not REPORT_JSON.exists():
        raise FileNotFoundError(f"Missing report.json at: {REPORT_JSON}")
    if not TEMPLATE_HTML.exists():
        raise FileNotFoundError(f"Missing template.html at: {TEMPLATE_HTML}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    labels, prev, curr = _get_bars_triplet(report)

    strength_pct = compute_strength_pct(labels, prev, curr)
    fat_change_lb, used_bfm = compute_fat_loss_lb(labels, prev, curr)
    lean_muscle_lb, used_smm = compute_estimated_lean_muscle_lb(labels, prev, curr, strength_pct, fat_change_lb)

    # Store for debugging / front-end use
    report["recomp"] = {
        "fat_change_lb": fat_change_lb,
        "lean_muscle_change_lb": lean_muscle_lb,
        "strength_change_pct": strength_pct,
        "used_inbody_bfm": bool(used_bfm),
        "used_inbody_smm": bool(used_smm),
        "lean_muscle_is_estimate": (not used_smm),
    }

    # Values for tiles
    idx_cico = _find_label_index(labels, ["CICO Goal", "Daily Calorie Goal", "Daily Cal Goal", "Calorie Goal", "Calorie goal", "Daily Calorie Goal"])
    idx_w = _find_label_index(labels, ["Weight", "Bodyweight"])
    cico_val = _num(curr[idx_cico], None) if 0 <= idx_cico < len(curr) else None
    w1 = _num(curr[idx_w], None) if 0 <= idx_w < len(curr) else None
    steps_val = _num(report.get("dailySteps"), None)

    cico_str = _pretty_int(cico_val, "—")
    weight_str = _pretty_int(w1, "—")
    steps_str = _pretty_int(steps_val, "—")

    # Ensure tokens exist (keep your title locks safe)
    tokens = report.get("tokens")
    if not isinstance(tokens, dict):
        tokens = {}
        report["tokens"] = tokens
    tokens.setdefault("REPORT_TITLE", "Progress Report")
    tokens.setdefault("REPORT_SUBTITLE", "A coach-generated results summary for the client, including macros, adherence, and trend lines.")
    tokens.setdefault("MACROS_TITLE", "Macronutrient Breakdown")
    tokens.setdefault("BARS_TITLE", "Composition Changes")

    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    html = inject_report_data(html, report)
    html = patch_recomp_tiles(html, fat_change_lb, lean_muscle_lb, strength_pct, cico_str, weight_str, steps_str)

    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Wrote: {OUTPUT_HTML}")

    # Optional export: pass args so export.py doesn't print Usage
    if EXPORT_PY.exists():
        try:
            subprocess.run([sys.executable, str(EXPORT_PY), str(OUTPUT_HTML), str(OUTPUT_DIR)], cwd=str(SCRIPT_DIR), check=False)
        except Exception as e:
            print(f"(Non-fatal) export.py failed: {e}")

if __name__ == "__main__":
    main()
