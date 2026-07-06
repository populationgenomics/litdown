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

from __future__ import annotations

import importlib.metadata
import io
import pathlib
from typing import IO

import defusedxml.ElementTree

from litdown import common, elsevier, jats

# Re-exported as the package's public API (see __all__): callers use
# `from litdown import mml_to_tex, render_mathml`.
from litdown.mathml import mml_to_tex, render_mathml

try:
    __version__ = importlib.metadata.version('litdown')
except importlib.metadata.PackageNotFoundError:
    # Package metadata not available — e.g. running directly from the
    # source tree without an editable install.
    __version__ = '0.0.0+unknown'

__all__ = ['__version__', 'convert', 'mml_to_tex', 'render_mathml']


def convert(source: str | pathlib.Path | bytes | IO[bytes]) -> str:
    """Convert scholarly full-text XML to Markdown.

    ``source`` is a filesystem path (``str`` / ``pathlib.Path``), the XML
    ``bytes`` already in hand, or an open binary stream — a caller that has
    fetched the document need not spill it to a temp file first.

    Sniffs the root element's local name (a single cheap parse) and
    dispatches to the matching dialect. An unrecognised root raises
    rather than returning ``''`` — a silent empty string would mask
    "wrong bytes" bugs in the caller, the exact failure mode the Elsevier
    dialect was added to fix.
    """
    parseable = io.BytesIO(source) if isinstance(source, bytes) else source
    tree = defusedxml.ElementTree.parse(parseable)
    root = tree.getroot()
    if root is None:
        return ''

    name = common.get_tag(root)
    if name == 'article':
        return jats.render(root)
    if name == 'full-text-retrieval-response':
        return elsevier.render(root)
    raise ValueError(f'unrecognized root element: {root.tag}')
