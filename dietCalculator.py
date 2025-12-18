#!/usr/bin/env python3
"""
dietCalculator.py (CLEANED)

What this version does (key behaviors preserved + fixes added):
- PyQt5 GUI collects client + metrics, calculates BMR/TDEE/macros.
- "Generate PDF" writes reports/report.json and runs progress_report_template/build_report.py.
- NEW: Old vs New comparisons
  - bars.prev (Old) is populated from the previous reports/report.json bars.curr when available.
  - macro "Old" grams are populated from the previous reports/report.json tokens ROW_*_VALUE when available.
  - bars.curr + macro "New" grams are the current GUI calculation.
- Badge placeholders removed from the JSON generation (no Compliance/Avg sleep/Training days tokens).
"""

import sys
import os
import csv
import subprocess
import json
from datetime import datetime
from pathlib import Path
import hashlib
import re


# --- Path roots (portable across Windows/macOS/Linux) ---
ROOT_DIR = Path(__file__).resolve().parent
REPORTS_DIR = ROOT_DIR / "reports"
TEMPLATE_DIR = ROOT_DIR / "progress_report_template"
OUTPUT_DIR = REPORTS_DIR / "output"
CLIENTS_DIR = REPORTS_DIR / "clients"

# --- FIX: make hashlib.md5 ignore unsupported 'usedforsecurity' kwarg (PyQt / macOS packaging edge) ---
_original_md5 = hashlib.md5
def _safe_md5(*args, **kwargs):
    kwargs.pop("usedforsecurity", None)
    return _original_md5(*args, **kwargs)
hashlib.md5 = _safe_md5
# -----------------------------------------------------------------------------------------------

from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLineEdit,
    QComboBox,
    QPushButton,
    QTextEdit,
    QMessageBox,
    QLabel,
    QDialog,
    QRadioButton,
    QButtonGroup,
)
from PyQt5.QtCore import QSettings, Qt
from PyQt5.QtGui import QPalette, QColor, QFont


def apply_dark_theme(app: QApplication):
    """Apply a dark Fusion theme to the whole app."""
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(20, 20, 20))
    palette.setColor(QPalette.WindowText, Qt.white)
    palette.setColor(QPalette.Base, QColor(25, 25, 25))
    palette.setColor(QPalette.AlternateBase, QColor(40, 40, 40))
    palette.setColor(QPalette.ToolTipBase, Qt.white)
    palette.setColor(QPalette.ToolTipText, Qt.white)
    palette.setColor(QPalette.Text, Qt.white)
    palette.setColor(QPalette.Button, QColor(40, 40, 40))
    palette.setColor(QPalette.ButtonText, Qt.white)
    palette.setColor(QPalette.BrightText, Qt.red)
    palette.setColor(QPalette.Highlight, QColor(150, 90, 255))
    palette.setColor(QPalette.HighlightedText, Qt.black)
    app.setPalette(palette)




def open_file(path: Path) -> None:
    """Open a file with the system default viewer (Windows/macOS/Linux)."""
    try:
        p = Path(path).resolve()
        if sys.platform.startswith("win"):
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess
            subprocess.run(["open", str(p)], check=False)
        else:
            import subprocess
            subprocess.run(["xdg-open", str(p)], check=False)
    except Exception:
        pass


class ReportTypeDialog(QDialog):
    #\"\"\"Blocking dialog shown at startup to choose Initial vs Follow-up behavior.\"\"\"
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Progress Report Type")
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout()

        title = QLabel("What type of progress report are you creating?")
        tf = QFont()
        tf.setPointSize(14)
        tf.setBold(True)
        title.setFont(tf)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        layout.addSpacing(12)

        self.rb_initial = QRadioButton("Initial report (no prior comparison)")
        self.rb_follow = QRadioButton("Follow-up report (compare vs client’s last report)")
        self.rb_follow.setChecked(True)

        # group (ensures exclusivity)
        self.grp = QButtonGroup(self)
        self.grp.addButton(self.rb_initial)
        self.grp.addButton(self.rb_follow)

        layout.addWidget(self.rb_initial)
        layout.addWidget(self.rb_follow)

        helper = QLabel(
            "Initial: Old/New values will match (baseline = current).\\n"
            "Follow-up: Old values come from that client’s previous saved report."
        )
        helper.setStyleSheet("color: rgba(255,255,255,.65); font-size: 11pt;")
        helper.setAlignment(Qt.AlignCenter)
        layout.addSpacing(10)
        layout.addWidget(helper)

        layout.addSpacing(14)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.ok_btn = QPushButton("Continue")
        self.ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.ok_btn)
        btn_row.addStretch()

        layout.addLayout(btn_row)
        self.setLayout(layout)

    def choice(self) -> str:
        return "initial" if self.rb_initial.isChecked() else "followup"


