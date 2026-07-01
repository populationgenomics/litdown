"""MathML → LaTeX converter.

Public API:
    mml_to_tex(elem)         — convert a MathML Element to a LaTeX string
    render_mathml(elem, ...) — wrap in $…$ or $$…$$
"""

import re as _re
import xml.etree.ElementTree as ET
from collections.abc import Iterable

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

# Unicode operators → LaTeX commands.  mo element text is looked up here.
_MO = {
    # Arithmetic / algebra
    '+': '+',
    '-': '-',
    '−': '-',
    '±': r'\pm',
    '∓': r'\mp',
    '×': r'\times',
    '÷': r'\div',
    '·': r'\cdot',
    '∘': r'\circ',
    '=': '=',
    '≠': r'\neq',
    '<': '<',
    '>': '>',
    '≤': r'\leq',
    '≥': r'\geq',
    '≪': r'\ll',
    '≫': r'\gg',
    '≈': r'\approx',
    '≡': r'\equiv',
    '∼': r'\sim',
    '≃': r'\simeq',
    '∝': r'\propto',
    # Set / logic
    '∈': r'\in',
    '∉': r'\notin',
    '∋': r'\ni',
    '⊂': r'\subset',
    '⊃': r'\supset',
    '⊆': r'\subseteq',
    '⊇': r'\supseteq',
    '∪': r'\cup',
    '∩': r'\cap',
    '∅': r'\emptyset',
    '∧': r'\wedge',
    '∨': r'\vee',
    '¬': r'\neg',
    # Calculus / analysis
    '∑': r'\sum',
    '∏': r'\prod',
    '∫': r'\int',
    '∬': r'\iint',
    '∭': r'\iiint',
    '∮': r'\oint',
    '∂': r'\partial',
    '∇': r'\nabla',
    '∞': r'\infty',
    '′': r"'",
    '″': r"''",
    # Arrows
    '→': r'\rightarrow',
    '←': r'\leftarrow',
    '↔': r'\leftrightarrow',
    '⇒': r'\Rightarrow',
    '⇐': r'\Leftarrow',
    '⇔': r'\Leftrightarrow',
    '↑': r'\uparrow',
    '↓': r'\downarrow',
    '↦': r'\mapsto',
    # Dots
    '…': r'\ldots',
    '⋯': r'\cdots',
    '⋮': r'\vdots',
    '⋱': r'\ddots',
    # Misc
    '_': r'\_',  # LOW LINE — visible underscore in math mode
    '$': r'\$',  # DOLLAR SIGN — literal currency symbol
    '#': r'\#',  # NUMBER SIGN — literal hash
    '%': r'\%',  # PERCENT SIGN — literal percent
    '|': r'|',
    '‖': r'\|',
    '⊥': r'\perp',
    '∥': r'\parallel',
    '⊕': r'\oplus',
    '⊗': r'\otimes',
    '√': r'\sqrt',
    # Arrows (extended)
    '↕': r'\updownarrow',
    '⇑': r'\Uparrow',
    '⇓': r'\Downarrow',
    '⇕': r'\Updownarrow',
    # Brackets (when used as mo rather than mfenced).
    # Use plain chars here — \left/\right require pairing; mfenced handles sizing.
    '(': '(',
    ')': ')',
    '[': '[',
    ']': ']',
    '{': r'\{',
    '}': r'\}',  # literal braces (not TeX groups)
    '⟨': r'\langle',
    '⟩': r'\rangle',
    '\u2329': r'\langle',
    '\u232a': r'\rangle',  # CJK angle brackets (common alias)
    '⌈': r'\lceil',
    '⌉': r'\rceil',
    '⌊': r'\lfloor',
    '⌋': r'\rfloor',
    # Invisible operators — produce no output in static rendering
    '\u2061': '',  # FUNCTION APPLICATION
    '\u2062': '',  # INVISIBLE TIMES
    '\u2063': '',  # INVISIBLE SEPARATOR
    '\u2064': '',  # INVISIBLE PLUS
    # Unicode spaces — map to LaTeX spacing commands
    '\u00a0': r'\ ',  # NO-BREAK SPACE
    '\u2002': r'\;',  # EN SPACE
    '\u2003': r'\quad',  # EM SPACE
    '\u2004': r'\;',  # THREE-PER-EM SPACE
    '\u2005': r'\;',  # FOUR-PER-EM SPACE
    '\u2009': r'\,',  # THIN SPACE
    '\u200a': r'\,',  # HAIR SPACE
    '\u205f': r'\:',  # MEDIUM MATHEMATICAL SPACE
    # Misc symbols
    '\u0332': r'\_',  # COMBINING LOW LINE → visible underscore
    '\u200b': '',  # ZERO WIDTH SPACE → discard
}

