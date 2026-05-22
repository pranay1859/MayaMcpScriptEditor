"""maya-mcp: FastMCP server running inside Autodesk Maya.

Import this package from Maya's userSetup.py and call ``start_server()``.
"""
from maya_mcp.server import start_server, get_port, mcp

__all__ = ["start_server", "get_port", "mcp"]
__version__ = "0.1.0"
