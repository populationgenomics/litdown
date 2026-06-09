"""litdown — convert scholarly full-text XML to Markdown.

Currently ships a JATS dialect (:func:`convert`, backed by
:mod:`litdown.jats`); an Elsevier ``ce:``/``ja:`` dialect is planned, at
which point ``convert`` will sniff the schema and dispatch.  The
:mod:`litdown.mathml` MathML→LaTeX converter is dialect-agnostic and shared
across dialects.
"""

from importlib.metadata import PackageNotFoundError, version

from litdown.jats import convert
from litdown.mathml import mml_to_tex, render_mathml

try:
    __version__ = version('litdown')
except PackageNotFoundError:
    # Package metadata not available — e.g. running directly from the
    # source tree without an editable install.
    __version__ = '0.0.0+unknown'

__all__ = ['__version__', 'convert', 'mml_to_tex', 'render_mathml']
