from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QTabWidget, QStatusBar, QLabel
from PyQt6.QtGui import QFont
from gui.transfer_tab import TransferTab
from gui.merge_tab    import MergeTab
from gui.verify_tab   import VerifyTab

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ST SyncTool — Signal Theory")
        self.setMinimumSize(1100, 780)
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(12,12,12,8)
        header_label = QLabel("ST SyncTool")
        header_label.setFont(QFont("SF Pro Display",18,QFont.Weight.Bold))
        header_label.setStyleSheet("color:white")
        root.addWidget(header_label)
        self.tabs = QTabWidget()
        self.tabs.addTab(TransferTab(self), "📦  Transfer")
        self.tabs.addTab(MergeTab(self),    "🔀  Merge")
        self.tabs.addTab(VerifyTab(self),   "🔎  Verify")
        root.addWidget(self.tabs)
        self.setStatusBar(QStatusBar())
