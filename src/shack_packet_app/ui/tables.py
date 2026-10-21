"""Plain read-only tables, sized to their contents with the last column
taking the rest (no sideways scrolling for a list of calls)."""
from __future__ import annotations

from PyQt6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem


def make(headers) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.setWordWrap(False)
    h = t.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    h.setStretchLastSection(True)
    return t


def fill(table: QTableWidget, rows) -> None:
    table.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            item = v if isinstance(v, QTableWidgetItem) else QTableWidgetItem(v if isinstance(v, str) else str(v))
            table.setItem(i, j, item)
    fit(table)


def fit(table: QTableWidget) -> None:
    for j in range(table.columnCount() - 1):
        table.resizeColumnToContents(j)
