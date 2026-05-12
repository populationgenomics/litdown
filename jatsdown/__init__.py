"""jatsdown — convert JATS XML articles to Markdown."""

from importlib.metadata import PackageNotFoundError, version

from jatsdown.jats import convert
from jatsdown.mathml import mml_to_tex, render_mathml

try:
    __version__ = version('jatsdown')
except PackageNotFoundError:
    # Package metadata not available — e.g. running directly from the
    # source tree without an editable install.
    __version__ = '0.0.0+unknown'

__all__ = ['__version__', 'convert', 'mml_to_tex', 'render_mathml']
