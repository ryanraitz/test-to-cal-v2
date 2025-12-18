#!/usr/bin/env python3
from pathlib import Path
import sys, math
from playwright.sync_api import sync_playwright

def main():
    if len(sys.argv) < 2:
        print("Usage: python export.py /path/to/latest.html [output_dir]")
        raise SystemExit(2)

    in_html = Path(sys.argv[1]).resolve()
    out_dir_arg = Path(sys.argv[2]).resolve() if len(sys.argv) >= 3 else None
    if not in_html.exists():
        raise FileNotFoundError(f"Input HTML not found: {in_html}")

    out_pdf = (out_dir_arg / (in_html.stem + ".pdf")) if out_dir_arg else in_html.with_suffix(".pdf")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(in_html.as_uri(), wait_until="networkidle")

        # Measure full-page size (in pixels) then export a single-page PDF
        box = page.evaluate(
            """() => {
                const el = document.documentElement;
                const body = document.body;
                const w = Math.max(el.scrollWidth, body.scrollWidth, el.clientWidth);
                const h = Math.max(el.scrollHeight, body.scrollHeight, el.clientHeight);
                return { w, h };
            }"""
        )
        w = int(math.ceil(box["w"]))
        h = int(math.ceil(box["h"]))
        page.set_viewport_size({"width": w, "height": h})

        page.pdf(
            path=str(out_pdf),
            print_background=True,
            width=f"{w}px",
            height=f"{h}px",
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            prefer_css_page_size=True,
        )

        browser.close()

    print(f"[export] wrote: {out_pdf}")

if __name__ == "__main__":
    main()
