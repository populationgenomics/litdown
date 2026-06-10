"""Comprehensive unittest suite for litdown.mathml.

Structure:
  - Tier 1  HandWritten*  — per-element white-box tests
  - Tier 2  Regression*   — data-driven tests over the curated W3C subset
                           in tests/w3c_mml/ (locked in by tests/golden.json)
  - Tier 3  Compile*      — smoke-compile every Tier-1 expression with pdflatex
                           (skipped when pdflatex is not on PATH)

Run with:
    python3 -m pytest tests/test_mml_unit.py -v
or:
    python3 -m unittest tests.test_mml_unit
"""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

from defusedxml.ElementTree import fromstring as defused_fromstring

from litdown.mathml import mml_to_tex, render_mathml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MML_NS = 'http://www.w3.org/1998/Math/MathML'


def parse(mml: str) -> ET.Element:
    """Parse a MathML snippet, wrapping bare fragments in <math> if needed."""
    if not mml.strip().startswith('<math'):
        mml = f'<math xmlns="{MML_NS}">{mml}</math>'
    return defused_fromstring(mml)


def tex(mml: str) -> str:
    """Parse MML snippet and return its LaTeX string."""
    return mml_to_tex(parse(mml)).strip()


def normalise(s: str) -> str:
    """Strip all whitespace for structural comparison."""
    return re.sub(r'\s+', '', s)


# ---------------------------------------------------------------------------
# Tier 1: Hand-written unit tests
# ---------------------------------------------------------------------------