# Greek and other special letters used in mi / mo.
_LETTERS = {
    'α': r'\alpha',
    'β': r'\beta',
    'γ': r'\gamma',
    'δ': r'\delta',
    'ε': r'\epsilon',
    'ζ': r'\zeta',
    'η': r'\eta',
    'θ': r'\theta',
    'ι': r'\iota',
    'κ': r'\kappa',
    'λ': r'\lambda',
    'μ': r'\mu',
    'ν': r'\nu',
    'ξ': r'\xi',
    'π': r'\pi',
    'ρ': r'\rho',
    'σ': r'\sigma',
    'τ': r'\tau',
    'υ': r'\upsilon',
    'φ': r'\phi',
    'χ': r'\chi',
    'ψ': r'\psi',
    'ω': r'\omega',
    'Γ': r'\Gamma',
    'Δ': r'\Delta',
    'Θ': r'\Theta',
    'Λ': r'\Lambda',
    'Ξ': r'\Xi',
    'Π': r'\Pi',
    'Σ': r'\Sigma',
    'Υ': r'\Upsilon',
    'Φ': r'\Phi',
    'Ψ': r'\Psi',
    'Ω': r'\Omega',
    # Variants
    'ϵ': r'\varepsilon',
    'ϑ': r'\vartheta',
    'ϕ': r'\varphi',
    'ϱ': r'\varrho',
    'ς': r'\varsigma',
    # Misc letter-like
    '∞': r'\infty',
    'ℓ': r'\ell',
    'ℏ': r'\hbar',
    'ℜ': r'\Re',
    'ℑ': r'\Im',
    '℘': r'\wp',
    '†': r'\dagger',
    '‡': r'\ddagger',
    '′': r"'",
    '″': r"''",
    '‴': r"'''",  # primes (also in _MO)
    # Double-struck (blackboard bold) — requires amssymb
    'ℕ': r'\mathbb{N}',
    'ℤ': r'\mathbb{Z}',
    'ℚ': r'\mathbb{Q}',
    'ℝ': r'\mathbb{R}',
    'ℂ': r'\mathbb{C}',
    'ℙ': r'\mathbb{P}',
    '𝔽': r'\mathbb{F}',
    # Mathematical italic / double-struck letters (U+2100 block)
    'ⅆ': r'\mathrm{d}',  # differential d
    'ⅇ': r'\mathrm{e}',  # Euler's number
    'ⅈ': r'\mathrm{i}',  # imaginary unit
    'ⅉ': r'\mathrm{j}',  # imaginary unit (engineering)
    # Script letters
    '\u212c': r'\mathcal{B}',
    '\u2130': r'\mathcal{E}',
    '\u2131': r'\mathcal{F}',
    '\u210b': r'\mathcal{H}',
    '\u2110': r'\mathcal{I}',
    '\u2112': r'\mathcal{L}',
    '\u2133': r'\mathcal{M}',
    '\u211b': r'\mathcal{R}',
    # Superscript digits / ordinal indicators
    '\u00b2': '2',
    '\u00b3': '3',
    '\u00b9': '1',
    '\u00aa': r'\mathrm{a}',  # FEMININE ORDINAL INDICATOR
    '\u00ba': r'\mathrm{o}',  # MASCULINE ORDINAL INDICATOR
    '\u00b0': r'\circ',  # DEGREE SIGN
    '\u00b4': r"'",  # ACUTE ACCENT → prime approximation
    '\u02d9': r'\cdot',  # DOT ABOVE (modifier letter)
    '\u2145': r'\mathbb{D}',  # DOUBLE-STRUCK ITALIC CAPITAL D
    # U+2146 DOUBLE-STRUCK ITALIC SMALL D is already mapped via the literal
    # '\u2146' entry above ("differential d").
    '\u212b': r'\text{\AA}',  # ANGSTROM SIGN
    # Standard math function names — map to LaTeX operator commands so they
    # render upright with correct spacing (e.g. <mi>ln</mi> → \ln).
    'arccos': r'\arccos',
    'arcsin': r'\arcsin',
    'arctan': r'\arctan',
    'arg': r'\arg',
    'cos': r'\cos',
    'cosh': r'\cosh',
    'cot': r'\cot',
    'coth': r'\coth',
    'csc': r'\csc',
    'deg': r'\deg',
    'det': r'\det',
    'dim': r'\dim',
    'exp': r'\exp',
    'gcd': r'\gcd',
    'hom': r'\hom',
    'inf': r'\inf',
    'ker': r'\ker',
    'lg': r'\lg',
    'lim': r'\lim',
    'liminf': r'\liminf',
    'limsup': r'\limsup',
    'ln': r'\ln',
    'log': r'\log',
    'max': r'\max',
    'min': r'\min',
    'Pr': r'\Pr',
    'sec': r'\sec',
    'sin': r'\sin',
    'sinh': r'\sinh',
    'sup': r'\sup',
    'tan': r'\tan',
    'tanh': r'\tanh',
}


