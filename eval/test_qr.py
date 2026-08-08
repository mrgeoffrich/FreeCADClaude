#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Encoder tests for freecad/freecadclaude/qr.py.

The module under test imports no Qt and no FreeCAD -- it returns a matrix of
booleans and nothing else -- so this needs no GUI and no running FreeCAD:

    python3 eval/test_qr.py
    freecadcmd /abs/path/to/eval/test_qr.py

(freecadcmd needs an ABSOLUTE path -- given a relative one it silently runs
nothing and still exits 0.)

**Why this test is written against reference matrices rather than properties.**
A QR encoder that is subtly wrong does not crash and does not look wrong: it
produces a plausible grid with correct-looking finder patterns that no phone
will read. That is exactly what happened while writing this -- the first version
treated version 6 at level L as a single block (it is two), and the symbol it
produced was structurally perfect and decoded as *nothing*. Only a
module-for-module comparison against a known-good code catches that.

So :data:`_REFERENCE` holds four full matrices, one per supported version,
baked in as literals. They were **not** produced by the code under test. They
came from the ``qrcode`` PyPI package (level L, mask 0, byte mode, the version
pinned) in a throwaway virtualenv, and each was then independently confirmed by
*decoding* it with ``zxing-cpp`` back to the original string. Neither package
is a dependency of the addon or of the test -- the matrices are literals here,
and the generator was scratch tooling. (``segno`` was tried as a third source
and disagrees on padding only: its ``write_padding_bits`` adds a whole zero
byte when the stream already ends on a codeword boundary, where the spec adds
none. Its symbols still scan, since padding is discarded by any reader, but it
is the wrong reference to pin bytes against.)

Exit: 0 = all passed, 1 = a failure.
"""

import importlib.util
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_qr():
    """Import qr.py directly by path.

    Not ``from freecad.freecadclaude import qr``: importing the package pulls in
    modules that expect a FreeCAD process. This one is deliberately standalone,
    so load it that way and keep the test honest about that.
    """
    path = os.path.join(_ROOT, "freecad", "freecadclaude", "qr.py")
    spec = importlib.util.spec_from_file_location("_fcc_qr", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


qr = _load_qr()

_failures = []


def check(name, condition, detail=""):
    status = "ok  " if condition else "FAIL"
    print(f"  {status} {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(name)


# -- reference matrices -------------------------------------------------------
#
# ``(version, text, rows)``, where each row is the hex of that row's modules with
# column 0 as the most significant bit (so the leading nibble of row 0 is always
# "1", the top-left finder's first four dark modules). Hex rather than ASCII art
# because 140 lines of '#' and '.' in a source file is not more readable than
# this once you are past the first glance -- and a failure prints the art anyway
# (see _art), which is where looking at it actually helps.

_REFERENCE = (
    (3, 'http://192.168.1.23:54321/?t=Ab3xY_9qLm-Kt7Rn2Ps4Zg',
     (
      "1fc6237f",
      "1046b241",
      "1753105d",
      "1749d35d",
      "174cf35d",
      "1046f341",
      "1fd5557f",
      "00139b00",
      "1df0acc4",
      "0f283c69",
      "19f405f7",
      "02bcecea",
      "11c4ee6b",
      "1e05306d",
      "19fd1803",
      "038eec9a",
      "1ddc2c43",
      "020714e5",
      "15e9c98b",
      "0e08ccea",
      "17e089f0",
      "001ccd17",
      "1fd9135b",
      "10518b19",
      "1757ddf9",
      "174dd799",
      "1751829d",
      "1058ef8a",
      "1fd811cb",
     )),
    (4, 'http://freecad-desktop.local:54321/?t=Ab3xY_9qLm-Kt7Rn2Ps4Zg',
     (
      "1fc468e7f",
      "104867f41",
      "17523155d",
      "17499125d",
      "1749c8a5d",
      "10476a341",
      "1fd55557f",
      "00123e800",
      "1df10adc4",
      "1f3b8ddcd",
      "08e7440f1",
      "150dccd9a",
      "17c640fc1",
      "1c1e50de5",
      "074c911db",
      "0eae28b0a",
      "08d909fc3",
      "16b7b1d45",
      "0de350d7b",
      "051dced62",
      "104a4dfc3",
      "011243cc1",
      "167c90d23",
      "0a021fa62",
      "12d932df8",
      "001790d1b",
      "1fdf5d753",
      "1051cfd12",
      "175a48df2",
      "174a7d131",
      "175880abd",
      "105e19f02",
      "1fd10ef73",
     )),
    (5, "Sphinx of black quartz, judge my vow -- 0123456789 "
        "!#$%&'()*+,-./:;<=>?@ and a tail to push it past v4.",
     (
      "1fc47b777f",
      "1048bebb41",
      "175260555d",
      "174841155d",
      "17492b725d",
      "1046a7a741",
      "1fd555557f",
      "0011b8eb00",
      "1df38eebc4",
      "1a88d481cd",
      "08d519c977",
      "0217ecde0b",
      "175c92c9e2",
      "1090c88c4b",
      "16c5d41877",
      "1f95f99fe0",
      "03e4bfcde0",
      "0799188947",
      "12dd4504db",
      "0b16a9f9a2",
      "1f61fbcdc0",
      "1208988163",
      "13dd449123",
      "09bbafbffa",
      "1de09bcd41",
      "03251081c9",
      "16f05980a7",
      "0b84fbcfa1",
      "17d08bcdf1",
      "0019d80918",
      "1fd8105753",
      "105ea88911",
      "1755dfcdf3",
      "1741880fb7",
      "175d454437",
      "1056a8eb82",
      "1fd89bcf53",
     )),
    (6, 'The quick brown fox jumps over the lazy dog, again and again and again'
        ', and then once more for luck. 0123456789 END',
     (
      "1fc5ee2e27f",
      "104477eeb41",
      "175bdd7665d",
      "1744fe1645d",
      "1742ddb2f5d",
      "1045babeb41",
      "1fd5555557f",
      "0013efceb00",
      "1dfb99c8dc4",
      "0a31999d1c3",
      "11fc440011d",
      "189fddeddab",
      "0e6555cb9c2",
      "071f77c89c7",
      "1263b95d457",
      "052599888ca",
      "0a4bff9d9c0",
      "15b1119916b",
      "00d0889d9e7",
      "1ba622bee30",
      "0b7401e89e8",
      "0e96abc95cb",
      "0b504404d57",
      "02a6aba99e2",
      "18ed999bbc3",
      "06859994ccb",
      "0e6d559cdaf",
      "031aeeec9e0",
      "137155cdb49",
      "0717ff499e8",
      "16cf759109f",
      "0891dee9d41",
      "1173b998bf1",
      "00180001b13",
      "1fd9994935f",
      "105f33bad11",
      "175403cddf3",
      "174e20c1135",
      "175488953b3",
      "105eaaeaf82",
      "1fdd9bd8b43",
     )),
)


def _unpack(rows):
    """A reference matrix's hex rows back into a list of rows of bool."""
    n = len(rows)
    return [
        [bool((int(row, 16) >> (n - 1 - col)) & 1) for col in range(n)]
        for row in rows
    ]


