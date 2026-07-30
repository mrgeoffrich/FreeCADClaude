# SPDX-License-Identifier: LGPL-2.1-or-later
"""A wrapping row layout, so a strip of buttons can't floor a dock's width.

A ``QHBoxLayout``'s minimum width is the *sum* of its children's minimums, and
a ``QPushButton``'s own minimum is ~80px whatever its label says. The chat
panel's control strip (model combo + Files/New/Stop/Send) therefore floored the
whole dock at ~430px even though its transcript and input box need less than
100 -- the dock simply refused to be dragged narrower.

This layout reflows its items onto as many rows as the given width needs, so
its minimum width is that of the *widest single item* rather than their sum,
while its ``sizeHint`` stays the one-row width (a dock still opens wide enough
to show the strip on a single line). Rows are laid out flush right -- the
control strip it was written for sits at the right-hand end of the panel -- and
stack top to bottom.
"""

from PySide import QtCore, QtWidgets


class FlowLayout(QtWidgets.QLayout):
    """Lays items left-to-right, wrapping to a new row when width runs out."""

    def __init__(self, parent=None, spacing=4):
        super().__init__(parent)
        self._items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    # -- the QLayout virtuals Qt requires a subclass to supply ------------

    def addItem(self, item):  # noqa: N802 (Qt API)
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):  # noqa: N802 (Qt API)
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):  # noqa: N802 (Qt API)
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):  # noqa: N802 (Qt API)
        return QtCore.Qt.Orientation(0)  # never asks for extra space

    # -- sizing ----------------------------------------------------------

    def hasHeightForWidth(self):  # noqa: N802 (Qt API)
        """Our height depends on our width -- that's the whole point."""
        return True

    def heightForWidth(self, width):  # noqa: N802 (Qt API)
        margins = self.contentsMargins()
        _rows, height = self._pack(width - margins.left() - margins.right())
        return height + margins.top() + margins.bottom()

    def minimumSize(self):  # noqa: N802 (Qt API)
        """The widest single item: one item per row is always a valid layout."""
        size = QtCore.QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return self._add_margins(size)

    def sizeHint(self):  # noqa: N802 (Qt API)
        """Everything on one row -- the preferred (unwrapped) width."""
        width = 0
        height = 0
        for i, item in enumerate(self._items):
            hint = item.sizeHint()
            width += hint.width() + (self.spacing() if i else 0)
            height = max(height, hint.height())
        return self._add_margins(QtCore.QSize(width, height))

    def _add_margins(self, size):
        margins = self.contentsMargins()
        return size + QtCore.QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )

    # -- geometry --------------------------------------------------------

    def setGeometry(self, rect):  # noqa: N802 (Qt API)
        super().setGeometry(rect)
        margins = self.contentsMargins()
        inner = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        y = inner.y()
        for items, row_width, row_height in self._pack(inner.width())[0]:
            x = inner.right() + 1 - row_width  # flush right
            for item in items:
                hint = item.sizeHint()
                item.setGeometry(QtCore.QRect(x, y, hint.width(), row_height))
                x += hint.width() + self.spacing()
            y += row_height + self.spacing()

    def _pack(self, available):
        """Pack the items into rows of at most `available` content width.

        Returns ``(rows, content_height)`` where each row is
        ``(items, row_width, row_height)``, both excluding this layout's
        margins. Pure arithmetic, shared by heightForWidth (measuring) and
        setGeometry (placing) so the two can't disagree about where the wraps
        fall. An item wider than `available` gets a row of its own -- which is
        why minimumSize() reports the widest item.
        """
        available = max(0, available)
        spacing = self.spacing()
        rows = []
        items, row_width, row_height = [], 0, 0
        for item in self._items:
            hint = item.sizeHint()
            wanted = row_width + (spacing if items else 0) + hint.width()
            if items and wanted > available:
                rows.append((items, row_width, row_height))
                items, row_width, row_height = [], 0, 0
                wanted = hint.width()
            items.append(item)
            row_width = wanted
            row_height = max(row_height, hint.height())
        if items:
            rows.append((items, row_width, row_height))

        height = sum(row[2] for row in rows) + spacing * max(0, len(rows) - 1)
        return rows, height
