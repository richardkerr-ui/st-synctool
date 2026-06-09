import sys, os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor
from core.preflight import run_preflight
from gui.main_window import MainWindow
from gui import theme


def _qcolor(hex_str):
    h = hex_str.lstrip("#")
    return QColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def main():
    run_preflight()

    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    app = QApplication(sys.argv)
    app.setApplicationName("ST SyncTool")
    app.setOrganizationName("Signal Theory")
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          _qcolor(theme.CHARCOAL))
    palette.setColor(QPalette.ColorRole.WindowText,      _qcolor(theme.CREAM))
    palette.setColor(QPalette.ColorRole.Base,            _qcolor(theme.CHARCOAL))
    palette.setColor(QPalette.ColorRole.AlternateBase,   _qcolor(theme.CHARCOAL_LIGHT))
    palette.setColor(QPalette.ColorRole.Text,            _qcolor(theme.CREAM))
    palette.setColor(QPalette.ColorRole.Button,          _qcolor(theme.CHARCOAL_LIGHT))
    palette.setColor(QPalette.ColorRole.ButtonText,      _qcolor(theme.CREAM))
    palette.setColor(QPalette.ColorRole.Highlight,       _qcolor(theme.GOLD))
    palette.setColor(QPalette.ColorRole.HighlightedText, _qcolor(theme.CHARCOAL))
    app.setPalette(palette)

    app.setStyleSheet(theme.app_stylesheet())

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
