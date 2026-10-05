"""DroidLogsPilot desktop UI - Android log capture and API diagnostics.

Requires Android platform-tools (adb) on PATH.  Install PDF support with:
    pip install reportlab
"""
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ── Optional dependency ────────────────────────────────────────────────────────
try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph,
                                    SimpleDocTemplate, Spacer, Table, TableStyle)
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

APP_NAME  = "DroidLogsPilot"
VERSION   = "3.1.0"

# ── Regex patterns ─────────────────────────────────────────────────────────────
ERROR_MARKERS    = ("fatal exception", "androidruntime", "exception", " error", "/e", " 5xx", " 4xx")
HTTP_PATTERN     = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(https?://[^\s'\"}]+|/[^\s'\"}]+)", re.I)
STATUS_PATTERN   = re.compile(r"\b(?:HTTP/?\d?(?:\.\d)?\s+|status(?:\s*code)?[=: ]+)([1-5]\d\d)\b", re.I)
PAYLOAD_PATTERN  = re.compile(r"(?:request|response|body|payload)[=:]\s*(\{.*?\}|\[.*?\])", re.I)
URL_PATTERN      = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")
LOG_TIME_PATTERN = re.compile(r"\b(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})")
DURATION_PATTERN = re.compile(r"\b(?:took|duration|latency|time)[=: ]+(\d+(?:\.\d+)?\s*(?:ms|s))", re.I)

# BUG-FIX: bearer tokens and values after 'Authorization:' were not redacted because
# the separator group (["\s:=]+) consumed 'Bearer ' leaving the actual token exposed.
# Now we apply two passes: first a generic key=value/key:value pattern, then a
# dedicated Authorization header pass that strips the whole scheme+token.
_REDACT_KV   = re.compile(
    r'(?i)(authorization|token|password|secret|api[_\-]?key)\s*(["\s:=]+)\s*(?:Bearer\s+|Basic\s+)?([^,}\s"\']{4,})',
)
_REDACT_AUTH = re.compile(r'(?i)(Authorization\s*:\s*)(?:Bearer|Basic|Token)\s+[A-Za-z0-9_.+/=\-]{4,}')


def redact(value: str) -> str:
    """Mask common secrets before showing or saving data in a report."""
    value = _REDACT_AUTH.sub(r"\1[REDACTED]", value)
    value = _REDACT_KV.sub(r"\1\2[REDACTED]", value)
    return value


# ── Tiny helpers ───────────────────────────────────────────────────────────────

def adb(args, device=None, timeout=12):
    command = ["adb"] + (["-s", device] if device else []) + args
    return subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=timeout)


def log_level(line: str) -> str:
    if re.search(r"\b[EF]/|\s[EF]\s|\b(?:fatal|exception|error)\b", line, re.I):
        return "Error"
    if re.search(r"\bW/|\sW\s|\bwarning\b", line, re.I):
        return "Warning"
    if re.search(r"\bI/|\sI\s", line):
        return "Info"
    return "Debug"


def compact(value, limit=900) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value if len(value) <= limit else value[:limit - 1] + "…"


def parse_api_event(line: str) -> dict | None:
    method_url = HTTP_PATTERN.search(line)
    status     = STATUS_PATTERN.search(line)
    payload    = PAYLOAD_PATTERN.search(line)
    if not (method_url or status or payload):
        return None
    method, url = method_url.groups() if method_url else ("-", "Not logged")
    code = status.group(1) if status else "Not logged"
    kind = "HTTP error" if code != "Not logged" and int(code) >= 400 else "API event"
    if "timeout" in line.lower():
        kind = "Timeout"
    elif "ssl" in line.lower() or "certificate" in line.lower():
        kind = "TLS / certificate"
    elif "unknownhost" in line.lower() or "dns" in line.lower():
        kind = "DNS / network"
    timestamp = LOG_TIME_PATTERN.search(line)
    duration  = DURATION_PATTERN.search(line)
    return {
        "timestamp": timestamp.group(1) if timestamp else "Not logged",
        "duration":  duration.group(1)  if duration  else "Not logged",
        "method":  method.upper(),
        "url":     redact(compact(url, 300)),
        "status":  code,
        "type":    kind,
        "payload": redact(compact(payload.group(1) if payload else "Not logged")),
        "retry":   0,
        "raw":     redact(compact(line, 1100)),
    }


# ── Palette ───────────────────────────────────────────────────────────────────
# Deep navy-to-slate base, emerald accent, amber/rose for warnings/errors.
# Monospace panels use a near-black with subtle blue tint for easy reading.

