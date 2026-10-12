"""Application entry point."""

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from ui import SerialPacketWindow


def apply_theme(app: QApplication, scheme: Qt.ColorScheme | None = None) -> None:
    if scheme is None:
        scheme = app.styleHints().colorScheme()
    is_dark = scheme == Qt.ColorScheme.Dark
    if scheme == Qt.ColorScheme.Unknown:
        is_dark = app.palette().color(QPalette.ColorRole.Window).lightness() < 128

    if is_dark:
        colors = {
            "window": "#191e1c",
            "surface": "#222927",
            "alternate": "#29312e",
            "text": "#e4eae7",
            "muted": "#a5b1ab",
            "border": "#414d47",
            "hover": "#303b36",
            "selected": "#285448",
            "selected_text": "#e8f4ef",
            "header": "#2b3430",
            "accent": "#26836d",
            "accent_hover": "#31977e",
            "status": "#b5c0bb",
        }
    else:
        colors = {
            "window": "#f3f5f4",
            "surface": "#ffffff",
            "alternate": "#f7f9f8",
            "text": "#202824",
            "muted": "#66736f",
            "border": "#d4dcd8",
            "hover": "#edf5f2",
            "selected": "#dcefe9",
            "selected_text": "#123f36",
            "header": "#edf1ef",
            "accent": "#176b5b",
            "accent_hover": "#125849",
            "status": "#52615b",
        }

    app.setStyleSheet(f"""
        QWidget {{ color: {colors["text"]}; font-size: 13px; }}
        QMainWindow {{ background: {colors["window"]}; }}
        #toolbar {{ background: {colors["surface"]}; border: 1px solid {colors["border"]}; border-radius: 5px; }}
        #brand {{ color: {colors["accent"]}; font-weight: 700; font-size: 15px; }}
        #muted {{ color: {colors["muted"]}; }}
        QTreeWidget, QTextEdit, QComboBox, QDoubleSpinBox {{
            background: {colors["surface"]}; border: 1px solid {colors["border"]}; border-radius: 3px;
        }}
        QComboBox QAbstractItemView {{
            background: {colors["surface"]}; color: {colors["text"]};
            selection-background-color: {colors["selected"]}; selection-color: {colors["selected_text"]};
        }}
        QTreeWidget {{ alternate-background-color: {colors["alternate"]}; }}
        QTreeWidget::item {{ padding: 5px 3px; }}
        QTreeWidget::item:selected {{ background: {colors["selected"]}; color: {colors["selected_text"]}; }}
        QPushButton {{ padding: 6px 10px; background: {colors["surface"]}; border: 1px solid {colors["border"]}; border-radius: 3px; }}
        QPushButton:hover {{ background: {colors["hover"]}; }}
        QPushButton#connectButton {{ background: {colors["accent"]}; color: #ffffff; border-color: {colors["accent"]}; }}
        QPushButton#connectButton:hover {{ background: {colors["accent_hover"]}; }}
        QToolButton {{ text-align: left; padding: 7px 4px; font-weight: 600; }}
        QHeaderView::section {{ background: {colors["header"]}; padding: 6px; border: 0; }}
        QStatusBar {{ color: {colors["status"]}; }}
        QToolTip {{ color: {colors["text"]}; background: {colors["surface"]}; border: 1px solid {colors["border"]}; }}
    """)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    style_hints = app.styleHints()
    apply_theme(app, style_hints.colorScheme())
    style_hints.colorSchemeChanged.connect(lambda scheme: apply_theme(app, scheme))
    window = SerialPacketWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