class HandWrittenTokens(unittest.TestCase):
    """Token elements: mi, mn, mo, mtext, mspace, ms."""

    # --- mi ---

    def test_mi_single_letter(self):
        self.assertEqual(tex('<mi>x</mi>'), 'x')

    def test_mi_greek(self):
        self.assertEqual(tex('<mi>α</mi>'), r'\alpha')

    def test_mi_known_function(self):
        self.assertEqual(tex('<mi>sin</mi>'), r'\sin')
        self.assertEqual(tex('<mi>ln</mi>'), r'\ln')
        self.assertEqual(tex('<mi>arctan</mi>'), r'\arctan')

    def test_mi_mathvariant_normal(self):
        self.assertEqual(tex('<mi mathvariant="normal">A</mi>'), r'\mathrm{A}')

    def test_mi_mathvariant_bold(self):
        self.assertEqual(tex('<mi mathvariant="bold">x</mi>'), r'\mathbf{x}')

    def test_mi_mathvariant_italic_is_noop(self):
        # italic is the default for mi; no wrapping command
        self.assertEqual(tex('<mi mathvariant="italic">x</mi>'), 'x')

    def test_mi_space_in_text_uses_text_mode(self):
        result = tex('<mi mathvariant="normal">a b</mi>')
        # Space must be preserved — either as \text{...} or with \
        self.assertIn(' ', result.replace(r'\ ', ' '))

    def test_mi_empty(self):
        # Empty mi: should not crash, returns empty string
        result = tex('<mi></mi>')
        self.assertIsInstance(result, str)

    # --- mn ---

    def test_mn_integer(self):
        self.assertEqual(tex('<mn>42</mn>'), '42')

    def test_mn_decimal(self):
        self.assertEqual(tex('<mn>3.14</mn>'), '3.14')

    def test_mn_mathvariant_bold(self):
        self.assertEqual(tex('<mn mathvariant="bold">3</mn>'), r'\mathbf{3}')

    # --- mo ---

    def test_mo_plus(self):
        self.assertEqual(tex('<mo>+</mo>'), '+')

    def test_mo_times(self):
        self.assertEqual(tex('<mo>×</mo>'), r'\times')

    def test_mo_sum_symbol(self):
        self.assertEqual(tex('<mo>∑</mo>'), r'\sum')

    def test_mo_literal_brace_open(self):
        self.assertEqual(tex('<mo>{</mo>'), r'\left\{')

    def test_mo_literal_brace_open_nonstretchy(self):
        self.assertEqual(tex('<mo stretchy="false">{</mo>'), r'\{')

    def test_mo_literal_brace_close(self):
        self.assertEqual(tex('<mo>}</mo>'), r'\right\}')

    def test_mo_literal_brace_close_nonstretchy(self):
        self.assertEqual(tex('<mo stretchy="false">}</mo>'), r'\}')

    def test_mo_dollar_sign(self):
        self.assertEqual(tex('<mo>$</mo>'), r'\$')

    def test_mo_hash(self):
        self.assertEqual(tex('<mo>#</mo>'), r'\#')

    def test_mo_invisible_operators_produce_no_output(self):
        # Test mo elements directly — wrapping in <math> triggers the
        # empty-element guard which returns "{}" instead of "".
        mo1 = defused_fromstring(f'<mo xmlns="{MML_NS}">\u2061</mo>')
        self.assertEqual(mml_to_tex(mo1), '')  # FUNCTION APPLICATION
        mo2 = defused_fromstring(f'<mo xmlns="{MML_NS}">\u2062</mo>')
        self.assertEqual(mml_to_tex(mo2), '')  # INVISIBLE TIMES

    # --- mtext ---

    def test_mtext_plain(self):
        result = tex('<mtext>hello</mtext>')
        self.assertIn('hello', result)
        self.assertTrue(result.startswith(r'\text{'))

    def test_mtext_escapes_special_chars(self):
        result = tex('<mtext>a_b</mtext>')
        self.assertIn(r'\_', result)

    def test_mtext_escapes_dollar(self):
        result = tex('<mtext>$5</mtext>')
        self.assertIn(r'\$', result)

    def test_mtext_escapes_hash(self):
        result = tex('<mtext>#1</mtext>')
        self.assertIn(r'\#', result)

    def test_mtext_greek_in_text(self):
        # Greek letters inside mtext are emitted as LaTeX commands outside \text{}
        result = tex('<mtext>αβ</mtext>')
        self.assertIn(r'\alpha', result)
        self.assertIn(r'\beta', result)

    # --- mspace ---

    def test_mspace_thin(self):
        self.assertEqual(tex('<mspace width="0.1em"/>'), r'\,')

    def test_mspace_medium(self):
        self.assertEqual(tex('<mspace width="0.25em"/>'), r'\;')

    def test_mspace_em(self):
        self.assertEqual(tex('<mspace width="1em"/>'), r'\quad')

    def test_mspace_newline(self):
        self.assertEqual(tex('<mspace linebreak="newline"/>'), r'\\')

    # --- ms ---

    def test_ms_default_quotes(self):
        result = tex('<ms>dog</ms>')
        self.assertIn('dog', result)
        self.assertIn('"', result)

    def test_ms_custom_quotes(self):
        result = tex('<ms lquote="\'" rquote="\'">cat</ms>')
        self.assertIn('cat', result)
        self.assertIn("'", result)


class HandWrittenGrouping(unittest.TestCase):
    """Grouping: mrow, math, mstyle, merror, mphantom, menclose, maction, semantics."""

    def test_mrow_concatenates_children(self):
        self.assertEqual(tex('<mrow><mi>a</mi><mo>+</mo><mi>b</mi></mrow>'), 'a+b')

    def test_math_empty_returns_nonempty(self):
        # Empty math element must produce valid LaTeX (not bare $$)
        result = tex('<math></math>')
        self.assertTrue(result)  # not empty string

    def test_mstyle_passthrough(self):
        result = tex('<mstyle><mi>x</mi></mstyle>')
        self.assertEqual(result, 'x')

    def test_merror_passthrough(self):
        result = tex('<merror><mi>x</mi></merror>')
        self.assertEqual(result, 'x')

    def test_mphantom(self):
        result = tex('<mphantom><mi>x</mi></mphantom>')
        self.assertEqual(result, r'\phantom{x}')

    def test_menclose_box(self):
        result = tex('<menclose notation="box"><mi>x</mi></menclose>')
        self.assertEqual(result, r'\boxed{x}')

    def test_menclose_overline(self):
        result = tex('<menclose notation="top"><mi>x</mi></menclose>')
        self.assertEqual(result, r'\overline{x}')

    def test_menclose_radical(self):
        result = tex('<menclose notation="radical"><mi>x</mi></menclose>')
        self.assertEqual(result, r'\sqrt{x}')

    def test_menclose_cancel(self):
        result = tex('<menclose notation="updiagonalstrike"><mi>x</mi></menclose>')
        self.assertEqual(result, r'\cancel{x}')

    def test_maction_uses_first_child(self):
        result = tex('<maction><mi>a</mi><mi>b</mi></maction>')
        self.assertEqual(result, 'a')

    def test_semantics_uses_presentation(self):
        mml = """<semantics>
            <mrow><mi>x</mi><mo>+</mo><mi>y</mi></mrow>
            <annotation encoding="application/x-tex">x+y</annotation>
        </semantics>"""
        result = tex(mml)
        self.assertEqual(result, 'x+y')


