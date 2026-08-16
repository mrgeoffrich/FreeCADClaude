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
    library      the reusable run_python modules, and their sys.path roots
    doc_notes    the document's standing context notes, and their staleness
    print_meta   per-part build direction: storage, plate side, the legend
    print_export parts meshed and stood up the way they print, via a scratch doc
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
    REFS_REL,
    _safe_name,
    _save_steps,
    active_session_id,
    artifacts_dir,
    bridge_file,
    ensure_sketches_dir,
    new_session_id,
    prepare_session_workspace,
    remove_bridge_file,
    session_dir,
    write_bridge_file,
)
from .tools_annotate import (
    _ANNOTATE_VIEW_SCHEMA,
    _READ_ANNOTATION_SCHEMA,
    _run_annotate_view,
    _run_read_annotation,
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
from .tools_device import (  # device_upload_dir/uploaded_caption re-exported for chat_panel
    _READ_DEVICE_IMAGE_SCHEMA,
    _SEND_TO_DEVICE_SCHEMA,
    _run_read_device_image,
    _run_send_to_device,
    device_upload_dir,  # noqa: F401
    uploaded_caption,  # noqa: F401
)
from .tools_document import (  # _REPORTED_PROPS re-exported for eval_runner
    _DESCRIBE_OBJECTS_SCHEMA,
    _GET_OBJECTS_SCHEMA,
    _GET_SELECTION_SCHEMA,
    _REPORTED_PROPS,  # noqa: F401
    _precheck_describe_objects,
    _run_describe_objects,
    _run_get_objects,
    _run_get_selection,
)
from .tools_export import _EXPORT_SCHEMA, _run_export
from .tools_inspect import _INSPECT_API_SCHEMA, _run_inspect_api
from .tools_library import (
    _SCRIPT_LIBRARY_SCHEMA,
    _precheck_script_library,
    _run_script_library,
)
from .tools_notes import (
    _DOCUMENT_NOTES_SCHEMA,
    _SET_PRINT_DIRECTION_SCHEMA,
    _precheck_document_notes,
    _precheck_set_print_direction,
    _run_document_notes,
    _run_set_print_direction,
)
from .tools_python import _RUN_PYTHON_SCHEMA, _precheck_python, _run_python
from .tools_slice import (  # two names re-exported for chat_panel
    _READ_SLICE_RESULT_SCHEMA,
    _SLICE_MODEL_SCHEMA,
    _VIEW_GCODE_SCHEMA,
    _precheck_slice_model,
    _run_read_slice_result,
    _run_slice_model,
    _run_view_gcode,
    open_settings_page,  # noqa: F401 - the chat panel's Slicer button
)
from .tools_slice import reset_session as reset_slice_session  # noqa: F401
from .tools_sketch import (
    _GET_SKETCH_SCHEMA,
    _VIEW_SKETCH_SVG_SCHEMA,
    _run_get_sketch,
    _run_view_sketch_svg,
)
from .tools_model import (  # model_upload_dir re-exported for chat_panel
    _READ_MODEL_MARKUP_SCHEMA,
    _VIEW_MODEL_3D_SCHEMA,
    _run_read_model_markup,
    _run_view_model_3d,
    model_upload_dir,  # noqa: F401
)

#: Registry: tool name -> {schema, run, precheck?}.
#: ``precheck`` is a pure-Python validation of the args the bridge runs before
#: the tool itself; a non-empty return is relayed to Claude instead of running.
TOOLS = {
    "get_objects": {"schema": _GET_OBJECTS_SCHEMA, "run": _run_get_objects},
    "describe_objects": {
        "schema": _DESCRIBE_OBJECTS_SCHEMA,
        "run": _run_describe_objects,
        "precheck": _precheck_describe_objects,
    },
    "get_selection": {"schema": _GET_SELECTION_SCHEMA, "run": _run_get_selection},
    "document_notes": {
        "schema": _DOCUMENT_NOTES_SCHEMA,
        "run": _run_document_notes,
        "precheck": _precheck_document_notes,
    },
    "set_print_direction": {
        "schema": _SET_PRINT_DIRECTION_SCHEMA,
        "run": _run_set_print_direction,
        "precheck": _precheck_set_print_direction,
    },
    "get_sketch": {"schema": _GET_SKETCH_SCHEMA, "run": _run_get_sketch},
    "view_sketch_svg": {"schema": _VIEW_SKETCH_SVG_SCHEMA, "run": _run_view_sketch_svg},
    "capture_view": {"schema": _CAPTURE_VIEW_SCHEMA, "run": _run_capture_view},
    "capture_user_view": {"schema": _CAPTURE_USER_VIEW_SCHEMA, "run": _run_capture_user_view},
    "annotate_view": {"schema": _ANNOTATE_VIEW_SCHEMA, "run": _run_annotate_view},
    "read_annotation": {"schema": _READ_ANNOTATION_SCHEMA, "run": _run_read_annotation},
    "send_to_device": {"schema": _SEND_TO_DEVICE_SCHEMA, "run": _run_send_to_device},
    "read_device_image": {
        "schema": _READ_DEVICE_IMAGE_SCHEMA,
        "run": _run_read_device_image,
    },
    "crop_view": {"schema": _CROP_VIEW_SCHEMA, "run": _run_crop_view},
    "cutaway": {"schema": _CUTAWAY_SCHEMA, "run": _run_cutaway},
    "export": {"schema": _EXPORT_SCHEMA, "run": _run_export},
    "slice_model": {
        "schema": _SLICE_MODEL_SCHEMA,
        "run": _run_slice_model,
        "precheck": _precheck_slice_model,
    },
    "read_slice_result": {
        "schema": _READ_SLICE_RESULT_SCHEMA,
        "run": _run_read_slice_result,
    },
    "view_gcode": {"schema": _VIEW_GCODE_SCHEMA, "run": _run_view_gcode},
    "inspect_api": {"schema": _INSPECT_API_SCHEMA, "run": _run_inspect_api},
    "run_python": {
        "schema": _RUN_PYTHON_SCHEMA,
        "run": _run_python,
        "precheck": _precheck_python,
    },
    "script_library": {
        "schema": _SCRIPT_LIBRARY_SCHEMA,
        "run": _run_script_library,
        "precheck": _precheck_script_library,
    },
    "get_diagnostics": {"schema": _GET_DIAGNOSTICS_SCHEMA, "run": _run_get_diagnostics},
    "view_model_3d": {"schema": _VIEW_MODEL_3D_SCHEMA, "run": _run_view_model_3d},
    "read_model_markup": {
        "schema": _READ_MODEL_MARKUP_SCHEMA,
        "run": _run_read_model_markup,
    },
}


def list_schemas():
    """Return the MCP tool schemas for tools/list."""
    return [entry["schema"] for entry in TOOLS.values()]
