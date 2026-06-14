"""The Dr. Zhivago exam dialog — gate for disabling bootup music.

Renders core.zhivago_quiz as a five-question multiple-choice exam. The dialog
accepts only when the user passes (>= PASS_THRESHOLD correct); a failing attempt
shows the score and keeps the user on the exam. All scoring lives in core.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QButtonGroup, QRadioButton,
    QGroupBox, QStackedWidget, QWidget, QPushButton, QDialogButtonBox,
    QMessageBox,
)

from core import zhivago_quiz as quiz
from gui import theme


def run_disable_exam(parent=None) -> bool:
    """The full disable-music flow: cheeky intro, then the exam.

    Returns True only if the user opted in and passed. Declining the intro or
    failing the exam returns False (so the caller keeps the music on).
    """
    if not ZhivagoIntroDialog(parent).exec() == QDialog.DialogCode.Accepted:
        return False
    return ZhivagoQuizDialog(parent).exec() == QDialog.DialogCode.Accepted


class ZhivagoIntroDialog(QDialog):
    """The setup. Accepted means 'take the exam'; rejected means 'leave it on'."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("So you want silence")
        self.setMinimumWidth(460)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(16)

        body = QLabel(
            'That\'s "Sim Nights" by Kirk Casey. Calming bossa nova to file '
            "things to. The developer likes it, so it plays on launch.\n\n"
            "You can turn it off. First you have to watch one of his favourite "
            "films. To prove you did, answer 4 of 5 questions on Doctor Zhivago "
            "(1965).")
        body.setWordWrap(True)
        body.setStyleSheet(f"color:{theme.CREAM};font-size:13px;line-height:150%;")
        root.addWidget(body)

        buttons = QDialogButtonBox()
        take = buttons.addButton("Take the exam", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Actually, leave it on", QDialogButtonBox.ButtonRole.RejectRole)
        take.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


class ZhivagoQuizDialog(QDialog):
    """Returns QDialog.Accepted only if the user passes the exam."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Doctor Zhivago: Final Exam")
        self.setMinimumWidth(560)
        self._groups: list = []
        self._build_ui()

    # Bigger, higher-contrast radio rows: a roomy indicator that fills gold when
    # checked, generous padding and a hover highlight so each option is an easy
    # target and easy to read against the dark theme.
    _QUIZ_QSS = f"""
        QGroupBox {{
            color: {theme.CREAM};
            font-size: 13px;
            font-weight: bold;
            border: 1px solid {theme.BORDER};
            border-radius: 6px;
            margin-top: 10px;
            padding: 8px 10px 10px 10px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
        }}
        QRadioButton {{
            color: {theme.CREAM};
            font-size: 13px;
            font-weight: normal;
            padding: 7px 6px;
            spacing: 10px;
            border-radius: 4px;
        }}
        QRadioButton:hover {{
            background: {theme.CHARCOAL_HOVER};
        }}
        QRadioButton::indicator {{
            width: 18px;
            height: 18px;
            border: 2px solid {theme.BORDER};
            border-radius: 11px;
            background: {theme.CHARCOAL};
        }}
        QRadioButton::indicator:hover {{
            border-color: {theme.GOLD};
        }}
        QRadioButton::indicator:checked {{
            border: 2px solid {theme.GOLD};
            background: {theme.GOLD};
        }}
    """

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)
        self.setStyleSheet(self._QUIZ_QSS)

        # Paginated: one question per page so the dialog stays short on any
        # monitor. The QGroupBoxes live in a stack; selections persist because
        # the widgets stay alive as we page back and forth.
        self._progress = QLabel()
        self._progress.setStyleSheet(f"color:{theme.TEXT_MUTED};font-size:12px;")
        root.addWidget(self._progress)

        self._stack = QStackedWidget()
        for i, q in enumerate(quiz.QUESTIONS):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            box = QGroupBox(f"{i + 1}.  {q.prompt}")
            box_layout = QVBoxLayout(box)
            box_layout.setSpacing(2)
            group = QButtonGroup(self)
            group.setExclusive(True)
            for j, option in enumerate(q.options):
                rb = QRadioButton(option)
                group.addButton(rb, j)
                box_layout.addWidget(rb)
            self._groups.append(group)
            page_layout.addWidget(box)
            page_layout.addStretch()
            self._stack.addWidget(page)
        root.addWidget(self._stack)

        nav = QHBoxLayout()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        self._back_btn = QPushButton("Back")
        self._back_btn.clicked.connect(self._go_back)
        self._next_btn = QPushButton("Next")
        self._next_btn.setDefault(True)
        self._next_btn.clicked.connect(self._go_next)
        nav.addWidget(self._cancel_btn)
        nav.addStretch()
        nav.addWidget(self._back_btn)
        nav.addWidget(self._next_btn)
        root.addLayout(nav)

        self._stack.setCurrentIndex(0)
        self._refresh_nav()

    def _refresh_nav(self):
        idx = self._stack.currentIndex()
        last = self._stack.count() - 1
        self._progress.setText(f"Question {idx + 1} of {self._stack.count()}")
        self._back_btn.setEnabled(idx > 0)
        self._next_btn.setText("Submit exam" if idx == last else "Next")

    def _go_back(self):
        idx = self._stack.currentIndex()
        if idx > 0:
            self._stack.setCurrentIndex(idx - 1)
            self._refresh_nav()

    def _go_next(self):
        idx = self._stack.currentIndex()
        if idx < self._stack.count() - 1:
            self._stack.setCurrentIndex(idx + 1)
            self._refresh_nav()
        else:
            self._submit()

    def _selected(self) -> list:
        """One chosen option-index per question; None where unanswered."""
        out = []
        for group in self._groups:
            cid = group.checkedId()
            out.append(cid if cid != -1 else None)
        return out

    def _submit(self):
        selected = self._selected()
        score = quiz.grade(selected)
        total = len(quiz.QUESTIONS)
        if score >= quiz.PASS_THRESHOLD:
            QMessageBox.information(
                self, "Passed",
                f"{score} of {total} correct. You may disable the music.")
            self.accept()
        else:
            QMessageBox.warning(
                self, "Failed",
                f"{score} of {total} correct. You need {quiz.PASS_THRESHOLD} to pass. "
                f"The music plays on. Try again.")
            self._stack.setCurrentIndex(0)
            self._refresh_nav()
