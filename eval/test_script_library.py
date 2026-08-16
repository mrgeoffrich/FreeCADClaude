# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for the script library -- index, validation, save, shadowing.

Runs under a bare python3: library.py parses with ast and never imports a
module, so nothing here needs FreeCAD. That is the point of the design, and
this suite is what holds it in place.

    python3 eval/test_script_library.py
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from freecad.freecadclaude.freecad_tools import library  # noqa: E402
from freecad.freecadclaude.freecad_tools import tools_library  # noqa: E402

GOOD = '''"""Do a useful thing.

The long why, which the index must not carry in full.
"""

import Part


def useful(label, height, axis=None, *rest, flag=True, **kw):
    """Do the useful thing to `label`."""
    return label


def _helper():
    """Private, so it stays out of the index."""
'''


class _TempRoots:
    """Point both library roots at throwaway folders for the duration."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.personal = os.path.join(self._tmp.name, "personal")
        self.bundled = os.path.join(self._tmp.name, "bundled")
        os.makedirs(self.personal)
        os.makedirs(self.bundled)
        self._saved = (library.personal_root, library.BUNDLED_ROOT)
        library.personal_root = lambda: self.personal
        library.BUNDLED_ROOT = self.bundled
        return self

    def __exit__(self, *exc):
        library.personal_root, library.BUNDLED_ROOT = self._saved
        self._tmp.cleanup()
        return False

    def write(self, root, name, text):
        with open(os.path.join(root, name + ".py"), "w", encoding="utf-8") as fh:
            fh.write(text)


class IndexTests(unittest.TestCase):
    def test_index_parses_without_importing(self):
        # the module imports Part, which does not exist under a bare python3 --
        # if the index ever imported instead of parsing, this would fail here
        with _TempRoots() as roots:
            roots.write(roots.bundled, "thing", GOOD)
            entries = library.index()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["module"], "thing")
        self.assertEqual(entry["origin"], "bundled")
        self.assertEqual(entry["summary"], "Do a useful thing.")
        self.assertEqual([f["signature"] for f in entry["functions"]],
                         ["useful(label, height, axis=None, *rest, flag=True, **kw)"])
        self.assertEqual(entry["functions"][0]["summary"],
                         "Do the useful thing to `label`.")

    def test_summary_is_first_paragraph_only(self):
        with _TempRoots() as roots:
            roots.write(roots.bundled, "thing", GOOD)
            summary = library.index()[0]["summary"]
        self.assertNotIn("long why", summary)

    def test_long_summary_is_capped(self):
        long_doc = '"""%s"""\n\n\ndef f():\n    """One."""\n' % ("word " * 200)
        with _TempRoots() as roots:
            roots.write(roots.bundled, "thing", long_doc)
            summary = library.index()[0]["summary"]
        self.assertLessEqual(len(summary), library._MAX_SUMMARY + 4)
        self.assertTrue(summary.endswith("..."))

    def test_unparseable_module_is_skipped_not_fatal(self):
        with _TempRoots() as roots:
            roots.write(roots.bundled, "broken", "def (:\n")
            roots.write(roots.bundled, "thing", GOOD)
            entries = library.index()
        self.assertEqual([e["module"] for e in entries], ["thing"])

    def test_private_and_non_python_files_are_ignored(self):
        with _TempRoots() as roots:
            roots.write(roots.bundled, "_private", GOOD)
            with open(os.path.join(roots.bundled, "notes.txt"), "w") as fh:
                fh.write("hi")
            self.assertEqual(library.index(), [])

    def test_personal_shadows_bundled_and_both_are_reported(self):
        with _TempRoots() as roots:
            roots.write(roots.bundled, "thing", GOOD)
            roots.write(roots.personal, "thing", GOOD)
            entries = library.index()
            text = library.format_index(entries)
        self.assertEqual([e["origin"] for e in entries], ["personal", "bundled"])
        self.assertEqual(entries[0].get("shadows"), "bundled")
        self.assertEqual(entries[1].get("shadowed_by"), "personal")
        self.assertIn("SHADOWED", text)

    def test_format_index_carries_signature_and_path(self):
        with _TempRoots() as roots:
            roots.write(roots.bundled, "thing", GOOD)
            text = library.format_index(library.index())
        self.assertIn("useful(label, height", text)
        self.assertIn("thing.py", text)
        self.assertNotIn("_helper", text)

    def test_empty_library_still_explains_how_to_add(self):
        with _TempRoots():
            text = library.format_index(library.index())
        self.assertIn("empty", text)
        self.assertIn("script_library", text)


class DeclaredOrderTests(unittest.TestCase):
    """__all__ decides the index entry's membership and order."""

    SOURCE = ('"""Doc."""\n\n__all__ = ["b", "a"]\n\n\n'
              'def a():\n    """A."""\n\n\ndef b():\n    """B."""\n\n\n'
              'def c():\n    """C, deliberately not in __all__."""\n')

    def _functions(self, source):
        with _TempRoots() as roots:
            roots.write(roots.bundled, "thing", source)
            return [f["signature"] for f in library.index()[0]["functions"]]

    def test_all_sets_the_order(self):
        self.assertEqual(self._functions(self.SOURCE), ["b()", "a()"])

    def test_public_function_left_out_of_all_is_omitted(self):
        self.assertNotIn("c()", self._functions(self.SOURCE))

    def test_without_all_definition_order_is_kept(self):
        source = '"""Doc."""\n\n\ndef b():\n    """B."""\n\n\ndef a():\n    """A."""\n'
        self.assertEqual(self._functions(source), ["b()", "a()"])

    def test_name_in_all_that_is_not_a_function_is_ignored(self):
        source = ('"""Doc."""\n\n__all__ = ["CONST", "a"]\n\nCONST = 1\n\n\n'
                  'def a():\n    """A."""\n')
        self.assertEqual(self._functions(source), ["a()"])

    def test_all_naming_no_public_function_is_refused_on_save(self):
        source = '"""Doc."""\n\n__all__ = ["CONST"]\n\nCONST = 1\n\n\ndef a():\n    """A."""\n'
        self.assertIn("__all__ names none", library.validate("thing", source))

    def test_non_literal_all_falls_back_to_definition_order(self):
        source = ('"""Doc."""\n\n__all__ = sorted(["a"])\n\n\n'
                  'def b():\n    """B."""\n\n\ndef a():\n    """A."""\n')
        self.assertEqual(self._functions(source), ["b()", "a()"])
        self.assertEqual(library.validate("thing", source), "")


