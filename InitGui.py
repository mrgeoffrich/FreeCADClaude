# SPDX-License-Identifier: LGPL-2.1-or-later
#
# ClaudeChat workbench - GUI init.
#
# Registers a workbench whose only job (for now) is to show a dockable
# chat panel on the right-hand side of the FreeCAD main window. No AI is
# wired up yet: this is the milestone-1 plumbing skeleton.

import FreeCAD
import FreeCADGui


class ClaudeChatWorkbench(FreeCADGui.Workbench):
    """A minimal workbench that exposes the Claude chat dock panel."""

    MenuText = "Claude Chat"
    ToolTip = "Talk to Claude with access to FreeCAD"

    def __init__(self):
        # NOTE: FreeCAD runs this file via exec() without defining __file__,
        # so we must NOT reference __file__ here. Derive the icon path from the
        # importable package instead (it has a real __file__).
        try:
            import os

            from freecad import claudechat

            self.__class__.Icon = os.path.join(
                os.path.dirname(claudechat.__file__),
                "resources",
                "icon.svg",
            )
        except Exception as exc:
            # Never let icon resolution stop the workbench from registering.
            FreeCAD.Console.PrintWarning(
                f"ClaudeChat: could not resolve workbench icon ({exc})\n"
            )

    def Initialize(self):
        """Run once, the first time the workbench is activated."""
        # Imports are done inside the method on purpose. InitGui.py is run via
        # exec(), so module-level names are NOT visible to methods called later
        # (they resolve against FreeCAD's loader globals). Importing here makes
        # these reliable locals. It also keeps GUI imports out of startup.
        from freecad.claudechat import commands  # noqa: F401
        from PySide.QtCore import QT_TRANSLATE_NOOP

        self.appendToolbar(
            QT_TRANSLATE_NOOP("Workbench", "Claude Chat"),
            ["ClaudeChat_TogglePanel"],
        )
        self.appendMenu(
            QT_TRANSLATE_NOOP("Workbench", "Claude"),
            ["ClaudeChat_TogglePanel"],
        )

    def Activated(self):
        """Run every time the user switches to this workbench."""
        from freecad.claudechat import chat_panel, plan_panel

        # Chat first so the Plan dock can tab itself alongside it.
        chat_panel.get_panel()
        plan_panel.get_panel()
        plan_panel.get_panel().show_dock()
        chat_panel.get_panel().show_dock()  # raise chat to the front tab

    def Deactivated(self):
        pass

    def GetClassName(self):
        # Required for Python workbenches.
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(ClaudeChatWorkbench())
