"""Signal Theory brand theme. Single source of truth for colors."""

GOLD       = "#F6BE00"
CHARCOAL   = "#25282A"
BLUE_GRAY  = "#8BA6C1"
CREAM      = "#FAF7F0"
GREEN      = "#046A38"
CORAL      = "#EE5340"

CHARCOAL_LIGHT  = "#2F3336"
CHARCOAL_HOVER  = "#3A3E42"
BORDER          = "#3F4347"
MUTED_TEXT      = "#888B8E"

BG_PRIMARY    = CHARCOAL
TEXT_PRIMARY  = CREAM
TEXT_MUTED    = MUTED_TEXT
ACCENT_GOLD   = GOLD
ACCENT_GREEN  = GREEN
ACCENT_CORAL  = CORAL
ACCENT_INFO   = BLUE_GRAY

STATE_COLORS = {
    "LOCAL_ONLY":     GOLD,
    "LOCAL_CHANGED":  GOLD,
    "SERVER_ONLY":    BLUE_GRAY,
    "SERVER_CHANGED": BLUE_GRAY,
    "BOTH_CHANGED":   CORAL,
    "DELETED_LOCAL":  MUTED_TEXT,
    "DELETED_SERVER": MUTED_TEXT,
    "DELETED_BOTH":   "#555555",
    "UNCHANGED":      MUTED_TEXT,
    "RENAMED":        "#c586c0",
}

# --------------------------------------------------------------------------- #
# Verdict / state styling (GUI refresh 2026-06-13)
# --------------------------------------------------------------------------- #
# Brightened brand colours used for verdicts, states and result tiles. Severity
# buckets come from core.verdict_style (pure logic); this maps them to colour.

VERDICT_GREEN   = "#3DBB8E"   # ok      — verified / passed / cleared / OK tile
VERDICT_GOLD    = GOLD        # warn    — needs attention / extra-files tile
VERDICT_CORAL   = CORAL       # error   — failed / missing tile
VERDICT_MUTED   = "#AEB1B3"   # neutral — completed, nothing to flag
VERDICT_MAGENTA = "#C0468C"   # mismatch tile (distinct from missing)

SEVERITY_COLORS = {
    "ok":      VERDICT_GREEN,
    "warn":    VERDICT_GOLD,
    "error":   VERDICT_CORAL,
    "neutral": VERDICT_MUTED,
}


def severity_color(severity: str) -> str:
    return SEVERITY_COLORS.get(severity, VERDICT_MUTED)


# Table colours (from the redesign mockup): header #222628 on muted text,
# rows on charcoal with a subtly lighter zebra stripe and a hover highlight.
TABLE_BG        = "#1E2123"
TABLE_ALT_BG    = "#24282A"
TABLE_HOVER_BG  = "#2C3033"
TABLE_HEADER_BG = "#222628"
TABLE_GRID      = "#2A2E30"
TABLE_TEXT      = "#D4D7D9"
TABLE_SELECTED  = "#33414C"


def table_stylesheet() -> str:
    """Shared QTableWidget look: zebra striping + row hover, matched to the
    redesign mockup. Selection stays a muted slate (never the bright accent)."""
    return f"""
    QTableWidget {{
        background:{TABLE_BG}; color:{TABLE_TEXT};
        gridline-color:{TABLE_GRID};
        border:1px solid {BORDER}; border-radius:6px;
    }}
    QHeaderView::section {{
        background:{TABLE_HEADER_BG}; color:{MUTED_TEXT};
        padding:8px 12px; border:none; font-weight:700; font-size:11px;
    }}
    QTableWidget::item {{ padding:7px 6px; }}
    QTableWidget::item:alternate {{ background:{TABLE_ALT_BG}; }}
    QTableWidget::item:hover {{ background:{TABLE_HOVER_BG}; }}
    QTableWidget::item:selected {{ background:{TABLE_SELECTED}; color:{CREAM}; }}
    """


def verdict_color(verdict: str) -> str:
    """Brand colour for a verdict token (e.g. "VERIFIED" → green)."""
    from core.verdict_style import verdict_severity
    return severity_color(verdict_severity(verdict))


