# SPDX-License-Identifier: LGPL-2.1-or-later
"""A QR encoder, cut down to exactly what pairing a device needs.

The pairing URL is ``http://192.168.1.23:54321/?t=<22 chars>`` -- about 54
characters, most of them a case-sensitive secret. Typing that on a tablet once
per session is the kind of friction that decides whether a feature gets used,
so the chat panel shows a code to scan instead.

There is no pip dependency available here (the addon installs as a plain file
copy -- see CLAUDE.md), so this is a stdlib encoder. It stays small because the
*scope* is small, not because corners were cut:

- **Byte mode, error-correction level L, versions 3-6 only** -- 53 to 134
  characters, which brackets the URL with room for a long host name. The cap at
  version 6 is what keeps this short: versions 7 and up carry an additional
  18-bit version-information block in two corners, and their alignment pattern
  count grows from one to six. Neither is hard; both are code that nothing here
  would ever exercise.
- **Mask 0, fixed.** The spec allows any of the eight, and choosing the best one
  is what the penalty-scoring pass is for; at this size a reader will not notice
  the difference. Skipping it also turns the format bits into a hardcoded table
  (:data:`_FORMAT_BITS_L`) instead of a BCH computation.

The design doc claims versions 1-6 at level L are all single-block and that this
therefore needs no interleaving. **That is wrong, and it was caught by the
reference test rather than by reading**: version 6 at level L is *two* blocks
(2 x 86 codewords, 68 data each), and a single-block version 6 produces a
symbol that no reader will decode -- verified, it decoded as nothing at all.
What is true, and is the reason the interleaving here is six lines rather than
thirty, is that every block in versions 3-6 at level L is the **same size**, so
there is no group-1/group-2 split to carry (see :func:`_interleave`).

This module imports **no Qt and no FreeCAD** and returns a plain matrix of
booleans, so it is unit-testable headlessly against known-good codes -- see
``eval/test_qr.py``, which compares against reference matrices. That matters
more here than in most modules: a QR encoder that is subtly wrong still produces
a plausible-looking grid, and the failure only shows up as a phone that will not
scan it.

    matrix = qr.encode("http://192.168.1.23:54321/?t=abc")
    matrix[row][col]  # True == dark
"""

#: The version range we implement. Below 3 the URL would not fit; above 6 the
#: codewords are split into interleaved blocks (see the module docstring).
MIN_VERSION = 3
MAX_VERSION = 6

#: Per version at EC level L: ``(blocks, data codewords per block, EC codewords
#: per block)``. ``blocks * (data + ec)`` is the version's total codeword count
#: (70/100/134/172). Versions 3-5 are one block; version 6 is two, which is the
#: only reason :func:`_interleave` exists.
_CODEWORDS_L = {
    3: (1, 55, 15),
    4: (1, 80, 20),
    5: (1, 108, 26),
    6: (2, 68, 18),
}

#: Byte-mode mode indicator, 4 bits.
_MODE_BYTE = 0b0100

#: Width of the character-count indicator for byte mode. 8 bits for versions
#: 1-9 (it widens to 16 at version 10, which is outside our range).
_COUNT_BITS = 8

#: The pad bytes the spec names, alternating, once the terminator and the
#: byte-alignment zeros have been written.
_PAD_BYTES = (0xEC, 0x11)

#: The 15-bit format strings for **error-correction level L**, indexed by mask
#: pattern. Each is the 5-bit format data (``01`` for level L, then the 3-bit
#: mask number) extended with BCH(15,5) and XORed with 0x5412 -- hardcoded
#: rather than computed because we only ever use one of them, and because these
#: are published values that can be checked by eye against the spec's Table C.1.
#: (``eval/test_qr.py`` re-derives all eight from the BCH generator and asserts
#: they match, so a typo here cannot survive.) Only index 0 is used; the rest
#: are here so a future penalty-scoring pass is a one-line change.
_FORMAT_BITS_L = (
    0b111011111000100,  # mask 0
    0b111001011110011,  # mask 1
    0b111110110101010,  # mask 2
    0b111100010011101,  # mask 3
    0b110011000101111,  # mask 4
    0b110001100011000,  # mask 5
    0b110110001000001,  # mask 6
    0b110100101110110,  # mask 7
)

#: The mask we always apply. Its condition is ``(row + col) % 2 == 0``.
_MASK = 0

# -- GF(256) ------------------------------------------------------------------
#
# Reed-Solomon runs over GF(256) with the standard QR primitive polynomial
# 0x11d. Building the log/antilog tables once at import turns every field
# multiply into two table lookups and an add; the antilog table is doubled in
# length so the exponent sum never needs a modulo.

_EXP = [0] * 512
_LOG = [0] * 256


def _build_tables():
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_build_tables()


