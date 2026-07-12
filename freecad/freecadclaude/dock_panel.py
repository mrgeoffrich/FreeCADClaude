# SPDX-License-Identifier: LGPL-2.1-or-later
"""The singleton dock shell both panels are: Claude chat, and Plan & Tasks.

Each panel is one QDockWidget wrapping one widget: created lazily on first use,
found again by objectName if it survived a workbench reload (rather than
stacking up a second dock), and reached through the module-level ``get_panel()``
of its own module. Only the inner widget, the title, and what happens to a
freshly-created dock (chat raises itself; the plan dock tabs in behind chat)
actually differ -- so the shell lives here once.
"""

import FreeCADGui

from PySide import QtCore, QtWidgets


class DockPanel:
    """Owns one QDockWidget. Subclasses set OBJECT_NAME/TITLE and build the widget."""

    #: objectName -- used both to register the dock and to find it again later.
    OBJECT_NAME = ""
    TITLE = ""
    AREA = QtCore.Qt.RightDockWidgetArea

    _instance = None

    @classmethod
    def instance(cls):
        """This panel's singleton, created on first use.

        Assigns onto the subclass, so each panel class keeps its own instance
        (an unset subclass reads the base's None and mints its own).
        """
        if cls.__dict__.get("_instance") is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._dock = None
        self._build_dock()

    # -- subclass hooks --------------------------------------------------

    def _make_widget(self, dock):
        """The widget this dock wraps."""
        raise NotImplementedError

    def _on_created(self, dock, main_window):
        """Called once, only for a dock we just created (not a reused one)."""

    # -- the dock --------------------------------------------------------

    def _build_dock(self):
        main_window = FreeCADGui.getMainWindow()

        # Reuse an existing dock if one survived a workbench reload.
        existing = main_window.findChild(QtWidgets.QDockWidget, self.OBJECT_NAME)
        if existing is not None:
            self._dock = existing
            return

        dock = QtWidgets.QDockWidget(main_window)
        dock.setObjectName(self.OBJECT_NAME)
        dock.setWindowTitle(self.TITLE)
        dock.setWidget(self._make_widget(dock))
        main_window.addDockWidget(self.AREA, dock)
        self._on_created(dock, main_window)
        self._dock = dock

    def show_dock(self):
        """Show the dock without raising it -- a panel that wants the front tab
        (chat) raises itself on top of this."""
        if self._dock is None:
            self._build_dock()
        self._dock.show()

    def toggle_dock(self):
        if self._dock is None:
            self._build_dock()
        self._dock.setVisible(not self._dock.isVisible())

    @property
    def widget(self):
        return self._dock.widget()
