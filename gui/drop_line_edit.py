"""A QLineEdit that accepts a dropped folder (or file → its parent folder).

The drop-resolution logic lives in core.dnd; this is the thin Qt layer that
wires Finder drops to it and gives a subtle highlight while a valid payload
hovers. Used by the Offload source/destination rows and any other bare path
field. PathInputWidget reuses core.dnd directly on its own line edit.
"""

from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent

from core.dnd import folder_from_dropped_paths
from gui import theme


def _local_paths(mime) -> list:
    return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]


class DropLineEdit(QLineEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)
        self._base_style = ""

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls() and _local_paths(e.mimeData()):
            self._base_style = self.styleSheet()
            self.setStyleSheet(
                self._base_style + f"QLineEdit {{ border:1px solid {theme.GOLD}; }}")
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragLeaveEvent(self, e: QDragLeaveEvent):
        self.setStyleSheet(self._base_style)
        super().dragLeaveEvent(e)

    def dropEvent(self, e: QDropEvent):
        self.setStyleSheet(self._base_style)
        folder = folder_from_dropped_paths(_local_paths(e.mimeData()))
        if folder:
            self.setText(folder)
            e.acceptProposedAction()
        else:
            super().dropEvent(e)