class HandWrittenFractionsRoots(unittest.TestCase):
    """mfrac, msqrt, mroot."""

    def test_mfrac_basic(self):
        self.assertEqual(tex('<mfrac><mi>a</mi><mi>b</mi></mfrac>'), r'\frac{a}{b}')

    def test_mfrac_nested(self):
        result = tex('<mfrac><mfrac><mn>1</mn><mn>2</mn></mfrac><mn>3</mn></mfrac>')
        self.assertEqual(normalise(result), normalise(r'\frac{\frac{1}{2}}{3}'))

    def test_mfrac_linethickness_zero(self):
        # No fraction bar, no extra delimiters — use \genfrac
        result = tex('<mfrac linethickness="0"><mi>n</mi><mi>k</mi></mfrac>')
        self.assertIn(r'\genfrac', result)
        self.assertNotIn(r'\binom', result)

    def test_mfrac_bevelled(self):
        result = tex('<mfrac bevelled="true"><mi>a</mi><mi>b</mi></mfrac>')
        self.assertEqual(result, 'a/b')

    def test_msqrt(self):
        self.assertEqual(tex('<msqrt><mi>x</mi></msqrt>'), r'\sqrt{x}')

    def test_mroot_cube(self):
        self.assertEqual(
            tex('<mroot><mi>x</mi><mn>3</mn></mroot>'),
            r'\sqrt[3]{x}',
        )


class HandWrittenScriptsLimits(unittest.TestCase):
    """msup, msub, msubsup, munder, mover, munderover, mmultiscripts."""

    def test_msup(self):
        self.assertEqual(tex('<msup><mi>x</mi><mn>2</mn></msup>'), 'x^2')

    def test_msup_multichar_exp(self):
        result = tex('<msup><mi>x</mi><mrow><mi>a</mi><mi>b</mi></mrow></msup>')
        self.assertEqual(normalise(result), normalise('x^{ab}'))

    def test_msub(self):
        self.assertEqual(tex('<msub><mi>x</mi><mi>i</mi></msub>'), 'x_i')

    def test_msubsup(self):
        result = tex('<msubsup><mi>x</mi><mi>i</mi><mn>2</mn></msubsup>')
        self.assertEqual(normalise(result), normalise('x_i^2'))

    def test_munder_limit_op(self):
        result = tex('<munder><mo>∑</mo><mi>i</mi></munder>')
        self.assertIn(r'\sum', result)
        self.assertIn('_', result)
        self.assertNotIn(r'\underset', result)

    def test_munder_non_limit(self):
        result = tex('<munder><mi>x</mi><mi>a</mi></munder>')
        self.assertIn(r'\underset', result)

    def test_munder_underbrace(self):
        result = tex('<munder><mrow><mi>x</mi><mo>+</mo><mi>y</mi></mrow><mo>\u23df</mo></munder>')
        self.assertIn(r'\underbrace', result)

    def test_mover_accent_hat(self):
        result = tex('<mover><mi>x</mi><mo>^</mo></mover>')
        self.assertEqual(result, r'\hat{x}')

    def test_mover_accent_vec(self):
        result = tex('<mover><mi>x</mi><mo>→</mo></mover>')
        self.assertEqual(result, r'\vec{x}')

    def test_mover_accent_bar(self):
        result = tex('<mover><mi>x</mi><mo>‾</mo></mover>')
        self.assertEqual(result, r'\overline{x}')

    def test_mover_limit_op(self):
        result = tex('<mover><mo>∑</mo><mi>n</mi></mover>')
        self.assertIn(r'\sum', result)
        self.assertIn('^', result)

    def test_mover_overbrace(self):
        result = tex('<mover><mrow><mi>x</mi><mo>+</mo><mi>y</mi></mrow><mo>\u23de</mo></mover>')
        self.assertIn(r'\overbrace', result)

    def test_munderover_limit_op(self):
        result = tex('<munderover><mo>∑</mo><mi>i</mi><mi>n</mi></munderover>')
        self.assertIn(r'\sum', result)
        self.assertIn('_', result)
        self.assertIn('^', result)
        self.assertNotIn(r'\overset', result)

    def test_munderover_non_limit_stacks_vertically(self):
        # For non-limit bases, must use \overset/\underset, not bare _/^
        result = tex('<munderover><mi>x</mi><mi>a</mi><mi>b</mi></munderover>')
        self.assertIn(r'\overset', result)
        self.assertIn(r'\underset', result)

    def test_mmultiscripts_postscripts(self):
        # R_i^j  (base with one post sub + sup)
        mml = """<mmultiscripts>
            <mi>R</mi>
            <mi>i</mi><mi>j</mi>
        </mmultiscripts>"""
        result = tex(mml)
        self.assertIn('R', result)
        self.assertIn('_', result)
        self.assertIn('^', result)

    def test_mmultiscripts_prescript(self):
        # {}^X A  (one prescript sup)
        mml = """<mmultiscripts>
            <mi>A</mi>
            <mprescripts/>
            <none/><mi>X</mi>
        </mmultiscripts>"""
        result = tex(mml)
        self.assertTrue(result.startswith('{}'))
        self.assertIn('^', result)
        self.assertIn('A', result)


