# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD tools exposed to Claude, plus their execution functions.

Each ``run`` function executes ON THE GUI MAIN THREAD (the bridge marshals it
there) and returns either a human-readable result string, or -- for
``capture_view``, the one tool that renders a raster image -- a
``(text, png_path)`` tuple; the bridge reads and base64-encodes ``png_path``
and ships it back as an inline MCP image content block, so Claude sees the
picture directly in the tool result instead of needing a separate Read call.
(``view_sketch_svg`` writes an SVG file too, but returns only its path as
plain text -- SVG isn't a raster format the Claude API can render as an
image, so Claude reads it as text via the Read tool instead.) FreeCAD imports
happen inside the functions so this package stays importable from any thread
for its schema data alone -- keep it that way: no submodule may import FreeCAD
at module level, since importing this package imports all of them.

The tools themselves live in the ``tools_*`` submodules, one per concern, over
a base of shared infrastructure:

    session      artifact folders, the per-conversation session dir
    geometry     bounding boxes, world-space crop extents
    svg          framing/cropping an SVG projection
    gui_state    what the user has open in an editor
    visibility   show only the captured objects, then restore
    render       the offscreen view, its camera, and the PNG grab
    diagnostics  what a mutating call changed, and what it broke
"""

from .diagnostics import (  # noqa: F401 - re-exported for gui_bridge
    MUTATING_TOOLS,
    feature_snapshot,
    post_tool_notes,
)
from .diagnostics import _GET_DIAGNOSTICS_SCHEMA, _run_get_diagnostics
from .session import (  # noqa: F401 - re-exported for chat_panel/agent_config/eval_runner
    PARAM_PATH,
    _safe_name,
    _save_steps,
    artifacts_dir,
    ensure_sketches_dir,
    new_session_id,
    session_dir,
)
from .tools_capture import (
    _CAPTURE_USER_VIEW_SCHEMA,
    _CAPTURE_VIEW_SCHEMA,
    _CROP_VIEW_SCHEMA,
    _run_capture_user_view,
    _run_capture_view,
    _run_crop_view,
)
from .tools_cutaway import _CUTAWAY_SCHEMA, _run_cutaway
from .tools_document import (  # _REPORTED_PROPS re-exported for eval_runner
    _GET_OBJECTS_SCHEMA,
    _GET_SELECTION_SCHEMA,
    _REPORTED_PROPS,  # noqa: F401
    _run_get_objects,
    _run_get_selection,
)
from .tools_export import _EXPORT_SCHEMA, _run_export
from .tools_inspect import _INSPECT_API_SCHEMA, _run_inspect_api
from .tools_python import _RUN_PYTHON_SCHEMA, _precheck_python, _run_python
from .tools_sketch import (
    _GET_SKETCH_SCHEMA,
    _VIEW_SKETCH_SVG_SCHEMA,
    _run_get_sketch,
    _run_view_sketch_svg,
)

#: Registry: tool name -> {schema, run, confirm?}.
#: ``confirm: True`` means the bridge asks the user to approve before running.
TOOLS = {
    "get_objects": {"schema": _GET_OBJECTS_SCHEMA, "run": _run_get_objects},
    "get_selection": {"schema": _GET_SELECTION_SCHEMA, "run": _run_get_selection},
    "get_sketch": {"schema": _GET_SKETCH_SCHEMA, "run": _run_get_sketch},
    "view_sketch_svg": {"schema": _VIEW_SKETCH_SVG_SCHEMA, "run": _run_view_sketch_svg},
    "capture_view": {"schema": _CAPTURE_VIEW_SCHEMA, "run": _run_capture_view},
    "capture_user_view": {"schema": _CAPTURE_USER_VIEW_SCHEMA, "run": _run_capture_user_view},
    "crop_view": {"schema": _CROP_VIEW_SCHEMA, "run": _run_crop_view},
    "cutaway": {"schema": _CUTAWAY_SCHEMA, "run": _run_cutaway},
    "export": {"schema": _EXPORT_SCHEMA, "run": _run_export},
    "inspect_api": {"schema": _INSPECT_API_SCHEMA, "run": _run_inspect_api},
    "run_python": {
        "schema": _RUN_PYTHON_SCHEMA,
        "run": _run_python,
        "confirm": True,
        "precheck": _precheck_python,
    },
    "get_diagnostics": {"schema": _GET_DIAGNOSTICS_SCHEMA, "run": _run_get_diagnostics},
}


def list_schemas():
    """Return the MCP tool schemas for tools/list."""
    return [entry["schema"] for entry in TOOLS.values()]
