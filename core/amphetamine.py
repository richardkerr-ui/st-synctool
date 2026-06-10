import subprocess, sys

AMPHETAMINE_APP_STORE = "https://apps.apple.com/us/app/amphetamine/id937984704"

def is_macos(): return sys.platform == "darwin"

def is_installed():
    if not is_macos(): return False
    r = subprocess.run(
        ["osascript","-e",'tell application "Finder" to exists application file id "com.if.Amphetamine"'],
        capture_output=True, text=True)
    return "true" in r.stdout.lower()

def start_session():
    if not is_macos() or not is_installed(): return False
    script = 'tell application "Amphetamine" to start new session with options {displaySleepAllowed:false}'
    r = subprocess.run(["osascript","-e",script], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"Amphetamine start_session error: {r.stderr}")
    return r.returncode == 0

def end_session():
    if not is_macos() or not is_installed(): return False
    r = subprocess.run(
        ["osascript","-e",'tell application "Amphetamine" to end session'],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(f"Amphetamine end_session error: {r.stderr}")
    return r.returncode == 0

def check_and_prompt(parent_widget=None):
    if not is_macos(): return True
    if is_installed(): return True
    if parent_widget:
        from PyQt6.QtWidgets import QMessageBox
        from PyQt6.QtCore import Qt
        msg = QMessageBox(parent_widget)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Amphetamine Required")
        msg.setText(
            "<b>Amphetamine is not installed.</b><br><br>"
            "Amphetamine prevents your Mac from sleeping during transfers.<br><br>"
            f'<a href="{AMPHETAMINE_APP_STORE}">Download from Mac App Store</a>'
        )
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.exec()
    return False