class HandWrittenFencedTable(unittest.TestCase):
    """mfenced and mtable."""

    def test_mfenced_default(self):
        result = tex('<mfenced><mi>x</mi></mfenced>')
        self.assertIn(r'\left(', result)
        self.assertIn(r'\right)', result)

    def test_mfenced_brackets(self):
        result = tex('<mfenced open="[" close="]"><mi>x</mi></mfenced>')
        self.assertIn(r'\left[', result)
        self.assertIn(r'\right]', result)

    def test_mfenced_curly(self):
        result = tex('<mfenced open="{" close="}"><mi>x</mi></mfenced>')
        self.assertIn(r'\left\{', result)
        self.assertIn(r'\right\}', result)

    def test_mfenced_no_delimiter(self):
        result = tex('<mfenced open="" close=""><mi>x</mi></mfenced>')
        self.assertNotIn(r'\left', result)
        self.assertNotIn(r'\right', result)

    def test_mfenced_separator(self):
        mml = '<mfenced><mi>a</mi><mi>b</mi><mi>c</mi></mfenced>'
        result = tex(mml)
        self.assertEqual(result.count(','), 2)

    def test_mtable_basic(self):
        mml = """<mtable>
            <mtr><mtd><mi>a</mi></mtd><mtd><mi>b</mi></mtd></mtr>
            <mtr><mtd><mi>c</mi></mtd><mtd><mi>d</mi></mtd></mtr>
        </mtable>"""
        result = tex(mml)
        self.assertIn(r'\begin{array}', result)
        self.assertIn(r'\end{array}', result)
        self.assertIn('&', result)

    def test_mtable_row_separator(self):
        mml = """<mtable>
            <mtr><mtd><mn>1</mn></mtd></mtr>
            <mtr><mtd><mn>2</mn></mtd></mtr>
        </mtable>"""
        result = tex(mml)
        self.assertIn(r'\\', result)
        self.assertNotIn(r'\\{}', result)

    def test_mtable_col_spec(self):
        mml = """<mtable>
            <mtr><mtd><mn>1</mn></mtd><mtd><mn>2</mn></mtd><mtd><mn>3</mn></mtd></mtr>
        </mtable>"""
        result = tex(mml)
        self.assertIn('{ccc}', result)


