Progress Report Template (test-to-cal-patched)

How to run (Windows):
- Double-click / run:
  generate_progress_report_TEST_TO_CAL_PATCHED.ps1

What to open/view:
- ALWAYS open the GENERATED report:
  reports/output/latest.html

Data flow / single source of truth:
- INPUT (editable):   reports/report.json
- GENERATED OUTPUTS:  reports/output/latest.html
                      reports/output/report-data.js

Important:
- Do not edit or rely on any report-data.js inside progress_report_template.
  That file has been removed to prevent stale/incorrect data from loading.

Dependencies:
- requirements.txt contains the Python deps.
- The launcher only installs deps when requirements.txt changes, and only
  installs Playwright browsers once per virtual environment.