BG          = "#0d1117"   # main window
BG_CARD     = "#161b22"   # panel / card
BG_PANEL    = "#080d14"   # log / API text areas
BG_INPUT    = "#1c2230"   # text inputs, comboboxes
BORDER      = "#30363d"
FG          = "#e6edf3"   # primary text
FG_DIM      = "#8b949e"   # hints, labels
FG_TITLE    = "#ffffff"
ACCENT      = "#238636"   # green start / success
ACCENT_HOV  = "#2ea043"
STOP_CLR    = "#da3633"   # red stop
STOP_HOV    = "#f85149"
TAG_ERR     = "#ff7b72"
TAG_WARN    = "#d29922"
TAG_INFO    = "#58a6ff"
TAG_DEBUG   = "#8b949e"
EMERALD     = "#3fb950"   # live status dot / highlights
AMBER       = "#d29922"
ROSE        = "#ff7b72"
MONO_FONT   = ("Cascadia Code", 9)
UI_FONT     = ("Segoe UI", 10)
UI_SEMI     = ("Segoe UI Semibold", 10)
UI_TITLE    = ("Segoe UI Semibold", 22)
UI_CAPTION  = ("Segoe UI", 9)


class RecorderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}  v{VERSION}")
        self.geometry("1280x820")
        self.minsize(960, 660)
        self.configure(bg=BG)

        self.events             = queue.Queue()
        self.lines: list        = []
        self.stats              = Counter()
        self.api_events: list   = []
        self.errors: list       = []
        self.request_attempts   = Counter()
        self.error_fingerprints: set = set()
        self.screenshot_paths: list  = []
        self.pending_screenshots     = 0
        self._screenshot_lock        = threading.Lock()   # BUG-FIX: thread safety for counter
        self.session_temp_dir        = None
        self.process                 = None
        self.started_at              = None
        self.devices: list           = []
        self._recording              = False

        self._apply_style()
        self._build_ui()
        self.after(120, self._drain_events)
        self.refresh_devices()
        # BUG-FIX: clean up temp dir on close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Styles ─────────────────────────────────────────────────────────────────

    def _apply_style(self):
        self.option_add("*TCombobox*Listbox.background",       BG_INPUT)
        self.option_add("*TCombobox*Listbox.foreground",       FG)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.option_add("*TCombobox*Listbox.font",             UI_FONT)
        s = ttk.Style(self)
        s.theme_use("clam")
        # Frames
        s.configure("TFrame",      background=BG)
        s.configure("Card.TFrame", background=BG_CARD)
        s.configure("Bar.TFrame",  background=BG_CARD, relief="flat")
        # Labels
        s.configure("TLabel",       background=BG,      foreground=FG,      font=UI_FONT)
        s.configure("Card.TLabel",  background=BG_CARD, foreground=FG,      font=UI_FONT)
        s.configure("Dim.TLabel",   background=BG,      foreground=FG_DIM,  font=UI_CAPTION)
        s.configure("CDim.TLabel",  background=BG_CARD, foreground=FG_DIM,  font=UI_CAPTION)
        s.configure("Title.TLabel", background=BG,      foreground=FG_TITLE,font=UI_TITLE)
        s.configure("Head.TLabel",  background=BG_CARD, foreground=FG_TITLE,font=("Segoe UI Semibold", 11))
        # Buttons
        s.configure("TButton",          font=UI_SEMI,   padding=(14, 8), relief="flat")
        s.configure("Accent.TButton",   background=ACCENT,   foreground="#ffffff", font=UI_SEMI, padding=(14, 8))
        s.map("Accent.TButton",         background=[("active", ACCENT_HOV), ("disabled", BORDER)])
        s.configure("Stop.TButton",     background=STOP_CLR, foreground="#ffffff", font=UI_SEMI, padding=(14, 8))
        s.map("Stop.TButton",           background=[("active", STOP_HOV),   ("disabled", BORDER)])
        s.configure("Ghost.TButton",    background=BG_INPUT, foreground=FG,       font=UI_SEMI, padding=(10, 7))
        s.map("Ghost.TButton",          background=[("active", BORDER)])
        # Combobox
        s.configure("TCombobox", fieldbackground=BG_INPUT, background=BG_INPUT,
                     foreground=FG, arrowcolor=FG_DIM, insertcolor=FG)
        s.map("TCombobox",
              fieldbackground=[("readonly", BG_INPUT)],
              foreground=[("readonly", FG)])
        # Checkbutton
        s.configure("TCheckbutton", background=BG_CARD, foreground=FG_DIM, font=UI_CAPTION)
        s.map("TCheckbutton",       background=[("active", BG_CARD)], foreground=[("active", FG)])
        # Separator
        s.configure("TSeparator",   background=BORDER)
        # Notebook (tabs)
        s.configure("TNotebook",               background=BG_CARD, borderwidth=0)
        s.configure("TNotebook.Tab",           background=BG,      foreground=FG_DIM,
                     font=UI_SEMI, padding=(14, 7))
        s.map("TNotebook.Tab",                 background=[("selected", BG_CARD)],
              foreground=[("selected", FG)])
        # Scrollbar
        s.configure("Slim.Vertical.TScrollbar",   background=BG_CARD, troughcolor=BG_PANEL,
                     arrowcolor=FG_DIM, relief="flat", width=8)
        s.configure("Slim.Horizontal.TScrollbar", background=BG_CARD, troughcolor=BG_PANEL,
                     arrowcolor=FG_DIM, relief="flat", width=8)
        # Progressbar (used for the live status indicator)
        s.configure("Live.TLabel", background=BG_CARD, foreground=EMERALD, font=("Segoe UI", 9))

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Top bar ────────────────────────────────────────────────────────────
        topbar = ttk.Frame(self, padding=(28, 18, 28, 0))
        topbar.pack(fill="x")
        left = ttk.Frame(topbar)
        left.pack(side="left")
        ttk.Label(left, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(left, text="Capture Android diagnostics · surface API failures · export PDF",
                  style="Dim.TLabel").pack(anchor="w", pady=(2, 0))
        # live indicator on the right
        self._dot_var = tk.StringVar(value="⬤  Idle")
        self._dot_lbl = ttk.Label(topbar, textvariable=self._dot_var, style="Live.TLabel")
        self._dot_lbl.pack(side="right", padx=(0, 4), anchor="e")
        self._dot_lbl.configure(foreground=FG_DIM)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=28, pady=(14, 0))

        # ── Controls card ──────────────────────────────────────────────────────
        ctrl = ttk.Frame(self, style="Card.TFrame", padding=(22, 16, 22, 16))
        ctrl.pack(fill="x", padx=28, pady=(12, 0))
        ctrl.columnconfigure(1, weight=1); ctrl.columnconfigure(4, weight=2)

        # Row 0: device + package selectors
        ttk.Label(ctrl, text="Device", style="CDim.TLabel").grid(row=0, column=0, sticky="w")
        self.device_var = tk.StringVar()
        self.device_box = ttk.Combobox(ctrl, textvariable=self.device_var, state="readonly", width=28)
        self.device_box.grid(row=0, column=1, sticky="ew", padx=(8, 10))
        self.device_box.bind("<<ComboboxSelected>>", self._device_selected)
        ttk.Button(ctrl, text="↺ Refresh", style="Ghost.TButton",
                   command=self.refresh_devices).grid(row=0, column=2, padx=(0, 22))

        ttk.Label(ctrl, text="Package", style="CDim.TLabel").grid(row=0, column=3, sticky="w")
        self.package_var = tk.StringVar()
        self.package_box = ttk.Combobox(ctrl, textvariable=self.package_var, state="readonly", width=36)
        self.package_box.grid(row=0, column=4, sticky="ew", padx=(8, 10))
        self.package_box.bind("<<ComboboxSelected>>", self._package_selected)
        ttk.Button(ctrl, text="Load apps", style="Ghost.TButton",
                   command=self.load_packages).grid(row=0, column=5)

        # Row 1: options + action buttons
        opts = ttk.Frame(ctrl, style="Card.TFrame")
        opts.grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self.clear_var = tk.BooleanVar(value=True)
        self.third_party_var = tk.BooleanVar(value=True)
        self.auto_screenshot_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Clear buffer before capture",
                        variable=self.clear_var).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(opts, text="User apps only",
                        variable=self.third_party_var).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(opts, text="Auto-screenshot on errors",
                        variable=self.auto_screenshot_var).pack(side="left")

        btns = ttk.Frame(ctrl, style="Card.TFrame")
        btns.grid(row=1, column=3, columnspan=3, sticky="e", pady=(12, 0))
        self.start_btn = ttk.Button(btns, text="▶  Start recording",
                                    style="Accent.TButton", command=self.start)
        self.start_btn.pack(side="left", padx=(0, 10))
        self.stop_btn = ttk.Button(btns, text="■  Stop & export PDF",
                                   style="Stop.TButton", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left")

        # Row 2: notes
        ttk.Label(ctrl, text="Session notes  (steps · expected vs actual result)",
                  style="CDim.TLabel").grid(row=2, column=0, columnspan=6, sticky="w", pady=(14, 4))
        self.notes_box = tk.Text(ctrl, height=2, background=BG_INPUT, foreground=FG,
                                  insertbackground=FG, relief="flat", wrap="word",
                                  font=UI_FONT, padx=8, pady=6)
        self.notes_box.grid(row=3, column=0, columnspan=6, sticky="ew")

        # ── Status bar ─────────────────────────────────────────────────────────
        sbar = ttk.Frame(self, padding=(28, 6, 28, 0))
        sbar.pack(fill="x")
        self.status_var = tk.StringVar(value="Checking ADB connection…")
        ttk.Label(sbar, textvariable=self.status_var, style="Dim.TLabel").pack(side="left")
        self.counter_var = tk.StringVar(value="0 logs  ·  0 errors  ·  0 API events")
        ttk.Label(sbar, textvariable=self.counter_var, style="Dim.TLabel").pack(side="right")

        # ── Main split view ────────────────────────────────────────────────────
        split = ttk.PanedWindow(self, orient="horizontal")
        split.pack(fill="both", expand=True, padx=28, pady=(8, 24))

        # Left: Log viewer
        log_card = ttk.Frame(split, style="Card.TFrame", padding=(14, 12, 14, 14))
        split.add(log_card, weight=3)
        log_header = ttk.Frame(log_card, style="Card.TFrame")
        log_header.pack(fill="x", pady=(0, 8))
        ttk.Label(log_header, text="Live log", style="Head.TLabel").pack(side="left")
        self._log_count_var = tk.StringVar(value="")
        ttk.Label(log_header, textvariable=self._log_count_var, style="CDim.TLabel").pack(side="right")

        log_wrap = ttk.Frame(log_card, style="Card.TFrame")
        log_wrap.pack(fill="both", expand=True)
        self.log_view = tk.Text(log_wrap, background=BG_PANEL, foreground=FG_DIM,
                                 insertbackground=FG, relief="flat", wrap="none",
                                 font=MONO_FONT, state="disabled",
                                 selectbackground=ACCENT, selectforeground="#ffffff")
        # BUG-FIX: add scrollbars
        vsb_log = ttk.Scrollbar(log_wrap, orient="vertical",   command=self.log_view.yview, style="Slim.Vertical.TScrollbar")
        hsb_log = ttk.Scrollbar(log_wrap, orient="horizontal", command=self.log_view.xview, style="Slim.Horizontal.TScrollbar")
        self.log_view.configure(yscrollcommand=vsb_log.set, xscrollcommand=hsb_log.set)
        vsb_log.pack(side="right",  fill="y")
        hsb_log.pack(side="bottom", fill="x")
        self.log_view.pack(fill="both", expand=True)
        for tag, color in [("Error", TAG_ERR), ("Warning", TAG_WARN),
                            ("Info", TAG_INFO), ("Debug", TAG_DEBUG)]:
            self.log_view.tag_configure(tag, foreground=color)

        # Right: tabbed API + filter panel
        right_card = ttk.Frame(split, style="Card.TFrame", padding=(14, 12, 14, 14))
        split.add(right_card, weight=2)
        ttk.Label(right_card, text="API timeline", style="Head.TLabel").pack(anchor="w", pady=(0, 8))

        api_wrap = ttk.Frame(right_card, style="Card.TFrame")
        api_wrap.pack(fill="both", expand=True)
        self.api_view = tk.Text(api_wrap, background=BG_PANEL, foreground=FG_DIM,
                                 insertbackground=FG, relief="flat", wrap="none",
                                 font=MONO_FONT, state="disabled",
                                 selectbackground=ACCENT, selectforeground="#ffffff")
        # BUG-FIX: scrollbars for api panel too
        vsb_api = ttk.Scrollbar(api_wrap, orient="vertical",   command=self.api_view.yview, style="Slim.Vertical.TScrollbar")
        hsb_api = ttk.Scrollbar(api_wrap, orient="horizontal", command=self.api_view.xview, style="Slim.Horizontal.TScrollbar")
        self.api_view.configure(yscrollcommand=vsb_api.set, xscrollcommand=hsb_api.set)
        vsb_api.pack(side="right",  fill="y")
        hsb_api.pack(side="bottom", fill="x")
        self.api_view.pack(fill="both", expand=True)
        for tag, color in [("err", TAG_ERR), ("ok", EMERALD), ("warn", AMBER),
                            ("dim", FG_DIM),  ("declared", TAG_INFO)]:
            self.api_view.tag_configure(tag, foreground=color)

    # ── Device / package helpers ───────────────────────────────────────────────

    def selected_device(self) -> str:
        return self.device_var.get().split("  ")[0]

    def _device_selected(self, _e=None):
        self.status_var.set(f"Device: {self.selected_device()}  — Load apps to choose a package.")

    def _package_selected(self, _e=None):
        self.status_var.set(f"Package: {self.package_var.get()}")

    def refresh_devices(self):
        if not shutil.which("adb"):
            self.status_var.set("adb not found — install Android platform-tools and add it to PATH.")
            return
        try:
            result = adb(["devices"])
        except Exception as exc:
            self.status_var.set(f"Cannot start ADB: {exc}")
            return
        self.devices = [
            line.split()[0] for line in result.stdout.splitlines()[1:]
            if line.endswith("\tdevice")
        ]
        self.device_box["values"] = self.devices
        if self.devices:
            self.device_var.set(self.devices[0])
            self.status_var.set(f"{len(self.devices)} device(s) ready.  Load apps to choose a package.")
        else:
            self.status_var.set("No authorized device — enable USB debugging, then tap Refresh.")

    def load_packages(self):
        device = self.selected_device()
        if not device:
            return messagebox.showwarning(APP_NAME, "Select a connected device first.")
        self.status_var.set("Loading packages…"); self.update_idletasks()
        # BUG-FIX: check returncode before consuming output
        result = adb(["shell", "pm", "list", "packages"] +
                     (["-3"] if self.third_party_var.get() else []), device)
        if result.returncode != 0:
            self.status_var.set("ADB error reading packages — check the device connection.")
            return
        packages = sorted(
            line.replace("package:", "") for line in result.stdout.splitlines()
            if line.startswith("package:")
        )
        self.package_box["values"] = packages
        if packages:
            self.package_var.set(packages[0])
            self.status_var.set(f"Loaded {len(packages)} packages.  Select one and start recording.")
        else:
            self.status_var.set("No packages found — check the device connection.")

    # ── Recording ──────────────────────────────────────────────────────────────

    def start(self):
        device, package = self.selected_device(), self.package_var.get()
        if not device or not package:
            return messagebox.showwarning(APP_NAME, "Select a device and a package first.")
        # Reset state
        self.lines, self.api_events, self.errors = [], [], []
        self.stats.clear(); self.request_attempts.clear(); self.error_fingerprints.clear()
        self.screenshot_paths, self.pending_screenshots = [], 0
        self.session_temp_dir = Path(tempfile.mkdtemp(prefix="droidlogspilot_"))
        self._set_text(self.log_view, ""); self._set_text(self.api_view, "")

        # BUG-FIX: check adb clear returncode
        if self.clear_var.get():
            r = adb(["logcat", "-c"], device)
            if r.returncode != 0:
                self.status_var.set("Warning: could not clear logcat buffer — continuing anyway.")

        pid_result = adb(["shell", "pidof", "-s", package], device)
        pid = pid_result.stdout.strip() if pid_result.returncode == 0 else ""
        command = ["adb", "-s", device, "logcat", "-v", "threadtime"]
        if pid.isdigit():
            command.extend(["--pid", pid])

        self.process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace"
        )
        self._recording   = True
        self.started_at   = datetime.now()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._dot_var.set("⬤  Recording")
        self._dot_lbl.configure(foreground=EMERALD)
        self.status_var.set(
            f"Recording  {package}" +
            (f"  (PID {pid})" if pid.isdigit() else "  — launch the app to see its logs.")
        )
        threading.Thread(target=self._reader,              daemon=True).start()
        threading.Thread(target=self._endpoint_catalogue,
                         args=(device, package),          daemon=True).start()

    # BUG-FIX: check self.process inside the loop, not after readline blocks
    def _reader(self):
        proc = self.process                     # local ref so None-set doesn't crash the check
        for line in iter(proc.stdout.readline, ""):
            if self.process is None:            # stop() already ran
                break
            self.events.put(line)

    def stop(self):
        self._recording = False
        if self.process:
            self.process.terminate()
            self.process = None
        self.stop_btn.config(state="disabled")
        self.start_btn.config(state="normal")
        self._dot_var.set("⬤  Idle")
        self._dot_lbl.configure(foreground=FG_DIM)
        if not self.lines:
            self.status_var.set("Recording stopped — no log lines were captured.")
            return
        if not REPORTLAB_AVAILABLE:
            messagebox.showerror(APP_NAME, "PDF support missing.\nRun:  pip install reportlab")
            return
        folder = filedialog.askdirectory(title="Choose folder for the PDF report")
        if not folder:
            self.status_var.set("Recording stopped — PDF export was cancelled.")
            return
        path = Path(folder) / f"DroidLogsPilot_{datetime.now():%Y%m%d_%H%M%S}.pdf"
        try:
            self.create_pdf(path)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not create report:\n{exc}")
            return
        self.status_var.set(f"Report saved: {path}")
        messagebox.showinfo(APP_NAME, f"PDF report created:\n{path}")

    # ── Event loop ─────────────────────────────────────────────────────────────

    def _drain_events(self):
        try:
            while True:
                item = self.events.get_nowait()
                if isinstance(item, tuple):
                    kind, data = item
                    if kind == "catalogue":
                        if data:
                            self._api_append("\n─── API endpoints declared in this APK ───\n", "dim")
                            for url in data:
                                self.api_events.append({
                                    "timestamp": "APK scan", "duration": "-", "retry": 0,
                                    "type": "Declared endpoint", "method": "-",
                                    "url": url, "status": "Static",
                                    "payload": "Found in APK", "raw": url,
                                })
                                self._api_append(f"  DECLARED  {url}\n", "declared")
                        else:
                            self._api_append("\nNo hard-coded URLs found in APK. Live calls appear here.\n", "dim")
                    elif kind == "catalogue_error":
                        self._api_append("\nAPK scan failed — live API calls will still appear here.\n", "dim")
                    elif kind == "screenshot":
                        self.screenshot_paths.append(data)
                        self._api_append(f"  EVIDENCE  Screenshot → {Path(data).name}\n", "ok")
                    continue
                line = item
                self.lines.append(line)
                level = log_level(line)
                self.stats[level] += 1
                if level == "Error":
                    self.errors.append(redact(compact(line, 1200)))
                    self._queue_screenshot(self.selected_device(), f"log:{compact(line, 180)}")
                event = parse_api_event(line)
                if event:
                    key = (event["method"], event["url"])
                    self.request_attempts[key] += 1
                    event["retry"] = self.request_attempts[key] - 1
                    self.api_events.append(event)
                    tag = "err" if event["type"] in ("HTTP error", "Timeout",
                                                      "TLS / certificate", "DNS / network") else "ok"
                    self._api_append(
                        f"  {event['timestamp']}  {event['method']:6}  {event['url']}\n"
                        f"    ↳ {event['status']}  {event['duration']}  retry={event['retry']}  [{event['type']}]\n",
                        tag,
                    )
                    if tag == "err":
                        self._queue_screenshot(self.selected_device(),
                                               f"api:{event['type']}:{event['url']}:{event['status']}")
                self._log_append(redact(line), level)
        except queue.Empty:
            pass
        self.counter_var.set(
            f"{len(self.lines)} logs  ·  {self.stats['Error']} errors  ·  {len(self.api_events)} API events"
        )
        self._log_count_var.set(f"{len(self.lines)} lines")
        self.after(120, self._drain_events)

    def _log_append(self, text, level=None):
        v = self.log_view
        v.config(state="normal")
        v.insert("end", text, level or "")
        v.see("end")
        v.config(state="disabled")

    def _api_append(self, text, tag=None):
        v = self.api_view
        v.config(state="normal")
        v.insert("end", text, tag or "")
        v.see("end")
        v.config(state="disabled")

    def _append(self, view, text, tag=None):  # kept for backwards-compat internally
        view.config(state="normal"); view.insert("end", text, tag or ""); view.see("end"); view.config(state="disabled")

    def _set_text(self, view, text):
        view.config(state="normal"); view.delete("1.0", "end"); view.insert("end", text); view.config(state="disabled")

    # ── Screenshots ────────────────────────────────────────────────────────────

    def _queue_screenshot(self, device, fingerprint):
        """Capture up to 10 distinct failure states without blocking the UI."""
        with self._screenshot_lock:            # BUG-FIX: protect counter + set update atomically
            if (not self.auto_screenshot_var.get()
                    or fingerprint in self.error_fingerprints
                    or len(self.screenshot_paths) + self.pending_screenshots >= 10):
                return
            self.error_fingerprints.add(fingerprint)
            self.pending_screenshots += 1
        threading.Thread(target=self._capture_screenshot, args=(device,), daemon=True).start()

    def _capture_screenshot(self, device):
        ts          = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        remote_path = f"/sdcard/droidlogspilot_{ts}.png"
        local_path  = self.session_temp_dir / f"error_{ts}.png"
        try:
            cap  = adb(["shell", "screencap", "-p", remote_path], device, timeout=30)
            pull = adb(["pull", remote_path, str(local_path)], device, timeout=30) if cap.returncode == 0 else None
            if pull and pull.returncode == 0 and local_path.exists():
                self.events.put(("screenshot", str(local_path)))
        except Exception:
            pass
        finally:
            try: adb(["shell", "rm", remote_path], device)
            except Exception: pass
            with self._screenshot_lock:
                self.pending_screenshots = max(0, self.pending_screenshots - 1)

    # ── APK endpoint catalogue ─────────────────────────────────────────────────

    def _endpoint_catalogue(self, device, package):
        try:
            paths    = adb(["shell", "pm", "path", package], device).stdout.splitlines()
            apk_list = [l.split("package:", 1)[1].strip() for l in paths if l.startswith("package:")]
            endpoints = set()
            with tempfile.TemporaryDirectory(prefix="droidlogspilot_") as tmp:
                for idx, remote in enumerate(apk_list):
                    local = os.path.join(tmp, f"part_{idx}.apk")
                    pulled = adb(["pull", remote, local], device, timeout=90)
                    if pulled.returncode != 0:
                        continue
                    try:
                        with zipfile.ZipFile(local) as apk:
                            for name in apk.namelist():
                                if name.endswith((".dex", ".xml", ".json", ".txt")):
                                    endpoints.update(
                                        URL_PATTERN.findall(
                                            apk.read(name).decode("latin-1", errors="ignore")
                                        )
                                    )
                    except zipfile.BadZipFile:
                        continue
            self.events.put(("catalogue", sorted(ep.rstrip(".,;)'\"") for ep in endpoints)[:300]))
        except Exception as exc:
            self.events.put(("catalogue_error", str(exc)))

    # ── PDF export ─────────────────────────────────────────────────────────────

    def create_pdf(self, path):
        styles   = getSampleStyleSheet()
        s_title  = ParagraphStyle("DTitle",    parent=styles["Title"],
                                   textColor=colors.HexColor("#238636"), fontSize=24,
                                   leading=30, alignment=TA_CENTER)
        s_sub    = ParagraphStyle("DSub",      parent=styles["Normal"],
                                   textColor=colors.HexColor("#57606a"), fontSize=11,
                                   leading=15, alignment=TA_CENTER, spaceAfter=8)
        s_h      = ParagraphStyle("DH2",       parent=styles["Heading2"],
                                   textColor=colors.HexColor("#0f766e"), spaceBefore=14, spaceAfter=7)
        s_body   = ParagraphStyle("DBody",     parent=styles["BodyText"], fontSize=8.3, leading=11)
        s_mono   = ParagraphStyle("DMono",     parent=styles["BodyText"], fontSize=7.5, leading=10,
                                   fontName="Courier")
        doc = SimpleDocTemplate(str(path), pagesize=A4,
                                 rightMargin=15*mm, leftMargin=15*mm,
                                 topMargin=15*mm, bottomMargin=15*mm)
        dur = str(datetime.now() - self.started_at).split(".")[0] if self.started_at else "Unknown"

        story = [
            Paragraph("DroidLogsPilot", s_title),
            Paragraph("Android API &amp; diagnostic session report", s_sub),
            Spacer(1, 8),
        ]
        # Session summary
        details = [
            ["Package",             self.package_var.get()],
            ["Device",              self.selected_device()],
            ["Recorded",            self.started_at.strftime("%Y-%m-%d %H:%M:%S") if self.started_at else "-"],
            ["Duration",            dur],
            ["Captured log lines",  str(len(self.lines))],
        ]
        story += [Paragraph("Session summary", s_h), self._table(details, [35*mm, 140*mm]), Spacer(1, 8)]
        stats_rows = [
            ["Errors",           str(self.stats["Error"])],
            ["Warnings",         str(self.stats["Warning"])],
            ["Info",             str(self.stats["Info"])],
            ["Debug / other",    str(self.stats["Debug"])],
            ["API events found", str(len(self.api_events))],
        ]
        story.append(self._table(stats_rows, [55*mm, 35*mm]))

        # Tester notes
        notes = self.notes_box.get("1.0", "end-1c").strip()
        def esc(s): return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        story += [
            Paragraph("Tester notes", s_h),
            Paragraph(esc(notes).replace("\n", "<br/>") if notes else "No session notes added.", s_body),
        ]

        # Capture caveat
        story += [
            Paragraph("Important capture note", s_h),
            Paragraph(
                "Only request, response, status, and payload details emitted to Android logcat are captured. "
                "HTTPS traffic is not decrypted. Common secrets are redacted before export.", s_body
            ),
        ]

        # API timeline
        story.append(Paragraph("API timeline", s_h))
        if self.api_events:
            rows = [["Time", "Request", "Status", "Duration", "Retry", "Payload / excerpt"]]
            for e in self.api_events[:300]:
                rows.append([e["timestamp"], f"{e['method']} {e['url']}", e["status"],
                              e["duration"], str(e["retry"]), e["payload"]])
            story.append(self._table(rows, [22*mm, 45*mm, 15*mm, 19*mm, 14*mm, 65*mm], header=True))
        else:
            story.append(Paragraph("No structured API events found.", s_body))

        # Errors
        story.append(Paragraph("Error details", s_h))
        if self.errors:
            for idx, err in enumerate(self.errors[:150], 1):
                story.append(KeepTogether([
                    Paragraph(f"Error {idx}", styles["Heading4"]),
                    Paragraph(esc(err), s_mono),
                    Spacer(1, 5),
                ]))
        else:
            story.append(Paragraph("No error-level entries detected.", s_body))

        # Evidence screenshots
        evidence = []
        if self.screenshot_paths:
            ev_dir = path.parent / f"{path.stem}_evidence"
            ev_dir.mkdir(exist_ok=True)
            for src in self.screenshot_paths:
                sp = Path(src)
                if sp.exists():
                    dst = ev_dir / sp.name
                    shutil.copy2(sp, dst)
                    evidence.append(dst)
        story.append(Paragraph("Failure screenshots", s_h))
        if evidence:
            story.append(Paragraph(
                f"{len(evidence)} screenshot(s) saved in '{path.stem}_evidence' next to this PDF.", s_body
            ))
            for ss in evidence[:10]:
                try:
                    ir = ImageReader(str(ss)); w, h = ir.getSize()
                    scale = min((175*mm)/w, (105*mm)/h)
                    story += [Spacer(1, 6), Paragraph(ss.name, styles["Heading4"]),
                              Image(str(ss), width=w*scale, height=h*scale)]
                except Exception:
                    story.append(Paragraph(f"Screenshot: {ss.name}", s_body))
        else:
            story.append(Paragraph("No error screenshots captured.", s_body))

        # Full log appendix
        story += [PageBreak(), Paragraph("Captured log appendix", s_h)]
        for line in self.lines[:4000]:
            story.append(Paragraph(esc(redact(compact(line, 1600))), s_mono))

        doc.build(story, onFirstPage=self._footer, onLaterPages=self._footer)

    def _table(self, rows, widths, header=False):
        def esc(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        cell_style = ParagraphStyle("Cell", fontSize=8, leading=10)
        tbl = Table(
            [[Paragraph(esc(cell), cell_style) for cell in row] for row in rows],
            colWidths=widths, repeatRows=(1 if header else 0),
        )
        cmds = [
            ("GRID",       (0,0), (-1,-1), .35, colors.HexColor("#cbd5e1")),
            ("VALIGN",     (0,0), (-1,-1), "TOP"),
            ("BACKGROUND", (0,0), (-1, 0), colors.HexColor("#e2e8f0")),
        ]
        if header:
            cmds += [
                ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#0f766e")),
                ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ]
        tbl.setStyle(TableStyle(cmds))
        return tbl

    @staticmethod
    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.setFont("Helvetica", 8)
        canvas.drawString(15*mm, 10*mm, f"{APP_NAME} {VERSION}  —  {datetime.now():%Y-%m-%d %H:%M}")
        canvas.drawRightString(195*mm, 10*mm, f"Page {doc.page}")
        canvas.restoreState()

    # ── Cleanup ────────────────────────────────────────────────────────────────

    def _on_close(self):
        """BUG-FIX: stop recording and clean up temp evidence folder on exit."""
        if self.process:
            self.process.terminate()
            self.process = None
        if self.session_temp_dir and self.session_temp_dir.exists():
            try: shutil.rmtree(self.session_temp_dir, ignore_errors=True)
            except Exception: pass
        self.destroy()


if __name__ == "__main__":
    RecorderApp().mainloop()