def _mul(a, b):
    """Multiply two GF(256) elements. Zero is special-cased: it has no log."""
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator_poly(degree):
    """The RS generator polynomial of `degree`, highest-order coefficient first.

    That is the product of ``(x - a^i)`` for i in 0..degree-1; subtraction is
    XOR here, so it is written as ``(x + a^i)``.
    """
    poly = [1]
    for i in range(degree):
        product = [0] * (len(poly) + 1)
        for j, coeff in enumerate(poly):
            product[j] ^= coeff  # the x term
            product[j + 1] ^= _mul(coeff, _EXP[i])  # the a^i term
        poly = product
    return poly


def _ec_codewords(data, count):
    """The `count` error-correction codewords for `data` (a bytes-like).

    Polynomial long division of the message by the generator, keeping only the
    remainder -- the usual shift-register form, one pass per data codeword.
    """
    gen = _generator_poly(count)
    rem = [0] * count
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        if factor:
            for i in range(count):
                rem[i] ^= _mul(gen[i + 1], factor)
    return rem


# -- payload ------------------------------------------------------------------


def capacity(version):
    """How many bytes fit in `version` at level L, in byte mode."""
    blocks, data_per_block, _ = _CODEWORDS_L[version]
    # The mode indicator and the character count come out of the same budget.
    return (blocks * data_per_block * 8 - 4 - _COUNT_BITS) // 8


def _choose_version(payload):
    for version in range(MIN_VERSION, MAX_VERSION + 1):
        if len(payload) <= capacity(version):
            return version
    raise ValueError(
        f"{len(payload)} bytes is too long for a version-{MAX_VERSION} QR code "
        f"at error-correction level L (max {capacity(MAX_VERSION)}). "
        "This encoder deliberately stops at version 6 -- see qr.py."
    )


def _interleave(blocks, ec_blocks):
    """The final codeword sequence: data interleaved, then EC interleaved.

    Every block here is the same length (versions 3-6 at level L), so this is a
    ``zip`` -- the general case, where a version splits its blocks into two
    groups of differing size, needs the ragged walk that ``zip`` won't do. If
    this ever grows past version 6, that is the line to change.
    """
    out = bytearray()
    for column in zip(*blocks):
        out.extend(column)
    for column in zip(*ec_blocks):
        out.extend(column)
    return bytes(out)


def _codewords(payload, version):
    """The full codeword sequence for `payload`: data, padded, EC, interleaved."""
    block_count, data_per_block, ec_per_block = _CODEWORDS_L[version]
    data_count = block_count * data_per_block
    capacity_bits = data_count * 8

    bits = []

    def _push(value, width):
        for shift in range(width - 1, -1, -1):
            bits.append((value >> shift) & 1)

    _push(_MODE_BYTE, 4)
    _push(len(payload), _COUNT_BITS)
    for byte in payload:
        _push(byte, 8)

    # Terminator: up to four zero bits, truncated if the payload very nearly
    # fills the version (a full-capacity payload gets fewer than four).
    _push(0, min(4, capacity_bits - len(bits)))
    # Then zeros up to the next byte boundary, then the alternating pad bytes.
    _push(0, -len(bits) % 8)

    data = bytearray(
        sum(bit << (7 - i) for i, bit in enumerate(bits[offset:offset + 8]))
        for offset in range(0, len(bits), 8)
    )
    for pad in range(data_count - len(data)):
        data.append(_PAD_BYTES[pad % 2])

    blocks = [
        bytes(data[i * data_per_block:(i + 1) * data_per_block])
        for i in range(block_count)
    ]
    return _interleave(blocks, [_ec_codewords(b, ec_per_block) for b in blocks])


# -- symbol -------------------------------------------------------------------


def size(version):
    """The symbol's width and height in modules (quiet zone excluded)."""
    return version * 4 + 17


def _blank_symbol(version):
    """A matrix with every function pattern drawn, plus the map of where they are.

    Returns ``(matrix, function)``: both `size(version)` square, `matrix` holding
    dark/light and `function` holding "this module is structural, so data
    placement must skip it and the mask must not touch it".
    """
    n = size(version)
    matrix = [[False] * n for _ in range(n)]
    function = [[False] * n for _ in range(n)]

    def _set(row, col, dark):
        matrix[row][col] = dark
        function[row][col] = True

    # Finder patterns, with their separators. Drawn as a 9x9 block centred on
    # each 7x7 finder so the one-module light separator falls out of the same
    # loop; the parts of that block that lie outside the symbol are clipped.
    for top, left in ((0, 0), (0, n - 7), (n - 7, 0)):
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                row, col = top + dy, left + dx
                if not (0 <= row < n and 0 <= col < n):
                    continue
                # Dark where the ring is: the outer 7x7 border and the 3x3 core.
                edge = max(abs(dy - 3), abs(dx - 3))
                _set(row, col, edge != 2 and edge <= 3)

    # Timing patterns: row 6 and column 6, dark on even coordinates. They run
    # the full width; the finder blocks above have already claimed their ends.
    for i in range(n):
        if not function[6][i]:
            _set(6, i, i % 2 == 0)
        if not function[i][6]:
            _set(i, 6, i % 2 == 0)

    # The single alignment pattern. Versions 2-6 have exactly one, and for these
    # versions its centre is always seven modules in from the bottom-right
    # corner (the table's second coordinate, 4*version + 10, equals n - 7).
    centre = n - 7
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            _set(centre + dy, centre + dx, max(abs(dy), abs(dx)) != 1)

    # Reserve the format-information areas. They are written after masking (see
    # encode), but data placement has to know to step over them now.
    for i in range(9):
        function[8][i] = True
        function[i][8] = True
    for i in range(8):
        function[8][n - 1 - i] = True
        function[n - 1 - i][8] = True

    return matrix, function


