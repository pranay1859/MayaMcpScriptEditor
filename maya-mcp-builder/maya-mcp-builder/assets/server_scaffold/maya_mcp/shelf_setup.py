"""Maya MCP shelf setup script.

Run this script once in Maya's Script Editor (Python tab) to install
the MCP shelf. Safe to run multiple times -- it won't duplicate buttons.

Usage:
    1. Open Maya's Script Editor (Windows > General Editors > Script Editor)
    2. Switch to the Python tab
    3. Paste this file's contents and press Ctrl+Enter (or the Run button)
    4. A new "MCP" shelf tab appears in the Maya shelf bar

The shelf provides two buttons:
    - MCP OFF  -- Starts the Maya MCP server. Label updates to "MCP:<port>"
                  once running.
    - Copy URL -- Copies the MCP server URL to the clipboard so you can
                  paste it into Claude Desktop's config.

Note on persistence: Maya saves custom shelves to
    <user prefs>/prefs/shelves/shelf_MCP.mel
automatically when you quit (File > Save Preferences also works). After
the first save the shelf reloads on every Maya startup without re-running
this script.
"""
import maya.cmds as cmds

SHELF_NAME = "MCP"
SHELF_PARENT = "ShelfLayout"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _shelf_exists(name):
    """Return True if a shelfLayout named *name* already exists."""
    return cmds.shelfLayout(name, exists=True)


def _button_name_exists(name):
    """Return True if a shelfButton with this exact internal name already exists."""
    try:
        return cmds.shelfButton(name, exists=True)
    except RuntimeError:
        return False


# ---------------------------------------------------------------------------
# Shelf creation
# ---------------------------------------------------------------------------

def _ensure_shelf():
    """Create the MCP shelf if it does not already exist, then activate it."""
    if not _shelf_exists(SHELF_NAME):
        cmds.shelfLayout(SHELF_NAME, parent=SHELF_PARENT)

    # Make the new/existing shelf the active (visible) tab.
    try:
        cmds.shelfTabLayout(SHELF_PARENT, edit=True, selectTab=SHELF_NAME)
    except Exception:  # noqa: BLE001 -- shelfTabLayout may not exist in batch
        pass


# ---------------------------------------------------------------------------
# Button commands (Python strings executed inside Maya at click time)
# ---------------------------------------------------------------------------

_START_CMD = '''\
import maya_mcp
port = maya_mcp.get_port()
if port is None:
    port = maya_mcp.start_server()
    import maya.cmds as cmds
    cmds.shelfButton('maya_mcp_start_btn', edit=True, label='MCP:{}'.format(port))
    print('[maya-mcp] Server started on port {}'.format(port))
else:
    print('[maya-mcp] Already running on port {}'.format(port))
'''

_COPY_URL_CMD = '''\
import os
import maya_mcp
port = maya_mcp.get_port()
if port:
    url = 'http://127.0.0.1:{}/mcp'.format(port)
    import maya.cmds as cmds
    cmds.optionVar(stringValue=('_mcp_clipboard_url', url))
    try:
        import subprocess
        import sys
        if os.name == 'nt':
            # Windows: avoid a console flash in a GUI process
            subprocess.run(
                ['clip'],
                input=url.encode(),
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        elif sys.platform == 'darwin':
            subprocess.run(['pbcopy'], input=url.encode(), check=True)
        else:
            # Linux: try xclip, fall back to xsel
            try:
                subprocess.run(['xclip', '-selection', 'clipboard'], input=url.encode(), check=True)
            except FileNotFoundError:
                subprocess.run(['xsel', '--clipboard', '--input'], input=url.encode(), check=True)
    except Exception:
        pass
    print('[maya-mcp] URL: {}'.format(url))
else:
    print('[maya-mcp] Server not started. Click "MCP OFF" first.')
'''


# ---------------------------------------------------------------------------
# Button creation
# ---------------------------------------------------------------------------

def _add_start_button():
    """Add the Start/Status button if not already present."""
    if _button_name_exists("maya_mcp_start_btn"):
        return  # Already installed; leave it as-is (label may show MCP:<port>).

    cmds.shelfButton(
        "maya_mcp_start_btn",
        parent=SHELF_NAME,
        label=label,
        annotation="Start the Maya MCP server (connects Claude/Ollama to Maya)",
        command=_START_CMD,
        sourceType="python",
        imageOverlayLabel=label,
        # Use a built-in Maya icon that suggests networking / connection.
        image="commandButton.png",
    )


def _add_copy_url_button():
    """Add the Copy URL button if not already present."""
    if _button_name_exists("maya_mcp_copy_url_btn"):
        return

    cmds.shelfButton(
        "maya_mcp_copy_url_btn",
        parent=SHELF_NAME,
        label=label,
        annotation="Copy the MCP server URL to clipboard",
        command=_COPY_URL_CMD,
        sourceType="python",
        imageOverlayLabel=label,
        image="commandButton.png",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def install():
    """Create (or verify) the MCP shelf and its buttons."""
    _ensure_shelf()
    _add_start_button()
    _add_copy_url_button()
    print('[maya-mcp] Shelf "{}" installed. Run this script again to update.'.format(SHELF_NAME))


install()
