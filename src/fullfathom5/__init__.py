try:
    from ._version import version as __version__  # created by setuptools-scm at build time
except Exception:  # pragma: no cover
    __version__ = "0.0.0"