# Extend _LETTERS with Mathematical Alphanumeric Symbols (U+1D400 block).
# Generated programmatically to avoid non-ASCII source characters.
def _add_math_alphanumeric() -> None:  # noqa: C901
    up = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    lo = 'abcdefghijklmnopqrstuvwxyz'
    # Mathematical bold:  capital U+1D400, small U+1D41A
    for _i, _c in enumerate(up):
        _LETTERS[chr(0x1D400 + _i)] = r'\mathbf{' + _c + '}'
    for _i, _c in enumerate(lo):
        _LETTERS[chr(0x1D41A + _i)] = r'\mathbf{' + _c + '}'
    # Mathematical italic: capital U+1D434, small U+1D44E (default italic = plain)
    for _i, _c in enumerate(up):
        _LETTERS[chr(0x1D434 + _i)] = _c
    for _i, _c in enumerate(lo):
        _LETTERS[chr(0x1D44E + _i)] = _c
    # Mathematical bold-italic: capital U+1D468, small U+1D482
    for _i, _c in enumerate(up):
        _LETTERS[chr(0x1D468 + _i)] = r'\boldsymbol{' + _c + '}'
    for _i, _c in enumerate(lo):
        _LETTERS[chr(0x1D482 + _i)] = r'\boldsymbol{' + _c + '}'
    # Mathematical script (calligraphic): capital U+1D49C, small U+1D4B6
    for _i, _c in enumerate(up):
        _LETTERS[chr(0x1D49C + _i)] = r'\mathcal{' + _c + '}'
    for _i, _c in enumerate(lo):
        _LETTERS[chr(0x1D4B6 + _i)] = r'\mathcal{' + _c + '}'
    # Mathematical fraktur: capital U+1D504, small U+1D51E
    for _i, _c in enumerate(up):
        _LETTERS[chr(0x1D504 + _i)] = r'\mathfrak{' + _c + '}'
    for _i, _c in enumerate(lo):
        _LETTERS[chr(0x1D51E + _i)] = r'\mathfrak{' + _c + '}'
    # Mathematical double-struck: capital U+1D538, small U+1D552
    for _i, _c in enumerate(up):
        _LETTERS[chr(0x1D538 + _i)] = r'\mathbb{' + _c + '}'
    for _i, _c in enumerate(lo):
        _LETTERS[chr(0x1D552 + _i)] = r'\mathbb{' + _c + '}'
    # Mathematical bold script: capital U+1D4D0, small U+1D4EA
    for _i, _c in enumerate(up):
        _LETTERS[chr(0x1D4D0 + _i)] = r'\mathcal{' + _c + '}'
    for _i, _c in enumerate(lo):
        _LETTERS[chr(0x1D4EA + _i)] = r'\mathcal{' + _c + '}'
    # Mathematical bold fraktur: capital U+1D56C, small U+1D586
    for _i, _c in enumerate(up):
        _LETTERS[chr(0x1D56C + _i)] = r'\mathfrak{' + _c + '}'
    for _i, _c in enumerate(lo):
        _LETTERS[chr(0x1D586 + _i)] = r'\mathfrak{' + _c + '}'
    # Mathematical sans-serif: capital U+1D5A0, small U+1D5BA
    for _i, _c in enumerate(up):
        _LETTERS[chr(0x1D5A0 + _i)] = r'\mathsf{' + _c + '}'
    for _i, _c in enumerate(lo):
        _LETTERS[chr(0x1D5BA + _i)] = r'\mathsf{' + _c + '}'
    # Mathematical monospace: capital U+1D670, small U+1D68A
    for _i, _c in enumerate(up):
        _LETTERS[chr(0x1D670 + _i)] = r'\mathtt{' + _c + '}'
    for _i, _c in enumerate(lo):
        _LETTERS[chr(0x1D68A + _i)] = r'\mathtt{' + _c + '}'


_add_math_alphanumeric()

# Operators whose under/over scripts should use _/^ (limit position).
_LIMIT_OPS = {
    r'\sum',
    r'\prod',
    r'\coprod',
    r'\int',
    r'\iint',
    r'\iiint',
    r'\oint',
    r'\bigcup',
    r'\bigcap',
    r'\bigvee',
    r'\bigwedge',
    r'\bigoplus',
    r'\bigotimes',
    r'\lim',
    r'\limsup',
    r'\liminf',
    r'\sup',
    r'\inf',
    r'\max',
    r'\min',
    r'\det',
    r'\gcd',
}

# mover accents: the over-element text → LaTeX accent command.
_ACCENTS = {
    '^': r'\hat',
    'ˆ': r'\hat',
    '~': r'\tilde',
    '˜': r'\tilde',
    '‾': r'\overline',
    '¯': r'\overline',
    '→': r'\vec',
    '˙': r'\dot',
    '⋅': r'\dot',
    '¨': r'\ddot',
    '˘': r'\breve',
    '˚': r'\mathring',
    'ˇ': r'\check',
}

# Extensible arrows: base LaTeX command → \x...arrow (amsmath).
# When an arrow is the base of mover/munder/munderover, use the extensible
# form so the label stretches the arrow rather than sitting above a fixed glyph.
_XARROWS = {
    r'\rightarrow': r'\xrightarrow',
    r'\leftarrow': r'\xleftarrow',
}

# mfenced open/close bracket maps — kept separate to avoid duplicate-key collisions.
_FENCES_OPEN = {
    '(': r'\left(',
    '[': r'\left[',
    '{': r'\left\{',
    '|': r'\left|',
    '‖': r'\left\|',
    '⟨': r'\left\langle',
    '⌈': r'\left\lceil',
    '⌊': r'\left\lfloor',
    '': '',
}
_FENCES_CLOSE = {
    ')': r'\right)',
    ']': r'\right]',
    '}': r'\right\}',
    '|': r'\right|',
    '‖': r'\right\|',
    '⟩': r'\right\rangle',
    '⌉': r'\right\rceil',
    '⌋': r'\right\rfloor',
    '': '',
}

# mfenced + single mtable → named amsmath matrix environment.
# Keys are (open_char, close_char) pairs.
_MATRIX_ENV = {
    ('(', ')'): 'pmatrix',
    ('[', ']'): 'bmatrix',
    ('{', '}'): 'Bmatrix',
    ('|', '|'): 'vmatrix',
    ('‖', '‖'): 'Vmatrix',
}

# Unambiguous stretchy fence chars for mo elements.
# | and ‖ are excluded: they appear in both _FENCES_OPEN and _FENCES_CLOSE, so
# the mo handler cannot tell which side it is on without parent context.
_MO_STRETCHY_OPEN = frozenset(_FENCES_OPEN) - {'', '|', '‖'}
_MO_STRETCHY_CLOSE = frozenset(_FENCES_CLOSE) - {'', '|', '‖'}