class DietCalculator(QWidget):

    @staticmethod
    def _slug(text: str) -> str:
        """Normalize a string for safe filesystem usage."""
        text = (text or "").strip().lower()
        text = re.sub(r'[^a-z0-9]+', '_', text)
        return text.strip('_')

    def __init__(self, report_mode: str = "followup"):
        super().__init__()

        self.report_mode = (report_mode or "followup").strip().lower()
        if self.report_mode not in ("initial", "followup"):
            self.report_mode = "followup"

        # For packaging (PyInstaller): base dir is where the executable/script lives
        self.base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))

        self.setWindowTitle("Client Diet Calculator")
        self.setMinimumSize(820, 720)

        self.clients = []
        self.last_results = None  # store last calculation results for export/PDF

        # PDF/send state
        self.pdf_state = "generate"  # "generate" or "send"
        self.last_pdf_path = None
        self.last_pdf_client_name = None
        self.last_pdf_client_phone = None

        # Settings for window state memory
        self.settings = QSettings("RyanTools", "ClientDietCalculator")

        main_layout = QVBoxLayout()

        # ------------------ HEADER BAR ------------------
        header_layout = QHBoxLayout()
        title_label = QLabel("Client Diet Calculator")
        title_font = QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)

        header_layout.addStretch()
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        # ------------------ CLIENT SECTION ------------------
        self.client_combo = QComboBox()
        self.client_combo.addItem("Select client...")
        self.load_clients_from_csv()
        self.client_combo.currentIndexChanged.connect(self.on_client_selected)

        self.phone_label = QLabel("phone # : ")

        main_layout.addSpacing(10)
        main_layout.addWidget(QLabel("Client:"))
        main_layout.addWidget(self.client_combo)
        main_layout.addWidget(self.phone_label)
        main_layout.addSpacing(10)

        # ------------------ FORM SECTION (GRID) + SIDE BUTTONS ------------------
        middle_layout = QHBoxLayout()

        form_layout = QGridLayout()
        form_layout.setHorizontalSpacing(18)
        form_layout.setVerticalSpacing(12)

        # Controls
        self.gender_combo = QComboBox()
        self.gender_combo.addItems(["Male", "Female"])

        self.age_edit = QLineEdit()
        self.height_edit = QLineEdit()
        self.height_edit.setPlaceholderText("e.g. 5'3\" or 5 3 or 63")

        self.weight_edit = QLineEdit()
        self.steps_edit = QLineEdit()
        self.sessions_edit = QLineEdit()
        self.sessions_edit.setPlaceholderText("e.g. 3")

        self.bf_edit = QLineEdit()
        self.bf_edit.setPlaceholderText("e.g. 18")

        self.pullups_edit = QLineEdit()
        self.pullups_edit.setPlaceholderText("e.g. 12")

        self.pushups_edit = QLineEdit()
        self.pushups_edit.setPlaceholderText("e.g. 35")

        self.goal_edit = QLineEdit()
        self.goal_edit.setPlaceholderText("e.g. -10 (lose 10 lb), +15 (gain 15 lb)")

        short_inputs = [
            self.age_edit,
            self.height_edit,
            self.weight_edit,
            self.steps_edit,
            self.sessions_edit,
            self.bf_edit,
            self.goal_edit,
        ]
        for w in short_inputs:
            w.setMaximumWidth(220)

        row = 0
        form_layout.addWidget(QLabel("Gender:"), row, 0)
        form_layout.addWidget(self.gender_combo, row, 1)
        row += 1

        form_layout.addWidget(QLabel("Age (years):"), row, 0)
        form_layout.addWidget(self.age_edit, row, 1)
        row += 1

        form_layout.addWidget(QLabel("Height:"), row, 0)
        form_layout.addWidget(self.height_edit, row, 1)
        row += 1

        form_layout.addWidget(QLabel("Weight (lbs):"), row, 0)
        form_layout.addWidget(self.weight_edit, row, 1)
        row += 1

        form_layout.addWidget(QLabel("Avg steps per day:"), row, 0)
        form_layout.addWidget(self.steps_edit, row, 1)
        row += 1

        form_layout.addWidget(QLabel("Training sessions/week:"), row, 0)
        form_layout.addWidget(self.sessions_edit, row, 1)
        row += 1

        form_layout.addWidget(QLabel("Body fat % (estimate):"), row, 0)
        form_layout.addWidget(self.bf_edit, row, 1)
        row += 1

        form_layout.addWidget(QLabel("Pull-Ups (max reps):"), row, 0)
        form_layout.addWidget(self.pullups_edit, row, 1)
        row += 1

        form_layout.addWidget(QLabel("Push-Ups (max reps):"), row, 0)
        form_layout.addWidget(self.pushups_edit, row, 1)
        row += 1

        form_layout.addWidget(QLabel("Goal weight change (lbs):"), row, 0)
        form_layout.addWidget(self.goal_edit, row, 1)
        row += 1

        middle_layout.addLayout(form_layout, stretch=3)

        # Side buttons: Clear / Exit / Calculate / Generate PDF
        side_btn_layout = QVBoxLayout()

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_fields)

        self.exit_button = QPushButton("Exit")
        self.exit_button.clicked.connect(self.close)

        self.calc_button = QPushButton("Calculate")
        self.calc_button.clicked.connect(self.calculate)

        self.pdf_button = QPushButton("Generate")
        self.pdf_button.clicked.connect(self.on_pdf_button_clicked)

        self.prev_report_button = QPushButton("Previous Report")
        self.prev_report_button.clicked.connect(self.on_prev_report_button_clicked)

        side_btn_layout.addWidget(self.clear_button)
        side_btn_layout.addWidget(self.exit_button)
        side_btn_layout.addSpacing(10)
        side_btn_layout.addWidget(self.calc_button)
        side_btn_layout.addWidget(self.pdf_button)
        side_btn_layout.addWidget(self.prev_report_button)
        side_btn_layout.addStretch()

        middle_layout.addLayout(side_btn_layout, stretch=1)
        main_layout.addLayout(middle_layout)

        # ------------------ OUTPUT AREA ------------------
        main_layout.addSpacing(10)
        main_layout.addWidget(QLabel("Output:"))
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        main_layout.addWidget(self.output_text, stretch=1)

        self.setLayout(main_layout)

        # ------------------ GLOBAL STYLESHEET (dark + purple buttons) ------------------
        self.setStyleSheet("""
            QWidget {
                background-color: #141414;
                color: #f0f0f0;
                font-size: 12pt;
            }
            QLabel { color: #f0f0f0; }
            QLineEdit, QComboBox {
                background-color: #222;
                border: 1px solid #555;
                border-radius: 6px;
                padding: 6px;
                color: #f0f0f0;
            }
            QLineEdit:focus, QComboBox:focus { border: 1px solid #a855ff; }
            QTextEdit {
                background-color: #1e1e1e;
                border: 1px solid #444;
                border-radius: 6px;
                padding: 8px;
                color: #f0f0f0;
            }
            QPushButton {
                background-color: #a855ff;
                border-radius: 10px;
                padding: 8px 18px;
                color: white;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #bf75ff; }
            QPushButton:pressed { background-color: #8b3fe0; }
        """)

        # Ensure PDF button starts in "generate" visual state
        self.set_pdf_button_state("generate")

        # Restore window geometry/state
        self.restore_window_state()

    # ------------------ WINDOW STATE MEMORY ------------------

    def restore_window_state(self):
        geom = self.settings.value("geometry")
        if geom is not None:
            try:
                self.restoreGeometry(geom)
            except Exception:
                pass

        state = self.settings.value("windowState")
        if state is not None and hasattr(self, "restoreState"):
            try:
                self.restoreState(state)
            except Exception:
                pass

    def closeEvent(self, event):
        try:
            self.settings.setValue("geometry", self.saveGeometry())
            if hasattr(self, "saveState"):
                self.settings.setValue("windowState", self.saveState())
        except Exception as e:
            print("closeEvent settings save failed:", e)
        event.accept()

    # ------------------ CLIENT CSV LOADING ------------------

    def load_clients_from_csv(self):
        """
        Load clients from clients_cleaned_go.csv located in the same folder
        as this script/executable. Tries to detect name + phone columns flexibly.

        (If you want to swap CSV name later, change it here.)
        """
        try:
            csv_path = os.path.join(self.base_dir, "clients_cleaned_go.csv")
            if not os.path.isfile(csv_path):
                return

            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    return

                name_field = None
                phone_field = None

                name_candidates = {"name", "client", "displayname", "full_name", "client_name"}
                phone_candidates = {"phone", "phone_number", "phonenumber", "number"}

                for fn in reader.fieldnames:
                    lfn = fn.lower().strip()
                    if name_field is None and lfn in name_candidates:
                        name_field = fn
                    if phone_field is None and lfn in phone_candidates:
                        phone_field = fn

                if name_field is None or phone_field is None:
                    print("Could not detect 'name' or 'phone' columns in clients_cleaned_go.csv")
                    return

                for row in reader:
                    name = (row.get(name_field, "") or "").strip()
                    phone = (row.get(phone_field, "") or "").strip()
                    if name and phone:
                        self.clients.append({"name": name, "phone": phone})
                        self.client_combo.addItem(name)

        except Exception as e:
            print("Error loading clients_cleaned_go.csv:", e)

    def on_client_selected(self, index):
        if index <= 0 or index - 1 >= len(self.clients):
            self.phone_label.setText("phone # : ")
            return
        client = self.clients[index - 1]
        self.phone_label.setText(f"phone # : {client['phone']}")

    # ------------------ CLEAR FIELDS ------------------

    def clear_fields(self):
        self.gender_combo.setCurrentIndex(0)
        self.age_edit.clear()
        self.height_edit.clear()
        self.weight_edit.clear()
        self.steps_edit.clear()
        self.sessions_edit.clear()
        self.bf_edit.clear()
        self.pullups_edit.clear()
        self.pushups_edit.clear()
        self.goal_edit.clear()
        self.output_text.clear()
        self.last_results = None
        self.last_pdf_path = None
        self.last_pdf_client_name = None
        self.last_pdf_client_phone = None
        self.set_pdf_button_state("generate")

    # ------------------ ERROR DIALOG ------------------

    def show_error(self, message: str):
        QMessageBox.critical(self, "Input Error", message)

    # ------------------ HEIGHT PARSER ------------------

    def parse_height_to_inches(self, s: str) -> float:
        """Accepts 5'3", 5 3, 63, etc. Returns inches."""
        text = (s or "").strip().lower()
        if not text:
            raise ValueError("Height is empty")

        if re.match(r"^\d+(\.\d+)?$", text):
            return float(text)

        pattern = r"""^\s*
            (\d+)                      # feet
            \s*(?:'|ft|feet)?\s*       # optional feet marker
            (\d+)?                     # optional inches
            \s*(?:\"|in|inch|inches)?\s*
            $"""
        m = re.match(pattern, text, re.VERBOSE)
        if not m:
            raise ValueError(f"Could not parse height: {s}")

        feet = int(m.group(1))
        inches = int(m.group(2)) if m.group(2) is not None else 0
        return feet * 12 + inches

    # ------------------ CORE CALCULATION ------------------

    def calculate(self):
        try:
            gender = self.gender_combo.currentText()

            age_str = self.age_edit.text().strip()
            height_str = self.height_edit.text().strip()
            weight_str = self.weight_edit.text().strip()
            steps_str = self.steps_edit.text().strip()
            sessions_str = self.sessions_edit.text().strip()
            bf_str = self.bf_edit.text().strip()
            goal_str = self.goal_edit.text().strip()

            pullups_str = self.pullups_edit.text().strip()
            pushups_str = self.pushups_edit.text().strip()

            if not all([age_str, height_str, weight_str, steps_str, sessions_str, bf_str, pullups_str, pushups_str]):
                self.show_error("Please fill in all required fields (goal weight change can be blank).")
                return

            age = int(age_str)
            try:
                height_in = self.parse_height_to_inches(height_str)
            except ValueError as he:
                self.show_error(str(he))
                return

            weight_lb = float(weight_str)
            steps = int(steps_str)
            sessions_per_week = float(sessions_str)
            bf_percent = float(bf_str)
            pullups = int(float(pullups_str))
            pushups = int(float(pushups_str))

            if pullups < 0 or pushups < 0:
                self.show_error("Pull-Ups and Push-Ups must be 0 or greater.")
                return

            if bf_percent <= 0 or bf_percent >= 60:
                self.show_error("Enter a realistic body fat % between 1 and 60.")
                return

            # Goal interpretation
            goal_weight_change_lbs = None
            direction = None
            pounds = 0.0
            if goal_str != "":
                try:
                    goal_raw = float(goal_str)
                except ValueError:
                    self.show_error("Goal weight change must be a number (e.g., -5 or +20).")
                    return

                if goal_raw == 0:
                    goal_weight_change_lbs = 0.0
                elif goal_raw < 0:
                    direction = "lose"
                    pounds = abs(goal_raw)
                    goal_weight_change_lbs = -pounds
                else:
                    direction = "gain"
                    pounds = abs(goal_raw)
                    goal_weight_change_lbs = pounds

            # --- BMR / TDEE Calculations ---
            weight_kg = weight_lb / 2.20462
            height_cm = height_in * 2.54
            bf = bf_percent / 100.0
            lbm_kg = weight_kg * (1 - bf)

            if gender == "Male":
                base_bmr = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) + 5
                ref_lbm = 59.0
            else:
                base_bmr = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) - 161
                ref_lbm = 45.0

            lbm_factor = lbm_kg / ref_lbm if ref_lbm > 0 else 1.0
            adj_bmr = base_bmr * lbm_factor

            if steps < 4000:
                activity_mult = 1.25
            elif steps < 6000:
                activity_mult = 1.35
            elif steps < 8000:
                activity_mult = 1.45
            elif steps < 10000:
                activity_mult = 1.55
            elif steps < 12000:
                activity_mult = 1.65
            elif steps < 15000:
                activity_mult = 1.75
            else:
                activity_mult = 1.85

            exercise_cals_per_session = 300.0
            avg_daily_exercise_cals = exercise_cals_per_session * (sessions_per_week / 7.0)

            tdee = adj_bmr * activity_mult + avg_daily_exercise_cals

            target_cals_250 = tdee - 250
            target_cals_500 = tdee - 500
            cal_target_mid = (target_cals_250 + target_cals_500) / 2.0

            protein_g = 1.0 * weight_lb  # 1g protein per lb bodyweight (user rule)
            protein_low_g = protein_g
            protein_high_g = protein_g
            protein_mid_g = protein_g
            protein_cals = protein_mid_g * 4.0

            # Split remaining calories into Carbs/Fats using a 5:3 ratio
            remaining_cals = max(cal_target_mid - protein_cals, 0)
            carb_cals = remaining_cals * (5.0 / 8.0)
            fat_cals  = remaining_cals * (3.0 / 8.0)

            fat_g = fat_cals / 9.0
            carb_g = carb_cals / 4.0

            adj_bmr_r = round(adj_bmr)
            tdee_r = round(tdee)
            target_cals_250_r = round(target_cals_250)
            target_cals_500_r = round(target_cals_500)
            cal_target_mid_r = round(cal_target_mid)

            protein_low_g_r = round(protein_low_g)
            protein_high_g_r = round(protein_high_g)
            protein_mid_g_r = round(protein_mid_g)
            fat_g_r = round(fat_g)
            carb_g_r = round(carb_g)

            # Goal time estimate
            goal_lines = []
            if direction in ("lose", "gain") and pounds > 0:
                total_kcal = pounds * 3500.0
                exact_days = total_kcal / 500.0
                lower_days = exact_days * 0.8
                upper_days = exact_days * 1.3
                lower_weeks = lower_days / 7.0
                upper_weeks = upper_days / 7.0
                lower_months = lower_weeks / 4.0
                upper_months = upper_weeks / 4.0

                goal_lines.append(
                    f"Estimated time to {direction} ~{pounds:.1f} lb at ~500 kcal/day "
                    f"{'deficit' if direction == 'lose' else 'surplus'} "
                    f"(assuming consistent but not perfect adherence):"
                )
                goal_lines.append(
                    f"  ≈ {lower_weeks:.1f} – {upper_weeks:.1f} weeks "
                    f"(about {lower_months:.1f} – {upper_months:.1f} months)"
                )

            summary_lines = []
            summary_lines.append("Client Info:")
            summary_lines.append(f"  Gender: {gender}")
            summary_lines.append(f"  Age: {age}  |  Height: {height_in:.1f} in  |  Weight: {weight_lb:.1f} lb")
            summary_lines.append(
                f"  Body Fat: {bf_percent:.1f}%  |  Steps/day: {steps}  |  Sessions/week: {sessions_per_week}"
            )
            summary_lines.append(f"  Pull-Ups (max): {pullups}  |  Push-Ups (max): {pushups}")
            summary_lines.append("")
            summary_lines.append("Estimated LBM-adjusted BMR:")
            summary_lines.append(f"  {adj_bmr_r} kcal/day")
            summary_lines.append("")
            summary_lines.append("Estimated TDEE:")
            summary_lines.append(f"  {tdee_r} kcal/day")
            summary_lines.append("")
            summary_lines.append("Target Calories for ~250–500 kcal Deficit:")
            summary_lines.append(f"  Higher-cal end (≈ -250): {target_cals_250_r} kcal/day")
            summary_lines.append(f"  Deeper deficit (≈ -500): {target_cals_500_r} kcal/day")
            summary_lines.append(f"  Midpoint used for macros: {cal_target_mid_r} kcal/day")
            summary_lines.append("")
            summary_lines.append("Macro Breakdown (based on midpoint target):")
            summary_lines.append(
                f"  Protein: ~{protein_mid_g_r} g/day (set to bodyweight: 1.0 g/lb)"
            )
            summary_lines.append(f"  Carbs:  ~{carb_g_r} g/day (remaining calories, 5:3 split with fats)")
            summary_lines.append(f"  Fats:   ~{fat_g_r} g/day (remaining calories, 5:3 split with carbs)")
            summary_lines.append("")

            if goal_lines:
                summary_lines.append("")
                summary_lines.append("Goal Projection:")
                summary_lines.extend(goal_lines)

            if gender == "Female" and cal_target_mid_r < 1400:
                summary_lines.append("")
                summary_lines.append(
                    "Note: Target calories are quite low for a female client. "
                    "Consider avoiding prolonged intake below ~1400 kcal/day without supervision."
                )
            if gender == "Male" and cal_target_mid_r < 1600:
                summary_lines.append("")
                summary_lines.append(
                    "Note: Target calories are quite low for a male client. "
                    "Consider avoiding prolonged intake below ~1600 kcal/day without supervision."
                )

            full_result_text = "\n".join(summary_lines)
            self.output_text.setPlainText(full_result_text)

            self.last_results = {
                "gender": gender,
                "age": age,
                "height_in": height_in,
                "weight_lb": weight_lb,
                "steps": steps,
                "sessions_per_week": sessions_per_week,
                "bf_percent": bf_percent,
                "pullups": pullups,
                "pushups": pushups,
                "adj_bmr_r": adj_bmr_r,
                "tdee_r": tdee_r,
                "target_cals_250_r": target_cals_250_r,
                "target_cals_500_r": target_cals_500_r,
                "cal_target_mid_r": cal_target_mid_r,
                "protein_mid_g_r": protein_mid_g_r,
                "protein_low_g_r": protein_low_g_r,
                "protein_high_g_r": protein_high_g_r,
                "fat_g_r": fat_g_r,
                "carb_g_r": carb_g_r,
                "goal_weight_change_lbs": goal_weight_change_lbs,
                "direction": direction,
                "pounds": pounds,
                "goal_lines": goal_lines,
                "full_text": full_result_text,
            }

            # New calculation invalidates previous PDF send state
            self.last_pdf_path = None
            self.last_pdf_client_name = None
            self.last_pdf_client_phone = None
            self.set_pdf_button_state("generate")

        except ValueError:
            self.show_error("Please enter valid numeric values in all fields.")
        except Exception as e:
            self.show_error(f"Unexpected error:\n{e}")

    # ------------------ PDF BUTTON STATE ------------------

    def set_pdf_button_state(self, state: str):
        self.pdf_state = state
        if state == "generate":
            self.pdf_button.setText("Generate")
            self.pdf_button.setStyleSheet("")
        else:
            self.pdf_button.setText("Send PDF")
            self.pdf_button.setStyleSheet("""
                QPushButton {
                    background-color: #22c55e;
                    border-radius: 10px;
                    padding: 8px 18px;
                    color: white;
                    font-weight: bold;
                }
                QPushButton:hover { background-color: #4ade80; }
                QPushButton:pressed { background-color: #16a34a; }
            """)

    def on_pdf_button_clicked(self):
        if self.pdf_state == "generate":
            self.generate_pdf_only()
        else:
            self.send_pdf_only()

    
    def on_prev_report_button_clicked(self):
        """Build and open the prior progress report for the selected client."""
        base_dir = ROOT_DIR
        reports_dir = REPORTS_DIR
        out_dir = OUTPUT_DIR
        tmpl_dir = TEMPLATE_DIR
        clients_root = CLIENTS_DIR

        # Resolve selected client (for per-client history lookup)
        client_name = "Client"
        client_phone = ""
        try:
            idx = self.client_combo.currentIndex()
            if idx > 0 and (idx - 1) < len(self.clients):
                row = self.clients[idx - 1]
                client_name = row.get("name", client_name) or client_name
                client_phone = row.get("phone", "") or ""
        except Exception:
            pass

        client_key_raw = f"{client_name}|{client_phone}"
        client_hash = hashlib.md5(client_key_raw.encode("utf-8")).hexdigest()[:12]
        client_dir = clients_root / f"{self._slug(client_name)}_{client_hash}"
        hist_dir = client_dir / "history"

        # Pick the PRIOR snapshot (2nd newest). If only one snapshot exists, use last_report.json.
        chosen = None
        try:
            if hist_dir.exists():
                snaps = sorted([p for p in hist_dir.glob("report_*.json") if p.is_file()])
                if len(snaps) >= 2:
                    chosen = snaps[-2]
                elif len(snaps) == 1:
                    chosen = snaps[-1]
            if chosen is None:
                lastp = client_dir / "last_report.json"
                if lastp.exists():
                    chosen = lastp
        except Exception:
            chosen = None

        if chosen is None or not chosen.exists():
            self.show_error("No previous report found yet for this client. Generate at least two reports first.")
            return

        build_py = tmpl_dir / "build_report.py"
        if not build_py.exists():
            self.show_error(f"Missing build_report.py at: {build_py}")
            return

        # Run builder using the chosen historical JSON (without overwriting reports/report.json)
        env = os.environ.copy()
        env["REPORT_JSON"] = str(chosen.resolve())

        try:
            subprocess.run([sys.executable, str(build_py)], cwd=str(tmpl_dir), env=env, check=True)
        except Exception as e:
            self.show_error(f"Failed to build previous report: {e}")
            return

        latest_html = out_dir / "latest.html"
        if latest_html.exists():
            open_file(latest_html)

    def send_pdf_only(self):
        """
        Placeholder: your send-via-iMessage logic can live here.
        (Keeping method so the "Send PDF" state doesn't crash.)
        """
        if not self.last_pdf_path:
            self.show_error("No PDF generated yet. Click Generate first.")
            self.set_pdf_button_state("generate")
            return

        # Implement your iMessage send routine here as desired.
        QMessageBox.information(self, "Send PDF", "Send PDF is not implemented in this cleaned version yet.")
        # Keep state as send (or flip back if you prefer)
        # self.set_pdf_button_state("generate")

    # ------------------ REPORT.JSON + TEMPLATE PIPELINE ------------------

    @staticmethod
    def _num(x, default=0.0) -> float:
        try:
            return float(x)
        except Exception:
            return float(default)

    def generate_pdf_only(self):
        """
        Generate reports/report.json from CURRENT GUI calculation, with Old/New comparisons.
        Then run progress_report_template/build_report.py (which reads ../reports/report.json)
        and exports reports/output/latest.pdf (no PNG).
        """
        if not getattr(self, "last_results", None):
            self.show_error("Please click Calculate before Generate.")
            return

        base_dir = ROOT_DIR
        reports_dir = REPORTS_DIR
        out_dir = OUTPUT_DIR
        tmpl_dir = TEMPLATE_DIR
        clients_root = CLIENTS_DIR
        reports_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        clients_root.mkdir(parents=True, exist_ok=True)
        # Selected client (for labels)
        client_name = "Client"
        client_phone = ""
        try:
            idx = self.client_combo.currentIndex()
            if idx > 0 and (idx - 1) < len(self.clients):
                c = self.clients[idx - 1]
                client_name = (c.get("name") or "Client").strip()
                client_phone = (c.get("phone") or "").strip()
        except Exception:
            pass

        # Load per-client previous report for "Old" baselines (so clients don't compare against each other)
        report_json_path = reports_dir / "report.json"  # current run (consumed by build_report.py)

        def _slug(s: str) -> str:
            s = (s or "").strip().lower()
            s = re.sub(r"[^a-z0-9]+", "_", s)
            return s.strip("_") or "client"

        # Build a stable per-client folder key
        clients_root = reports_dir / "clients"
        clients_root.mkdir(parents=True, exist_ok=True)

        client_key_raw = f"{client_name}|{client_phone}"
        client_hash = hashlib.md5(client_key_raw.encode("utf-8")).hexdigest()[:12]
        client_dir = clients_root / f"{self._slug(client_name)}_{client_hash}"
        client_dir.mkdir(parents=True, exist_ok=True)

        prev_data = None
        prev_path = client_dir / "last_report.json"

        # Initial report: baseline == current (no comparison)
        # Follow-up report: baseline comes from THIS client's last_report.json (if it exists)
        if self.report_mode == "followup" and prev_path.exists():
            try:
                prev_data = json.loads(prev_path.read_text(encoding="utf-8"))
            except Exception:
                prev_data = None
        else:
            prev_data = None
        res = self.last_results

        # Daily steps (for Recomp tiles)
        steps_val = int(self._num(res.get("steps", 0), 0))
        # Current values
        weight_lb = self._num(res.get("weight_lb", 0))
        bf_pct = self._num(res.get("bf_percent", 0))
        tdee = self._num(res.get("tdee_r", 0))
        cal_goal = self._num(res.get("cal_target_mid_r", 0))
        pullups = int(self._num(res.get("pullups", 0), 0))
        pushups = int(self._num(res.get("pushups", 0), 0))

        p_g = self._num(res.get("protein_mid_g_r", 0))
        c_g = self._num(res.get("carb_g_r", 0))
        f_g = self._num(res.get("fat_g_r", 0))

        # Old bars baseline = previous bars.curr
        prev_curr = None
        try:
            prev_curr = (prev_data or {}).get("bars", {}).get("curr", None)
        except Exception:
            prev_curr = None

        def old_at(i: int, fallback: float) -> float:
            try:
                if isinstance(prev_curr, list) and i < len(prev_curr):
                    return float(prev_curr[i])
            except Exception:
                pass
            return float(fallback)

        old_weight = old_at(0, weight_lb)
        old_bf = old_at(1, bf_pct)
        old_tdee = old_at(2, tdee)
        old_cal = old_at(3, cal_goal)
        old_pull = old_at(4, pullups)
        old_push = old_at(5, pushups)

        # Old macro grams baseline = previous tokens ROW_*_VALUE
        def old_macro(token_key: str, fallback_val: float) -> float:
            try:
                t = (prev_data or {}).get("tokens", {})
                s = str(t.get(token_key, "")).strip()
                mm = re.search(r"([0-9]+(?:\.[0-9]+)?)", s)
                if mm:
                    return float(mm.group(1))
            except Exception:
                pass
            return float(fallback_val)

        old_p = old_macro("ROW_1_VALUE", p_g)
        old_c = old_macro("ROW_2_VALUE", c_g)
        old_f = old_macro("ROW_3_VALUE", f_g)

        # Macro percents (by calories)
        pk, ck, fk = p_g * 4.0, c_g * 4.0, f_g * 9.0
        total = max(pk + ck + fk, 1.0)
        p_pct = int(round(pk / total * 100))
        c_pct = int(round(ck / total * 100))
        f_pct = max(0, 100 - p_pct - c_pct)

        # Goal timeline
        direction = str(res.get("direction", "")).strip().lower()
        pounds = self._num(res.get("pounds", 0))
        goal_weight = weight_lb
        if direction == "lose" and pounds > 0:
            goal_weight = weight_lb - pounds
        elif direction == "gain" and pounds > 0:
            goal_weight = weight_lb + pounds

        report_date = datetime.now().strftime("%m.%d.%y").lstrip("0").replace(".0", ".")

        tokens = {
            "REPORT_TITLE": "Goal Tracker",
            "REPORT_SUBTITLE": "",
            "CLIENT_LABEL": client_name,
            "REPORT_DATE": report_date,
            "DAILY_STEPS": str(steps_val),
            "HEADER_CHIP_1": "CUT" if direction == "lose" else ("GAIN" if direction == "gain" else "MAINT"),
            "HEADER_CHIP_2": "",
            "HEADER_CHIP_3": "XT",

            "MACROS_TITLE": "Protein/Carb/Fat",
            "PROTEIN_LABEL": "Protein",
            "PROTEIN_PCT": f"{p_pct}%",
            "CARB_LABEL": "Carbs",
            "CARB_PCT": f"{c_pct}%",
            "FAT_LABEL": "Fat",
            "FAT_PCT": f"{f_pct}%",
            "MACROS_FOOTNOTE": "Auto-calculated from daily macro targets.",

            "BARS_TITLE": "Composition changes",

            # Bottom-left "Old vs New" macros (grams)
            "TABLE_TITLE": "Dietary Changes",
            "TABLE_COL_1": "Macro",
            "TABLE_COL_2": "Old",
            "TABLE_COL_3": "New",
            "ROW_1_LABEL": "Protein",
            "ROW_1_MID": f"{int(round(old_p))} g",
            "ROW_1_VALUE": f"{int(round(p_g))} g",
            "ROW_2_LABEL": "Carbs",
            "ROW_2_MID": f"{int(round(old_c))} g",
            "ROW_2_VALUE": f"{int(round(c_g))} g",
            "ROW_3_LABEL": "Fats",
            "ROW_3_MID": f"{int(round(old_f))} g",
            "ROW_3_VALUE": f"{int(round(f_g))} g",

            # Badge placeholders intentionally omitted

            "LINE_TITLE": "Goal Timeline",
            "AXIS_TOP": "Weight (lb)",
            "AXIS_BOTTOM": "Start",
            "AXIS_RIGHT": "Goal",
        }

        data = {
            "dailySteps": steps_val,
            "tokens": tokens,
            "macros": [
                {"key": "Protein", "value": p_pct, "color": "var(--orange)"},
                {"key": "Carbs", "value": c_pct, "color": "var(--teal)"},
                {"key": "Fat", "value": f_pct, "color": "var(--teal2)"},
            ],
            "bars": {
                "labels": ["Weight", "Bodyfat %", "TDEE", "Daily Calorie Goal", "Pull-Ups", "Push-Ups"],
                "prev": [round(old_weight, 1), round(old_bf, 1), round(old_tdee), round(old_cal), int(round(old_pull)), int(round(old_push))],
                "curr": [round(weight_lb, 1), round(bf_pct, 1), round(tdee), round(cal_goal), pullups, pushups],
            },
            "goalTimeline": {
                "startWeight": round(weight_lb, 1),
                "goalWeight": round(goal_weight, 1),
                "estWeeksMin": 10,
                "estWeeksMax": 12,
                "rateLbPerWeek": 0.9,
                "variationPct": 0.2,
                "yTicks": [190, 187, 184, 181, 178, 172],
            },
        }

        # Write new report.json (this becomes baseline for NEXT run)
        report_json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        # Persist this client's report as their new baseline for future follow-ups
        try:
            (client_dir / "last_report.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            hist_dir = client_dir / "history"
            hist_dir.mkdir(parents=True, exist_ok=True)
            (hist_dir / f"report_{ts}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass


        # Run build_report.py (reads ../reports/report.json and writes ../reports/output/latest.*)
        build_py = tmpl_dir / "build_report.py"
        if not build_py.exists():
            raise RuntimeError(f"Missing build_report.py at: {build_py}")

        subprocess.run([sys.executable, str(build_py)], cwd=str(tmpl_dir), check=True)

        latest_html = out_dir / "latest.html"
        if latest_html.exists():
            open_file(latest_html)

        pdf_path = out_dir / "latest.pdf"

        # Flip button to "Send PDF"
        self.last_pdf_path = str(pdf_path)
        self.last_pdf_client_name = client_name
        self.last_pdf_client_phone = client_phone
        self.set_pdf_button_state("send")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_dark_theme(app)

    dlg = ReportTypeDialog()
    if dlg.exec_() != QDialog.Accepted:
        sys.exit(0)

    window = DietCalculator(report_mode=dlg.choice())
    window.show()
    sys.exit(app.exec_())