class ValidationTests(unittest.TestCase):
    def test_good_module_passes(self):
        self.assertEqual(library.validate("thing", GOOD), "")

    def test_name_must_be_an_importable_identifier(self):
        for bad in ("thing.py", "two words", "9lives", "_private", "class", ""):
            self.assertNotEqual(library.validate(bad, GOOD), "",
                                "%r should be rejected" % (bad,))

    def test_syntax_error_is_reported_with_its_line(self):
        message = library.validate("thing", '"""Doc."""\n\ndef (:\n')
        self.assertIn("does not parse", message)
        self.assertIn("line", message)

    def test_module_docstring_is_required(self):
        self.assertIn("module docstring",
                      library.validate("thing", "def f():\n    'One.'\n"))

    def test_public_function_is_required(self):
        code = '"""Doc."""\n\nx = 1\n\n\ndef _p():\n    """P."""\n'
        self.assertIn("public top-level function", library.validate("thing", code))

    def test_public_function_docstring_is_required(self):
        code = '"""Doc."""\n\n\ndef f():\n    return 1\n'
        self.assertIn("no docstring", library.validate("thing", code))


class SaveTests(unittest.TestCase):
    def test_save_writes_and_reports_replacement(self):
        with _TempRoots() as roots:
            path, replaced = library.save("thing", GOOD)
            self.assertEqual(path, os.path.join(roots.personal, "thing.py"))
            self.assertFalse(replaced)
            _, replaced = library.save("thing", GOOD)
            self.assertTrue(replaced)
            self.assertEqual([e["module"] for e in library.index()], ["thing"])

    def test_save_appends_a_trailing_newline(self):
        with _TempRoots():
            path, _ = library.save("thing", '"""D."""\ndef f():\n    """O."""')
            with open(path, encoding="utf-8") as fh:
                self.assertTrue(fh.read().endswith("\n"))

    def test_shadowed_note_only_when_a_bundled_copy_exists(self):
        with _TempRoots() as roots:
            self.assertEqual(library.shadowed_note("thing"), "")
            roots.write(roots.bundled, "thing", GOOD)
            self.assertIn("SHADOWS", library.shadowed_note("thing"))


