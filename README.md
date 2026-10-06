# DroidLogsPilot

<p align="center">
  <img src="https://img.shields.io/badge/Android-ADB%20Log%20Capture-34A853?style=for-the-badge&logo=android" alt="Android ADB" />
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python" alt="Python" />
  <img src="https://img.shields.io/badge/Export-PDF%20Reports-FFB000?style=for-the-badge" alt="PDF export" />
</p>

<p align="center">
  <strong>DroidLogsPilot</strong> is a desktop diagnostics dashboard for Android app testing.
  It records live logcat output, detects API and HTTP activity, highlights failures, captures screenshots, and exports a polished PDF report for QA and debugging.
</p>

<p align="center">
  <em>Original concept and groundwork credited to <a href="https://github.com/shuvo27/DroidLogsPilot">Shuvo</a>.</em>
</p>

---

## ✨ What the app does

- Records live Android logs from a connected device or emulator
- Filters and color-codes log entries by severity
- Detects HTTP/API events and timestamps from logcat output
- Surfaces errors, timeouts, TLS issues, DNS problems, and request failures
- Captures screenshots automatically for error patterns
- Lists declared endpoints found in the APK bundle
- Exports a structured PDF report with session notes, evidence, and log appendix

---

## 🧠 Why this tool is useful

DroidLogsPilot helps Android engineers and QA teams quickly answer:

- What failed during a test run?
- Which API calls were triggered?
- Did the app hit 4xx/5xx responses or timeouts?
- What did the app log right before a crash or network issue?
- How do I package that evidence into a shareable report?

---

## 🖥️ UI preview

The interface is built with Tkinter and designed for fast debugging sessions:

- Device and package selection
- Live log viewer with severity color-coding
- API timeline panel for request and response events
- Error-focused screenshot capture
- PDF export with summary, API details, screenshots, and log appendix

---

## ✅ Requirements

Before running the app, make sure the following are installed:

1. Python 3.10+
2. Android SDK Platform Tools with ADB available on your PATH
3. An Android device connected in USB debugging mode or an emulator running

### Install dependencies

```bash
pip install -r requirements.txt
```

> Note: PDF export support uses `reportlab`, which is included in the project requirements file.

---

## 🚀 How to run

From the project folder:

```bash
python DroidLogsPilot_UI_V2.py
```

If the app does not detect your device:

```bash
adb devices
```

Make sure USB debugging is enabled and the device is authorized.

---

## 🔧 Typical workflow

1. Connect your Android device or start an emulator
2. Open the app and click Refresh
3. Select a target device and app package
4. Toggle capture options if needed
5. Press Start recording
6. Reproduce the issue or test flow
7. Stop the recording and export the PDF report

---

## 📦 Project files

- `DroidLogsPilot_UI_V2.py` — main application
- `requirements.txt` — required Python dependencies

---

## 🛡️ Notes

- HTTPS traffic is not decrypted by the app; it only captures what is emitted to Android logcat.
- Sensitive values such as authorization tokens, passwords, and API keys are redacted before display and export.
- The app stores temporary evidence files during a session and cleans them up when closed.

---

## 💡 Best use cases

- Android app QA
- API regression investigation
- Crash triage and reproduction
- Network failure analysis
- Test-case evidence collection

---

<p align="center">
  <em>Built for faster Android debugging and cleaner bug reporting.</em>
  <br>
  <strong>Credit: <a href="https://github.com/shuvo27/DroidLogsPilot">Shuvo</a></strong>
</p>