def app_stylesheet():
    """Generate the Qt stylesheet for the application."""
    return f"""
    QMainWindow {{
        background-color: {BG_PRIMARY};
        color: {TEXT_PRIMARY};
    }}
    QWidget {{
        background-color: {BG_PRIMARY};
        color: {TEXT_PRIMARY};
    }}
    QPushButton {{
        background-color: {CHARCOAL_LIGHT};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 4px;
        padding: 6px 12px;
    }}
    QPushButton:hover {{
        background-color: {CHARCOAL_HOVER};
    }}
    QLineEdit, QTextEdit {{
        background-color: {CHARCOAL_LIGHT};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 4px;
        padding: 4px;
    }}
    QLabel {{
        color: {TEXT_PRIMARY};
    }}
    """

def primary_button_style():
    """Style for primary action buttons."""
    return f"""
    QPushButton {{
        background-color: {ACCENT_GOLD};
        color: {CHARCOAL};
        border: none;
        border-radius: 4px;
        padding: 8px 16px;
        font-weight: bold;
    }}
    QPushButton:hover {{
        background-color: #E6AE00;
    }}
    QPushButton:pressed {{
        background-color: #D4A000;
    }}
    """

def success_button_style():
    """Style for success/confirm buttons."""
    return f"""
    QPushButton {{
        background-color: {ACCENT_GREEN};
        color: {CREAM};
        border: none;
        border-radius: 4px;
        padding: 8px 16px;
        font-weight: bold;
    }}
    QPushButton:hover {{
        background-color: #055A2F;
    }}
    QPushButton:pressed {{
        background-color: #044620;
    }}
    """


# --------------------------------------------------------------------------- #
# Per-tab accent system (GUI refresh 2026-06-13)
# --------------------------------------------------------------------------- #
# Each tab drives an accent colour (brightened for legibility on charcoal) that
# tints its section headers, the active-tab underline, the primary button and
# checked checkboxes. The look is one consistent layout recoloured per tab.

TAB_ACCENTS = {
    "Transfer": "#5E9BD6",   # blue   — movement
    "Merge":    "#B57EDC",   # purple — reconcile
    "Offload":  "#EE5340",   # coral  — card ingest
    "Verify":   "#3DBB8E",   # green  — integrity
    "History":  "#F6BE00",   # gold   — records
}
DEFAULT_ACCENT = TAB_ACCENTS["Transfer"]
FIELD_BG = "#1E2123"


def tab_accent(name: str) -> str:
    return TAB_ACCENTS.get(name, DEFAULT_ACCENT)


def accent_primary_button_style(accent: str) -> str:
    """Big rounded primary action button filled with the tab accent."""
    return f"""
    QPushButton {{
        background-color: {accent};
        color: {CHARCOAL};
        border: none;
        border-radius: 8px;
        padding: 12px 24px;
        font-weight: 800;
    }}
    QPushButton:hover  {{ background-color: {accent}; }}
    QPushButton:disabled {{ background-color: {BORDER}; color: {MUTED_TEXT}; }}
    """


def section_label_style(accent: str) -> str:
    """Uppercase-style section header in the tab accent (set text upper-case)."""
    return (f"color:{accent}; font-weight:700; font-size:12px; "
            f"background:transparent;")


def tab_stylesheet(accent: str) -> str:
    """Cascading stylesheet applied to a whole tab widget so its group titles,
    checkboxes and primary button pick up the tab accent in one shot."""
    return f"""
    QGroupBox {{
        border: none;
        margin-top: 16px;
        font-weight: 700;
        color: {accent};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 0px;
        padding: 0 0 6px 0;
        color: {accent};
        font-size: 12px;
    }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border-radius: 4px;
        border: 1px solid {BORDER};
        background: {FIELD_BG};
    }}
    QCheckBox::indicator:checked {{
        background: {accent};
        border: 1px solid {accent};
    }}
    QPushButton#primaryBtn {{
        background-color: {accent};
        color: {CHARCOAL};
        border: none; border-radius: 8px;
        padding: 12px 24px; font-weight: 800;
    }}
    QPushButton#primaryBtn:disabled {{
        background-color: {BORDER}; color: {MUTED_TEXT};
    }}
    """


def tabbar_stylesheet(accent: str) -> str:
    """Tab bar with the selected tab underlined in the active tab's accent."""
    return f"""
    QTabBar::tab {{
        background: transparent;
        color: {MUTED_TEXT};
        padding: 8px 18px 12px;
        margin-right: 18px;
        border: none;
        font-weight: 700;
    }}
    QTabBar::tab:selected {{
        color: {CREAM};
        border-bottom: 3px solid {accent};
    }}
    QTabWidget::pane {{ border: none; border-top: 1px solid #303437; }}
    """