def _art(matrix):
    """The matrix as '#'/'.' text, for reading a failure rather than counting it."""
    return "\n".join(
        "      " + "".join("#" if cell else "." for cell in row) for row in matrix
    )


def _compare(name, got, want):
    diffs = [
        (r, c)
        for r in range(len(want))
        for c in range(len(want))
        if got[r][c] != want[r][c]
    ]
    check(name, not diffs, f"{len(diffs)} modules differ, first at {diffs[:6]}")
    if diffs:
        print("    got:")
        print(_art(got))
        print("    want:")
        print(_art(want))


# -- checks -------------------------------------------------------------------


def _check_reference():
    print("reference matrices (source: the qrcode package, decode-verified with zxing-cpp)")
    for version, text, rows in _REFERENCE:
        want = _unpack(rows)
        _compare(
            f"v{version} ({len(text)} chars) matches the reference",
            qr.encode(text, version),
            want,
        )
        # The same payload with no version argument must land on the same
        # version -- version selection and the capacity table have to agree, or
        # a URL one byte over a boundary silently changes symbol.
        auto = qr.encode(text)
        check(
            f"v{version} is also what auto-selection picks",
            auto == want,
            f"auto gave a {len(auto)}x{len(auto)} symbol",
        )


def _check_format_bits():
    """Re-derive the hardcoded format table from the BCH(15,5) generator.

    The table in qr.py is hardcoded so that mask 0 costs no computation, and a
    single wrong bit there would produce a symbol that looks right and reads as
    the wrong mask (i.e. not at all). This is the check that makes hardcoding
    them honest.
    """
    print("format information (EC level L)")
    for mask in range(8):
        data = (0b01 << 3) | mask  # 01 = level L, then the 3-bit mask number
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        want = (data << 10 | rem) ^ 0x5412
        got = qr._FORMAT_BITS_L[mask]
        check(f"mask {mask} format bits", got == want, f"{got:015b} != {want:015b}")


