"""Sphinx documentation configuration for PolyFoamMesh.

Usage::

    pip install sphinx sphinx-rtd-theme sphinx-autodoc-typehints
    cd docs
    sphinx-build -b html . _build/html
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# -- Project information -----------------------------------------------------

project = "PolyFoamMesh"
copyright = "2026, PolyFoamMesh Project"
author = "Davide Valoroso"

# The full version, including alpha/beta/rc tags
release = "2.2.0"

# -- General configuration ---------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
    "sphinx_rtd_theme",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# -- Options for HTML output -------------------------------------------------

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_title = f"{project} v{release}"

# -- Autodoc configuration ---------------------------------------------------

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "private-members": False,
    "special-members": False,
    "inherited-members": False,
    "show-inheritance": True,
}

# -- Napoleon (Google/NumPy docstrings) --------------------------------------

napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True
napoleon_include_private_with_doc = False

# -- Intersphinx -------------------------------------------------------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "PySide6": ("https://doc.qt.io/qtforpython", None),
}

# -- Autosummary -------------------------------------------------------------

autosummary_generate = True