def _place_codewords(matrix, function, codewords):
    """Fill the data modules in the spec's zig-zag order.

    Two-module-wide columns, right to left, alternating upward and downward,
    each pair filled right module first. Column 6 is the vertical timing pattern
    and is not part of a pair at all -- the ``right -= 1`` below is what steps
    the whole scan one to the left once it is passed, rather than skipping
    modules inside it.

    Any modules left over after the codewords run out are the version's
    remainder bits (7, for versions 2-6). They stay light here and are masked
    with everything else, which is exactly what the spec asks for.
    """
    n = len(matrix)
    bit = 0
    total_bits = len(codewords) * 8
    for right in range(n - 1, 0, -2):
        if right <= 6:
            right -= 1
        upward = ((right + 1) & 2) == 0
        for vert in range(n):
            row = (n - 1 - vert) if upward else vert
            for col in (right, right - 1):
                if function[row][col] or bit >= total_bits:
                    continue
                matrix[row][col] = bool(
                    (codewords[bit >> 3] >> (7 - (bit & 7))) & 1
                )
                bit += 1


def _apply_mask(matrix, function):
    """XOR mask 0 -- ``(row + col) % 2 == 0`` -- over the data modules only.

    Never over a function pattern: the finders and timing are what a reader
    locks onto, and the format bits carry their own mask number, so masking
    either of them would make the symbol unreadable rather than merely ugly.
    """
    for row, cells in enumerate(matrix):
        for col in range(len(cells)):
            if not function[row][col] and (row + col) % 2 == 0:
                cells[col] = not cells[col]


def _place_format_bits(matrix):
    """Write both copies of the 15-bit format string, and the dark module.

    Bit 0 is the least significant. The two copies are placed in the spec's
    order, which is not a simple mirror of each other -- hence the two separate
    walks rather than one shared loop.
    """
    n = len(matrix)
    bits = _FORMAT_BITS_L[_MASK]

    def _bit(i):
        return bool((bits >> i) & 1)

    # Copy 1: wrapped around the top-left finder, down column 8 then along row 8.
    for i in range(6):
        matrix[i][8] = _bit(i)
    matrix[7][8] = _bit(6)
    matrix[8][8] = _bit(7)
    matrix[8][7] = _bit(8)
    for i in range(9, 15):
        matrix[8][14 - i] = _bit(i)

    # Copy 2: along the top row of the bottom-left finder, then across to the
    # right edge of row 8.
    for i in range(8):
        matrix[8][n - 1 - i] = _bit(i)
    for i in range(8, 15):
        matrix[n - 15 + i][8] = _bit(i)

    # The dark module: always set, always here, carries no information.
    matrix[n - 8][8] = True


def encode(text, version=None):
    """Encode `text` as a QR matrix. ``matrix[row][col]`` is True where dark.

    `text` may be a str (encoded UTF-8) or a bytes-like. `version` picks a
    specific one of 3-6; by default the smallest that fits is used. Raises
    ``ValueError`` if the payload does not fit version 6, or if `version` is
    outside the supported range.

    The result has **no quiet zone** -- the renderer adds it, because how much
    margin to leave is a display decision (the panel uses the spec's 4 modules).
    """
    payload = text.encode("utf-8") if isinstance(text, str) else bytes(text)

    if version is None:
        version = _choose_version(payload)
    elif version not in _CODEWORDS_L:
        raise ValueError(
            f"unsupported QR version {version}: this encoder implements "
            f"{MIN_VERSION}-{MAX_VERSION} only (see qr.py)"
        )
    elif len(payload) > capacity(version):
        raise ValueError(
            f"{len(payload)} bytes does not fit version {version} at level L "
            f"(max {capacity(version)})"
        )

    matrix, function = _blank_symbol(version)
    _place_codewords(matrix, function, _codewords(payload, version))
    _apply_mask(matrix, function)
    _place_format_bits(matrix)
    return matrix
