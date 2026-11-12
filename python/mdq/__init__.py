"""MDQ: extended Markdown processor. Logic lives in the Rust core."""
from mdq._native import MdqError, version_info

__version__: str = version_info()["package"]
__all__ = ["MdqError", "version_info", "__version__"]