def _check_structure():
    """Function patterns, on the largest supported symbol.

    Redundant with the reference comparison when everything passes; the point is
    what a *failure* says. "156 modules differ" localises nothing; "the alignment
    pattern is in the wrong place" localises it completely.
    """
    print("function patterns (v6)")
    matrix = qr.encode("x" * 134)
    n = len(matrix)
    check("v6 is 41 modules square", n == 41 and all(len(r) == 41 for r in matrix), n)
    check("every module is a bool", all(isinstance(c, bool) for r in matrix for c in r))

    for name, top, left in (
        ("top-left", 0, 0),
        ("top-right", 0, n - 7),
        ("bottom-left", n - 7, 0),
    ):
        ring = all(matrix[top][left + i] and matrix[top + 6][left + i] for i in range(7))
        ring = ring and all(matrix[top + i][left] and matrix[top + i][left + 6] for i in range(7))
        light = not any(matrix[top + 1][left + 1 + i] for i in range(5))
        core = all(matrix[top + 3][left + 2 + i] for i in range(3))
        check(f"{name} finder pattern", ring and light and core)

    check(
        "horizontal timing pattern",
        all(matrix[6][i] == (i % 2 == 0) for i in range(8, n - 8)),
    )
    check(
        "vertical timing pattern",
        all(matrix[i][6] == (i % 2 == 0) for i in range(8, n - 8)),
    )
    # Versions 3-6 have exactly one alignment pattern, and for all of them its
    # centre is seven modules in from the bottom-right corner.
    centre = n - 7
    check(
        "alignment pattern at (n-7, n-7)",
        matrix[centre][centre]
        and not any(matrix[centre - 1][centre - 1 + i] for i in range(3))
        and all(matrix[centre - 2][centre - 2 + i] for i in range(5)),
    )
    check("dark module at (4v+9, 8)", matrix[4 * 6 + 9][8] is True)
    check("separator below the top-left finder is light", not any(matrix[7][:8]))


def _check_capacity():
    """Version selection, the capacity boundaries, and the refusals."""
    print("capacity and version selection")
    expected = {3: 53, 4: 78, 5: 106, 6: 134}
    for version, cap in expected.items():
        check(f"v{version} holds {cap} bytes", qr.capacity(version) == cap,
              qr.capacity(version))
        check(f"v{version} is {qr.size(version)} modules", qr.size(version) == version * 4 + 17)

    # Every boundary, both sides: one byte over a version's capacity must step up
    # to the next, and the symbol must grow by exactly four modules.
    for version, cap in ((3, 53), (4, 78), (5, 106)):
        check(
            f"{cap} bytes stays in v{version}",
            len(qr.encode("x" * cap)) == qr.size(version),
        )
        check(
            f"{cap + 1} bytes steps up to v{version + 1}",
            len(qr.encode("x" * (cap + 1))) == qr.size(version + 1),
        )

    for payload, note in (("x" * 135, "135 bytes"), ("é" * 68, "68 two-byte chars")):
        try:
            qr.encode(payload)
            check(f"{note} is refused", False, "no ValueError raised")
        except ValueError as exc:
            check(f"{note} is refused", "version 6" in str(exc), str(exc))

    try:
        qr.encode("hi", 7)
        check("version 7 is refused", False, "no ValueError raised")
    except ValueError as exc:
        check("version 7 is refused", "3-6" in str(exc), str(exc))

    try:
        qr.encode("x" * 60, 3)
        check("an oversized payload for an explicit version is refused", False)
    except ValueError as exc:
        check(
            "an oversized payload for an explicit version is refused",
            "max 53" in str(exc),
            str(exc),
        )

    # A str is UTF-8 encoded, so the capacity is in bytes and not characters --
    # worth pinning, because the pairing URL is ASCII and would never notice.
    check(
        "str is encoded as UTF-8, not per-character",
        qr.encode("é" * 53) == qr.encode(("é" * 53).encode("utf-8")),
    )
    check("a 53-char two-byte payload needs v5", len(qr.encode("é" * 53)) == 37)


def main():
    _check_reference()
    _check_format_bits()
    _check_structure()
    _check_capacity()

    print()
    if _failures:
        print(f"FAIL: {len(_failures)} check(s) failed: {', '.join(_failures)}")
        return 1
    print("PASS")
    return 0


# No __main__ guard: freecadcmd *imports* the script under a module name taken
# from the filename, so a guarded body would silently never run there -- which
# is the one interpreter this most needs to work under. It also tears the
# process down on SystemExit without flushing, so do that ourselves or the
# entire report is discarded and the run looks like it printed nothing.
_status = main()
sys.stdout.flush()
sys.exit(_status)
