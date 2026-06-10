"""litdown — convert scholarly full-text XML to Markdown.

Ships two dialects behind a single :func:`convert` entry point, which
sniffs the document root and dispatches:

* **JATS** (``<article>``) — PMC / NLM full text, via :mod:`litdown.jats`.
* **Elsevier** (``<full-text-retrieval-response>``) — the ScienceDirect
  Article Retrieval API's ``xocs``/``ja``/``ce`` schema, via
  :mod:`litdown.elsevier`.

The :mod:`litdown.mathml` MathML→LaTeX converter and the dialect-neutral
leaves in :mod:`litdown.common` are shared across both dialects.
"""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from defusedxml.ElementTree import parse as defused_parse

from litdown import elsevier, jats
from litdown.common import get_tag
from litdown.mathml import mml_to_tex, render_mathml

try:
    __version__ = version('litdown')
except PackageNotFoundError:
    # Package metadata not available — e.g. running directly from the
    # source tree without an editable install.
    __version__ = '0.0.0+unknown'

__all__ = ['__version__', 'convert', 'mml_to_tex', 'render_mathml']


def convert(xml_path: str | Path) -> str:
    """Convert a scholarly full-text XML file to Markdown.

    Sniffs the root element's local name (a single cheap parse) and
    dispatches to the matching dialect. An unrecognised root raises
    rather than returning ``''`` — a silent empty string would mask
    "wrong bytes" bugs in the caller, the exact failure mode the Elsevier
    dialect was added to fix.
    """
    tree = defused_parse(xml_path)
    root = tree.getroot()
    if root is None:
        return ''

    name = get_tag(root)
    if name == 'article':
        return jats.render(root)
    if name == 'full-text-retrieval-response':
        return elsevier.render(root)
    raise ValueError(f'unrecognized root element: {root.tag}')
