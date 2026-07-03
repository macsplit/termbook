"""Shared mutable state accessed across multiple termbook modules.

Only names that are genuinely read or reassigned from more than one module
live here. Everything else that used to be a `global` in the single-file
termbook.py is confined to the one module that owns it (e.g. VWR/SEARCHPATTERN
stay in reader.py, GLOBAL_BOOKMARKS/BOOKMARKSFILE stay in ui/bookmarks.py) and
remains a plain module-level global there.

Other modules must read/write these via attribute access (`state.COLORSUPPORT`),
never `from termbook.state import COLORSUPPORT` + `global COLORSUPPORT` --
the latter only rebinds the importing module's local name and would not see
reassignments made elsewhere.
"""

STATE = {}
STATEFILE = ""
COLORSUPPORT = False
DEBUG_MODE = False  # set via --debug; read throughout for optional diagnostic logging
CURRENT_SEARCH_TERM = None  # current search term, used for highlighting while reading
