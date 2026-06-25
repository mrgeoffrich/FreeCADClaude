# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI commands for the ClaudeChat workbench."""

import os

import FreeCAD
import FreeCADGui

from PySide.QtCore import QT_TRANSLATE_NOOP

_ICON = os.path.join(os.path.dirname(__file__), "resources", "icon.svg")


class TogglePanelCommand:
    """Show/hide the Claude chat dock panel."""

    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": QT_TRANSLATE_NOOP("ClaudeChat_TogglePanel", "Claude Chat panel"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "ClaudeChat_TogglePanel", "Show or hide the Claude chat panel"
            ),
        }

    def Activated(self):
        from freecad.claudechat import chat_panel

        chat_panel.get_panel().toggle_dock()

    def IsActive(self):
        # Always available as long as the GUI is up.
        return FreeCADGui.getMainWindow() is not None


FreeCADGui.addCommand("ClaudeChat_TogglePanel", TogglePanelCommand())
