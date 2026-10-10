"""Application entry point."""

import sys

from PySide6.QtWidgets import QApplication

from ui import SerialPacketWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget { font-size: 13px; }
        QMainWindow { background: #f3f5f4; }
        #toolbar { background: #ffffff; border: 1px solid #d9dfdc; border-radius: 5px; }
        #brand { color: #176b5b; font-weight: 700; font-size: 15px; }
        #muted { color: #66736f; }
        QTreeWidget, QTextEdit, QComboBox, QDoubleSpinBox {
            background: #ffffff; border: 1px solid #d4dcd8; border-radius: 3px;
        }
        QTreeWidget { alternate-background-color: #f7f9f8; }
        QTreeWidget::item { padding: 5px 3px; }
        QTreeWidget::item:selected { background: #dcefe9; color: #123f36; }
        QPushButton { padding: 6px 10px; background: #ffffff; border: 1px solid #c9d2ce; border-radius: 3px; }
        QPushButton:hover { background: #edf5f2; }
        QPushButton#connectButton { background: #176b5b; color: white; border-color: #176b5b; }
        QPushButton#connectButton:hover { background: #125849; }
        QToolButton { text-align: left; padding: 7px 4px; font-weight: 600; }
        QHeaderView::section { background: #edf1ef; padding: 6px; border: 0; }
        QStatusBar { color: #52615b; }
    """)
    window = SerialPacketWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