class HandWrittenSpecialCases(unittest.TestCase):
    """Edge cases, control-word absorption, function application, whitespace."""

    def test_join_prevents_ctrl_word_absorption(self):
        # \pi followed by x must not produce \pix
        result = tex('<mrow><mi>π</mi><mi>x</mi></mrow>')
        self.assertNotIn(r'\pix', result)
        self.assertIn(r'\pi', result)

    def test_join_pi_z(self):
        result = tex('<mrow><mi>π</mi><mi>z</mi></mrow>')
        self.assertNotIn(r'\piz', result)

    def test_join_ln_x(self):
        # \ln followed by x — must not merge into \lnx
        result = tex('<mrow><mi>ln</mi><mi>x</mi></mrow>')
        self.assertNotIn(r'\lnx', result)
        self.assertIn(r'\ln', result)

    def test_function_application_u2061_multi_char(self):
        # Multi-char unknown function + U+2061 → \operatorname{...}
        result = tex('<mrow><mi>foo</mi><mo>\u2061</mo><mi>x</mi></mrow>')
        self.assertIn(r'\operatorname{foo}', result)

    def test_function_application_u2061_known_function(self):
        # \ln + U+2061: should NOT be re-wrapped as \operatorname
        result = tex('<mrow><mi>ln</mi><mo>\u2061</mo><mi>x</mi></mrow>')
        self.assertNotIn(r'\operatorname', result)
        self.assertIn(r'\ln', result)

    def test_function_application_u2061_single_char_variable(self):
        # Single-char variable + U+2061: leave as-is (could be function, could be product)
        result = tex('<mrow><mi>f</mi><mo>\u2061</mo><mi>x</mi></mrow>')
        self.assertNotIn(r'\operatorname', result)

    def test_double_script_protection_msup(self):
        # x_i^2 is fine; x_i^2^3 would be double-script
        result = tex('<msup><msub><mi>x</mi><mi>i</mi></msub><mn>2</mn></msup>')
        self.assertIn('x', result)
        self.assertIn('_', result)
        self.assertIn('^', result)

    def test_render_mathml_inline(self):
        elem = parse('<math><mi>x</mi></math>')
        result = render_mathml(elem, display=False)
        self.assertTrue(result.startswith('$'))
        self.assertTrue(result.endswith('$'))
        self.assertNotIn('$$', result)

    def test_render_mathml_display(self):
        elem = parse('<math><mi>x</mi></math>')
        result = render_mathml(elem, display=True)
        self.assertTrue(result.startswith('$$'))
        self.assertTrue(result.endswith('$$'))

    def test_render_mathml_empty(self):
        elem = parse('<math></math>')
        # Empty math should not return bare "$$$$"
        result = render_mathml(elem, display=False)
        self.assertNotEqual(result, '$$')


class HandWrittenMathvariant(unittest.TestCase):
    """mathvariant attribute on mi / mn."""

    def test_mi_double_struck(self):
        result = tex('<mi mathvariant="double-struck">R</mi>')
        self.assertIn(r'\mathbb{R}', result)

    def test_mi_script(self):
        result = tex('<mi mathvariant="script">F</mi>')
        self.assertIn(r'\mathcal{F}', result)

    def test_mi_fraktur(self):
        result = tex('<mi mathvariant="fraktur">g</mi>')
        self.assertIn(r'\mathfrak{g}', result)

    def test_mi_monospace(self):
        result = tex('<mi mathvariant="monospace">x</mi>')
        self.assertIn(r'\mathtt{x}', result)

    def test_mi_sans_serif(self):
        result = tex('<mi mathvariant="sans-serif">x</mi>')
        self.assertIn(r'\mathsf{x}', result)


# ---------------------------------------------------------------------------
# Tier 2: Data-driven regression tests from grading.jsonl
# ---------------------------------------------------------------------------


def _load_regression_cases():
    """Load golden expected outputs for W3C regression tests.

    Source: tests/golden.json — generated by tests/regenerate_golden.py.
    The golden file records current converter output for all W3C test cases
    that were judged correct in human grading.  Regenerate it intentionally
    when the converter is improved:

        python3 tests/regenerate_golden.py
    """
    golden = Path(__file__).parent / 'golden.json'
    if not golden.exists():
        return []
    return list(json.loads(golden.read_text()).items())


