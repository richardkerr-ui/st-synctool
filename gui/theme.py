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