class DegradedRootTests(unittest.TestCase):
    """An unavailable personal root must cost the personal half, nothing more."""

    def test_index_still_lists_bundled_when_personal_is_unavailable(self):
        with _TempRoots() as roots:
            roots.write(roots.bundled, "thing", GOOD)
            library.personal_root = lambda: None
            self.assertEqual([("bundled", roots.bundled)], library.roots())
            self.assertEqual([e["module"] for e in library.index()], ["thing"])
            library.ensure_on_path()          # must not raise
            self.assertIn(roots.bundled, sys.path)
            sys.path.remove(roots.bundled)

    def test_save_refuses_rather_than_writing_into_the_bundled_root(self):
        with _TempRoots() as roots:
            library.personal_root = lambda: None
            with self.assertRaises(RuntimeError):
                library.save("thing", GOOD)
            self.assertEqual(os.listdir(roots.bundled), [])


class PathTests(unittest.TestCase):
    def test_ensure_on_path_puts_personal_first_and_is_idempotent(self):
        saved = list(sys.path)
        try:
            with _TempRoots() as roots:
                library.ensure_on_path()
                library.ensure_on_path()
                self.assertEqual(sys.path[:2], [roots.personal, roots.bundled])
                self.assertEqual(sys.path.count(roots.personal), 1)
        finally:
            sys.path[:] = saved

    def test_missing_root_is_skipped(self):
        saved = list(sys.path)
        try:
            with _TempRoots() as roots:
                os.rmdir(roots.bundled)
                library.ensure_on_path()
                self.assertEqual(sys.path[0], roots.personal)
                self.assertNotIn(roots.bundled, sys.path)
        finally:
            sys.path[:] = saved


class ToolTests(unittest.TestCase):
    def test_listing_needs_no_arguments_and_passes_precheck(self):
        self.assertEqual(tools_library._precheck_script_library({}), "")

    def test_half_a_save_is_refused_with_the_missing_half_named(self):
        self.assertIn("no 'name'",
                      tools_library._precheck_script_library({"code": GOOD}))
        self.assertIn("no 'code'",
                      tools_library._precheck_script_library({"name": "thing"}))

    def test_precheck_rejects_a_bad_module_before_the_gui_thread(self):
        self.assertIn("module docstring", tools_library._precheck_script_library(
            {"name": "thing", "code": "def f():\n    'One.'\n"}))

    def test_run_lists_then_saves(self):
        with _TempRoots():
            self.assertIn("empty", tools_library._run_script_library({}))
            out = tools_library._run_script_library({"name": "thing", "code": GOOD})
            self.assertIn("Saved thing.py", out)
            self.assertIn("useful(label, height",
                          tools_library._run_script_library({}))

    def test_schema_declares_both_optional_arguments(self):
        schema = tools_library._SCRIPT_LIBRARY_SCHEMA["inputSchema"]
        self.assertEqual(set(schema["properties"]), {"name", "code"})
        self.assertNotIn("required", schema)   # a bare listing must be legal


class RegistryTests(unittest.TestCase):
    def test_tool_is_registered_with_its_precheck(self):
        from freecad.freecadclaude import freecad_tools

        entry = freecad_tools.TOOLS["script_library"]
        self.assertIs(entry["run"], tools_library._run_script_library)
        self.assertIs(entry["precheck"], tools_library._precheck_script_library)
        self.assertIn("script_library",
                      [s["name"] for s in freecad_tools.list_schemas()])


class BundledLibraryTests(unittest.TestCase):
    """The shipped modules must pass the same bar a saved one does."""

    def test_every_bundled_module_validates(self):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                            "freecad", "freecadclaude", "library")
        names = [f for f in sorted(os.listdir(root)) if f.endswith(".py")]
        self.assertTrue(names, "the bundled library should not be empty")
        for name in names:
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                code = fh.read()
            self.assertEqual(library.validate(name[:-3], code), "", name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