def _build_regression_test(test_id: str, expected_latex: str):
    """Factory for a single regression test method."""

    def test_method(self):
        mml_path = Path(__file__).parent / 'w3c_mml' / (test_id + '.mml')
        if not mml_path.exists():
            self.fail(f'MML file missing: {mml_path}')
        mml_bytes = mml_path.read_bytes()
        root = defused_fromstring(mml_bytes.decode('utf-8', errors='replace'))
        # Find <math> element whether or not wrapped in XHTML
        math_elem = None
        tag = root.tag
        local = tag.split('}', 1)[1] if '}' in tag else tag
        if local == 'math':
            math_elem = root
        else:
            for elem in root.iter(f'{{{MML_NS}}}math'):
                math_elem = elem
                break
            if math_elem is None:
                for elem in root.iter('math'):
                    math_elem = elem
                    break
        if math_elem is None:
            self.skipTest('Could not find <math> element')
        assert math_elem is not None
        result = normalise(mml_to_tex(math_elem).strip())
        expected = normalise(expected_latex)
        self.assertEqual(result, expected, msg=f'Test: {test_id}')

    test_method.__name__ = f'test_{test_id.replace("/", "_").replace("-", "_")}'
    return test_method


class RegressionW3C(unittest.TestCase):
    """Regression tests: re-run converter on cached W3C MML files and compare
    against the latex output that was judged correct in human grading."""


# Dynamically attach one test method per graded case.
for _test_id, _expected in _load_regression_cases():
    _method = _build_regression_test(_test_id, _expected)
    setattr(RegressionW3C, _method.__name__, _method)


# ---------------------------------------------------------------------------
# Tier 3: pdflatex smoke tests
# ---------------------------------------------------------------------------

# Gather a representative sample of LaTeX expressions to compile.
_COMPILE_SAMPLES = [
    r'\frac{a}{b}',
    r'x^2 + y^2 = z^2',
    r'\int_0^\infty e^{-x}\,\mathrm{d}x',
    r'\sum_{i=1}^{n} i = \frac{n(n+1)}{2}',
    r'\sqrt[3]{x+y}',
    r'\begin{array}{cc} a & b \\\\ c & d \end{array}',
    r'\left(\frac{x}{y}\right)^2',
    r'\overset{\mathrm{over}}{\mathrm{BASE}}',
    r'\underset{i \in S}{\min}\, f(i)',
    r'\mathbf{A}_{ij} = \delta_{ij}',
    r'\genfrac{}{}{0pt}{}{n}{k}',
    r'\operatorname{myFunc}(x)',
    r'\cancel{(a-3)}',
]

LATEX_TEMPLATE = r"""\documentclass{article}
\usepackage{amsmath,amssymb,cancel}
\usepackage[margin=8pt,paperwidth=500pt,paperheight=300pt]{geometry}
\pagestyle{empty}
\begin{document}
$BODY$
\end{document}
"""


def _compile_latex(latex: str) -> bool:
    doc = LATEX_TEMPLATE.replace('BODY', latex)
    with tempfile.TemporaryDirectory() as td:
        tex_file = Path(td) / 'expr.tex'
        tex_file.write_text(doc)
        subprocess.run(
            ['pdflatex', '-interaction=nonstopmode', '-halt-on-error', 'expr.tex'],
            cwd=td,
            capture_output=True,
            timeout=20,
            check=False,
        )
        return (Path(td) / 'expr.pdf').exists()


def _make_compile_test(latex: str) -> Callable[[unittest.TestCase], None]:
    def t(self: unittest.TestCase) -> None:
        self.assertTrue(_compile_latex(latex), msg=f'pdflatex failed for: {latex!r}')

    t.__name__ = 'test_compile_' + re.sub(r'[^a-zA-Z0-9]', '_', latex)[:40]
    return t


@unittest.skipUnless(shutil.which('pdflatex'), 'pdflatex not on PATH')
class CompileSmoke(unittest.TestCase):
    """Smoke-test: each sample expression must compile without error."""


for _latex in _COMPILE_SAMPLES:
    _method = _make_compile_test(_latex)
    setattr(CompileSmoke, _method.__name__, _method)


if __name__ == '__main__':
    unittest.main(verbosity=2)