# mathvariant attribute → LaTeX font command (None = default/no-op).
_MATHVARIANT = {
    'normal': r'\mathrm',
    'bold': r'\mathbf',
    'italic': None,  # default for mi; no-op
    'bold-italic': r'\boldsymbol',
    'script': r'\mathcal',
    'bold-script': r'\mathcal',
    'fraktur': r'\mathfrak',
    'bold-fraktur': r'\mathfrak',
    'double-struck': r'\mathbb',
    'sans-serif': r'\mathsf',
    'bold-sans-serif': r'\mathbf',
    'sans-serif-italic': r'\mathsf',
    'sans-serif-bold-italic': r'\mathbf',
    'monospace': r'\mathtt',
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _retarget_array(inner: str, env: str) -> str:
    r"""Rewrite ``\begin{array}{…}…\end{array}`` blocks to use ``env`` instead."""
    return _re.sub(
        r'\\begin\{array\}\{[^}]*\}(.*?)\\end\{array\}',
        lambda m: r'\begin{' + env + r'}' + m.group(1) + r'\end{' + env + r'}',
        inner,
        flags=_re.DOTALL,
    )


def _mml_tag(elem: ET.Element) -> str:
    tag = elem.tag
    return tag.split('}', 1)[1] if '}' in tag else tag


_CTRL_WORD_END = _re.compile(r'\\[A-Za-z]+$')


def _join(parts: Iterable[str]) -> str:
    r"""Concatenate LaTeX fragments, inserting '{}' between control words.

    Prevents a control word from absorbing the first letter of the next
    fragment. E.g. ['\in', 'S'] → '\in{}S', not '\inS'.
    """
    result = ''
    for part in parts:
        if result and _CTRL_WORD_END.search(result) and part and part[0].isalpha():
            result += '{}'
        result += part
    return result


def _is_single_brace_group(s: str) -> bool:
    r"""Return True if s is exactly one {…} group whose braces balance at the end.

    Needed because a string like '{}_{T}^{H}Y' starts with '{' and ends with '}'
    (from \mathrm{Y}) but is NOT a single group — misidentifying it causes
    double-script errors in nested mmultiscripts.
    """
    if not (s.startswith('{') and s.endswith('}')):
        return False
    depth = 0
    for i, ch in enumerate(s):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return i == len(s) - 1
    return False


def _brace(s: str) -> str:
    r"""Wrap s in LaTeX braces for use as a script argument (^, _).

    Only bare single ASCII characters are left unbraced (x^2, x_i).
    Everything else — multi-char strings, LaTeX commands — gets braces so that
    adjacent letters can't accidentally extend the command name (e.g. ^\inftyf
    would tokenise as the undefined \inftyf).
    """
    if not s:
        return '{}'
    if _is_single_brace_group(s):
        return s
    if len(s) == 1 and (s.isalnum() or s in '+-=<>|'):
        return s
    return '{' + s + '}'


def _brace_arg(s: str) -> str:
    r"""Always wrap in braces — for \frac, \sqrt, \binom, etc. arguments."""
    if not s:
        return '{}'
    if _is_single_brace_group(s):
        return s
    return '{' + s + '}'


def _has_bare(s: str, char: str) -> bool:
    """Return True if s contains `char` at brace-depth 0."""
    depth = 0
    for ch in s:
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
        elif ch == char and depth == 0:
            return True
    return False


def _script_base(s: str, script_char: str) -> str:
    """Wrap base in braces to avoid a double-script error.

    Only when adding `script_char` would create one (e.g. x_i + _j →
    {x_i}_j, but x_i + ^2 is fine).
    """
    if _has_bare(s, script_char):
        return '{' + s + '}'
    return s


def _children(elem: ET.Element) -> list[ET.Element]:
    """Direct child elements, skipping any non-MathML nodes."""
    return list(elem)


# Characters that need special treatment when they appear inside \text{}.
# Maps a char → its LaTeX equivalent (emitted outside the \text{} group).
_TEXT_ESCAPES_MATH = {
    # These are emitted as math-mode commands, not inside \text{}
    '\u2003': r'\quad',
    '\u2002': r'\;',
    '\u2004': r'\;',
    '\u2005': r'\;',
    '\u2009': r'\,',
    '\u200a': r'\,',
    '\u205f': r'\:',
    '\u00a0': r'\ ',
    '\u2195': r'\updownarrow',
    '\u21d1': r'\Uparrow',
    '\u21d3': r'\Downarrow',
    '\u21d5': r'\Updownarrow',
    '\u0332': r'\_',
    '\u2032': r"'",
    '\u2033': r"''",
    '\u23df': r'\underbrace{}',
    '\u23de': r'\overbrace{}',
    '\u200b': '',  # ZERO WIDTH SPACE — discard
    '\u212b': r'\text{\AA}',  # ANGSTROM SIGN
    '\u2329': r'\langle',  # CJK LEFT ANGLE BRACKET
    '\u232a': r'\rangle',  # CJK RIGHT ANGLE BRACKET
    # Typographic quotation marks — approximate as standard LaTeX punctuation
    '\u2018': r"'",
    '\u2019': r"'",  # LEFT/RIGHT SINGLE QUOTATION MARK
    '\u201a': r',',
    '\u201b': r"'",  # SINGLE LOW-9 / HIGH-REVERSED-9
    '\u201c': r'``',
    '\u201d': r"''",  # LEFT/RIGHT DOUBLE QUOTATION MARK
    '\u201e': r',,',
    '\u201f': r"''",  # DOUBLE LOW-9 / HIGH-REVERSED-9
    '\u00b4': r"'",  # ACUTE ACCENT → prime
}


def _build_text(raw: str) -> str:
    r"""Convert a raw string into safe LaTeX for use inside a math expression.

    ASCII 'special' chars (_, ^, {, }) are escaped for text mode.
    Non-ASCII chars that have known LaTeX equivalents are emitted as math-mode
    fragments; Greek / other mapped chars are likewise substituted.
    Everything else is enclosed in \text{…}.
    """
    parts: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            # Escape LaTeX text-mode specials before wrapping in \text{}
            s = ''.join(buf)
            s = (
                s.replace('\\', r'\textbackslash{}')
                .replace('{', r'\{')
                .replace('}', r'\}')
                .replace('_', r'\_')
                .replace('^', r'\^{}')
                .replace('#', r'\#')
                .replace('$', r'\$')
            )
            parts.append(r'\text{' + s + '}')
            buf.clear()

    for ch in raw:
        if ch in _TEXT_ESCAPES_MATH:
            flush()
            parts.append(_TEXT_ESCAPES_MATH[ch])
        elif ch in _LETTERS:
            flush()
            parts.append(_LETTERS[ch])
        elif ch in _MO:
            flush()
            parts.append(_MO[ch])
        elif ord(ch) <= 127:
            buf.append(ch)
        # else: skip non-ASCII chars not in any map — passing them through
        # raw would trigger pdflatex encoding errors.

    flush()
    return ''.join(parts) if parts else r'\text{}'


def _join_kids(kids: list) -> str:
    r"""Convert and join child elements, with one special case.

    When a FUNCTION APPLICATION operator (U+2061) follows a sibling whose
    LaTeX is plain undecorated ASCII text, promote that text to
    \operatorname{} so it renders upright with correct operator spacing.
    Known function names (already mapped to \ln etc.) are left alone.
    """
    parts: list[str] = []
    for child in kids:
        tex = mml_to_tex(child)
        if _mml_tag(child) == 'mo' and ''.join(child.itertext()).strip() == '\u2061' and parts:
            prev = parts[-1]
            # Only promote multi-char plain text — single chars are variables,
            # and anything starting with \ is already a proper command.
            if len(prev) > 1 and prev.isalpha() and not prev.startswith('\\'):
                parts[-1] = r'\operatorname{' + prev + '}'
                # \operatorname{...} already ends with }, no extra separator needed.
            elif prev.startswith('\\') and prev[-1].isalpha():
                # LaTeX command ending with a letter (e.g. \ln, \sin): append
                # {} so the next token doesn't merge into the command name.
                parts.append('{}')
        else:
            parts.append(tex)
    return _join(parts)


# ---------------------------------------------------------------------------
# Main converter
# ---------------------------------------------------------------------------


def mml_to_tex(elem: ET.Element) -> str:  # noqa: C901, PLR0911, PLR0912, PLR0915
    """Recursively convert a MathML element to a LaTeX string."""
    tag = _mml_tag(elem)
    kids = _children(elem)

    # ------------------------------------------------------------------ tokens
    if tag == 'mglyph':
        # Image glyph: use alt text as a text-mode approximation.
        alt = elem.get('alt', '')
        return _build_text(alt) if alt else ''

    if tag == 'mi':
        val = ''.join(elem.itertext()).strip()
        # If mi contains child elements (e.g. mglyph) with no text content,
        # delegate to child converters instead of returning empty string.
        if not val and kids:
            return _join(mml_to_tex(c) for c in kids)
        tex = _LETTERS.get(val, val)
        variant = elem.get('mathvariant', '')
        cmd = _MATHVARIANT.get(variant) if variant else None
        # Only wrap plain text/letters — don't try to font-switch a \command.
        if cmd and tex and not tex.startswith('\\'):
            # Content with LaTeX special chars or non-ASCII → text mode.
            if any(c in tex for c in '_^{}\\') or any(ord(c) > 127 for c in tex):
                return _build_text(tex)
            # Spaces in math-mode font commands are silently dropped by TeX;
            # escape them as explicit spaces.
            tex = cmd + '{' + tex.replace(' ', r'\ ') + '}'
        elif any(ord(c) > 127 for c in tex) and not tex.startswith('\\'):
            return _build_text(tex)
        elif ' ' in tex and not tex.startswith('\\'):
            # Plain mi with spaces (no mathvariant): wrap in \text{} so spaces render.
            return _build_text(tex)
        return tex

    if tag == 'mn':
        val = ''.join(elem.itertext()).strip()
        if not val and kids:
            return _join(mml_to_tex(c) for c in kids)
        # mn can contain Greek letters or special chars in some test suites
        tex = _LETTERS.get(val, val)
        variant = elem.get('mathvariant', '')
        cmd = _MATHVARIANT.get(variant) if variant else None
        if cmd and tex and not tex.startswith('\\'):
            return cmd + '{' + tex.replace(' ', r'\ ') + '}'
        if any(ord(c) > 127 for c in tex) and not tex.startswith('\\'):
            return _build_text(tex)
        return tex

    if tag == 'mo':
        val = ''.join(elem.itertext()).strip()
        # Bracket characters are stretchy by default per the MathML operator
        # dictionary.  Emit \left/\right unless stretchy="false" explicitly
        # suppresses it.  | and ‖ are ambiguous (same char open and close) so
        # they fall through to the plain _MO lookup.
        if elem.get('stretchy', '') != 'false':
            if val in _MO_STRETCHY_OPEN:
                return _FENCES_OPEN[val]
            if val in _MO_STRETCHY_CLOSE:
                return _FENCES_CLOSE[val]
        tex = _MO.get(val, _LETTERS.get(val, val))
        if any(ord(c) > 127 for c in tex) and not tex.startswith('\\'):
            return _build_text(tex)
        return tex

    if tag == 'mtext':
        return _build_text(''.join(elem.itertext()))

    if tag == 'mspace':
        if elem.get('linebreak') in ('newline', 'indentingnewline'):
            return r'\\'
        width = elem.get('width', '')
        if 'em' in width:
            try:
                w = float(width.replace('em', ''))
                if w <= 0.17:
                    return r'\,'
                if w <= 0.22:
                    return r'\:'
                if w <= 0.28:
                    return r'\;'
                return r'\quad' if w <= 1.0 else r'\qquad'
            except ValueError:
                pass
        return r'\,'

    if tag == 'ms':
        val = ''.join(elem.itertext())
        lq = elem.get('lquote', '"')
        rq = elem.get('rquote', '"')
        return _build_text(lq + val + rq)

    # --------------------------------------------------------------- grouping
    if tag in ('mrow', 'math', 'mstyle', 'merror', 'mpadded', 'mtd'):
        # Detect mo_open + mtable + mo_close subsequences and emit named
        # matrix environments (pmatrix, bmatrix, etc.) rather than
        # \left..\begin{array}..\end{array}..\right.
        non_ws = [c for c in kids if _mml_tag(c) is not None]
        if any(_mml_tag(c) == 'mtable' for c in non_ws):
            parts: list[str] = []
            i = 0
            while i < len(non_ws):
                c = non_ws[i]
                if (
                    i + 2 < len(non_ws)
                    and _mml_tag(c) == 'mo'
                    and _mml_tag(non_ws[i + 1]) == 'mtable'
                    and _mml_tag(non_ws[i + 2]) == 'mo'
                ):
                    open_ch = ''.join(c.itertext()).strip()
                    close_ch = ''.join(non_ws[i + 2].itertext()).strip()
                    env = _MATRIX_ENV.get((open_ch, close_ch))
                    if env is not None:
                        inner = mml_to_tex(non_ws[i + 1])
                        inner = _retarget_array(inner, env)
                        parts.append(inner)
                        i += 3
                        continue
                parts.append(mml_to_tex(c))
                i += 1
            if len(parts) != len(non_ws):
                # At least one substitution happened
                result = _join(p for p in parts)
                if tag == 'math' and not result:
                    return '{}'
                return result
        result = _join_kids(kids)
        # An empty top-level math element must still produce valid LaTeX;
        # $$ (two bare dollar signs) opens display math and never closes.
        if tag == 'math' and not result:
            return '{}'
        return result

    if tag == 'semantics':
        # First child is the Presentation MathML; the rest are annotations.
        # Skip <annotation> and <annotation-xml> children entirely.
        pres = [c for c in kids if _mml_tag(c) not in ('annotation', 'annotation-xml')]
        return _join(mml_to_tex(c) for c in pres) if pres else _join(mml_to_tex(c) for c in kids[:1])

    if tag == 'mphantom':
        return r'\phantom{' + ''.join(mml_to_tex(c) for c in kids) + '}'

    if tag == 'menclose':
        inner = _join(mml_to_tex(c) for c in kids)
        notation = elem.get('notation', 'longdiv')
        # A single element can have multiple space-separated notations; apply
        # them inside-out.
        applied = inner
        for note in reversed(notation.split()):
            if note in ('box', 'roundedbox'):
                applied = r'\boxed{' + applied + '}'
            elif note in ('top', 'overline'):
                applied = r'\overline{' + applied + '}'
            elif note in ('bottom', 'underline'):
                applied = r'\underline{' + applied + '}'
            elif note == 'radical':
                applied = r'\sqrt{' + applied + '}'
            elif note in ('updiagonalstrike', 'downdiagonalstrike', 'verticalstrike', 'horizontalstrike', 'longdiv'):
                applied = r'\cancel{' + applied + '}'  # needs cancel pkg
            elif note == 'actuarial':
                applied = r'\overline{' + applied + '}'
            elif note == 'circle' and not applied.startswith(r'\boxed{'):
                # No standard single-command encircle in LaTeX; \boxed is the
                # closest approximation that still draws a visible border.
                # Skip when "box" is also present to avoid double-boxing.
                applied = r'\boxed{' + applied + '}'
            # unknown notations: pass through unchanged
        return applied

    if tag == 'maction':
        # Static rendering: use the first (selected) child.
        return mml_to_tex(kids[0]) if kids else ''

    # --------------------------------------------------------- fractions/roots
    if tag == 'mfrac':
        if len(kids) != 2:
            return ''.join(mml_to_tex(c) for c in kids)
        num = _brace_arg(mml_to_tex(kids[0]))
        den = _brace_arg(mml_to_tex(kids[1]))
        # linethickness="0": stacked without a fraction bar or delimiters.
        # Use \genfrac with empty delimiters rather than \binom, which adds
        # parentheses.  If this mfrac is inside mfenced with ( ), those
        # delimiters come from the mfenced handler and produce the same result.
        if elem.get('linethickness') == '0':
            return r'\genfrac{}{}{0pt}{}' + num + den
        # bevelled="true" → slanted fraction  a/b
        if elem.get('bevelled') == 'true':
            return mml_to_tex(kids[0]) + '/' + mml_to_tex(kids[1])
        return r'\frac' + num + den

    if tag == 'msqrt':
        return r'\sqrt{' + ''.join(mml_to_tex(c) for c in kids) + '}'

    if tag == 'mroot':
        if len(kids) != 2:
            return r'\sqrt{' + ''.join(mml_to_tex(c) for c in kids) + '}'
        base = mml_to_tex(kids[0])
        index = mml_to_tex(kids[1])
        return r'\sqrt[' + index + ']{' + base + '}'

    # ---------------------------------------------------- scripts / limits
    if tag == 'msup':
        if len(kids) != 2:
            return ''.join(mml_to_tex(c) for c in kids)
        base = _script_base(mml_to_tex(kids[0]), '^')
        exp = _brace(mml_to_tex(kids[1]))
        return base + '^' + exp

    if tag == 'msub':
        if len(kids) != 2:
            return ''.join(mml_to_tex(c) for c in kids)
        base = _script_base(mml_to_tex(kids[0]), '_')
        sub = _brace(mml_to_tex(kids[1]))
        return base + '_' + sub

    if tag == 'msubsup':
        if len(kids) != 3:
            return ''.join(mml_to_tex(c) for c in kids)
        # msubsup adds both; wrap if either would collide.
        raw_base = mml_to_tex(kids[0])
        base = _script_base(_script_base(raw_base, '_'), '^')
        sub = _brace(mml_to_tex(kids[1]))
        sup = _brace(mml_to_tex(kids[2]))
        return base + '_' + sub + '^' + sup

    if tag == 'munder':
        if len(kids) != 2:
            return ''.join(mml_to_tex(c) for c in kids)
        base_tex = mml_to_tex(kids[0])
        under_unicode = ''.join(kids[1].itertext()).strip()
        under_tex = mml_to_tex(kids[1])
        # Bottom curly bracket → \underbrace
        if under_unicode == '\u23df':
            return r'\underbrace{' + base_tex + '}'
        # Underscore / combining low line used as underline decoration
        if under_unicode in ('_', '\u0332'):
            return r'\underline{' + base_tex + '}'
        if base_tex in _LIMIT_OPS:
            return base_tex + '_' + _brace(under_tex)
        xarrow = _XARROWS.get(base_tex)
        if xarrow:
            return xarrow + '[' + under_tex + ']{}'
        return r'\underset' + _brace_arg(under_tex) + _brace_arg(base_tex)

    if tag == 'mover':
        if len(kids) != 2:
            return ''.join(mml_to_tex(c) for c in kids)
        base_tex = mml_to_tex(kids[0])
        # Check the raw Unicode text first (before operator substitution),
        # then the converted form, so that e.g. → matches \vec via either path.
        over_unicode = ''.join(kids[1].itertext()).strip()
        over_tex = mml_to_tex(kids[1])
        # Top curly bracket → \overbrace
        if over_unicode == '\u23de':
            return r'\overbrace{' + base_tex + '}'
        acc = _ACCENTS.get(over_unicode) or _ACCENTS.get(over_tex)
        if acc:
            return acc + '{' + base_tex + '}'
        if base_tex in _LIMIT_OPS:
            return base_tex + '^' + _brace(over_tex)
        xarrow = _XARROWS.get(base_tex)
        if xarrow:
            return xarrow + '{' + over_tex + '}'
        return r'\overset' + _brace_arg(over_tex) + _brace_arg(base_tex)

    if tag == 'munderover':
        if len(kids) != 3:
            return ''.join(mml_to_tex(c) for c in kids)
        raw_base = mml_to_tex(kids[0])
        under_tex = mml_to_tex(kids[1])
        over_tex = mml_to_tex(kids[2])
        if raw_base in _LIMIT_OPS:
            base = _script_base(_script_base(raw_base, '_'), '^')
            return base + '_' + _brace(under_tex) + '^' + _brace(over_tex)
        xarrow = _XARROWS.get(raw_base)
        if xarrow:
            return xarrow + '[' + under_tex + ']{' + over_tex + '}'
        # Non-limit base: use \overset/\underset so over/under stack directly
        # above/below regardless of display mode.
        return (
            r'\overset' + _brace_arg(over_tex) + _brace_arg(r'\underset' + _brace_arg(under_tex) + _brace_arg(raw_base))
        )

    if tag == 'mmultiscripts':
        # Structure: base (sub sup)* <mprescripts/> (presub presup)*
        # Prescripts are rendered as {}_{sub}^{sup} placed before the base —
        # the empty-group idiom works in pdflatex, MathJax, and KaTeX without
        # any extra packages.
        if not kids:
            return ''
        base = mml_to_tex(kids[0])
        post: list[str] = []  # post-scripts  (attach to base on the right)
        pre: list[str] = []  # pre-scripts   (attach to {} on the left)
        prescripts = False
        i = 1
        while i < len(kids):
            if _mml_tag(kids[i]) == 'mprescripts':
                prescripts = True
                i += 1
                continue
            sub_tex = mml_to_tex(kids[i])
            sup_tex = mml_to_tex(kids[i + 1]) if i + 1 < len(kids) else ''
            none_sub = not sub_tex or sub_tex == 'none'
            none_sup = not sup_tex or sup_tex == 'none'
            if not prescripts:
                # Each additional (sub, sup) pair must go on a fresh {} atom;
                # multiple _/^ on the same atom give "Double subscript" errors.
                has_script = not none_sub or not none_sup
                if has_script and post:
                    post.append('{}')
                if not none_sub:
                    post.append('_' + _brace(sub_tex))
                if not none_sup:
                    post.append('^' + _brace(sup_tex))
            else:
                # Build {}_{sub}^{sup} prefix, omitting absent scripts.
                pre_part = '{}'
                if not none_sub:
                    pre_part += '_' + _brace(sub_tex)
                if not none_sup:
                    pre_part += '^' + _brace(sup_tex)
                pre.append(pre_part)
            i += 2
        return ''.join(pre) + base + ''.join(post)

    # --------------------------------------------------------- fenced / table
    if tag == 'mfenced':
        open_ch = elem.get('open', '(')
        close_ch = elem.get('close', ')')
        seps = elem.get('separators', ',')

        # When the single child is an mtable (possibly wrapped in mrow), use a
        # named amsmath matrix environment instead of \left..\right.
        non_whitespace_kids = [c for c in kids if _mml_tag(c) is not None]
        _sole = non_whitespace_kids[0] if len(non_whitespace_kids) == 1 else None
        # Unwrap a single-child mrow
        if _sole is not None and _mml_tag(_sole) == 'mrow':
            _mrow_kids = [c for c in _sole if _mml_tag(c) is not None]
            if len(_mrow_kids) == 1:
                _sole = _mrow_kids[0]
        if _sole is not None and _mml_tag(_sole) == 'mtable':
            env = _MATRIX_ENV.get((open_ch, close_ch))
            if env is not None:
                inner = mml_to_tex(_sole)
                # Strip the \begin{array}{...} wrapper; replace with named env.
                inner = _re.sub(
                    r'\\begin\{array\}\{[^}]*\}(.*?)\\end\{array\}',
                    lambda m: r'\begin{' + env + r'}' + m.group(1) + r'\end{' + env + r'}',
                    inner,
                    flags=_re.DOTALL,
                )
                if r'\begin{' + env + r'}' in inner:
                    return inner

        left = _FENCES_OPEN.get(open_ch, r'\left' + (open_ch or '.'))
        right = _FENCES_CLOSE.get(close_ch, r'\right' + (close_ch or '.'))

        # \left and \right must be paired.  If one side has no delimiter (empty
        # string from the map), don't generate a bare \right/\left on the other.
        if left == '' and right.startswith(r'\right'):
            right = close_ch or ''
        elif right == '' and left.startswith(r'\left'):
            left = open_ch or ''

        # The separators attribute is a sequence of chars (whitespace ignored).
        # Per MathML spec: use sep[i] for gap i; if exhausted, repeat the last.
        sep_list = [c for c in seps if not c.isspace()] or [',']
        child_texs = [mml_to_tex(c) for c in kids]
        inner_parts: list[str] = []
        for i, t in enumerate(child_texs):
            if i > 0:
                sc = sep_list[min(i - 1, len(sep_list) - 1)]
                inner_parts.append(_MO.get(sc, sc) + ' ')
            inner_parts.append(t)
        inner = ''.join(inner_parts)
        return left + inner + right

    if tag == 'mlabeledtr':
        # First child is the equation label (displayed outside the table);
        # remaining children are the data cells — treat them like mtr cells.
        data_cells = [c for c in kids if _mml_tag(c) == 'mtd'][1:]
        return ' & '.join(mml_to_tex(td) for td in data_cells)

    if tag == 'mtable':
        rows = []
        for tr in kids:
            tr_tag = _mml_tag(tr)
            if tr_tag == 'mtr':
                cells = [mml_to_tex(td) for td in tr if _mml_tag(td) == 'mtd']
                rows.append(cells)
            elif tr_tag == 'mlabeledtr':
                rows.append(mml_to_tex(tr).split(' & '))

        ncols = max((len(r) for r in rows), default=1)
        # Pad each row to ncols so \begin{array} columns stay aligned
        padded = [' & '.join(r + [''] * (ncols - len(r))) for r in rows]

        # Build column spec from columnalign attribute (space-separated words).
        # Map "left"/"right"/"center" to "l"/"r"/"c"; default is "c".
        align_map = {'left': 'l', 'right': 'r', 'center': 'c'}
        calign = elem.get('columnalign', '')
        align_words = calign.split() if calign else []
        col_spec = (
            ''.join(align_map.get(align_words[i] if i < len(align_words) else '', 'c') for i in range(ncols))
            or 'c' * ncols
        )

        body = ' \\\\\n'.join(padded)
        return r'\begin{array}{' + col_spec + '}\n' + body + '\n' + r'\end{array}'

    # ---------------------------------------------------------------- fallback
    # Unknown element (e.g. Content MathML): concatenate all descendant text,
    # collapsing whitespace so blank lines don't break out of $…$ math mode.
    return _re.sub(r'\s+', ' ', ''.join(elem.itertext())).strip()


def render_mathml(elem: ET.Element, display: bool = False) -> str:
    """Convert a MathML root element to a Markdown-embeddable LaTeX string."""
    latex = mml_to_tex(elem).strip()
    if not latex:
        return ''
    return '$$\n' + latex + '\n$$' if display else '$' + latex + '$'
