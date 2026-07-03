"""\
Usages:
    termbook             read last epub
    termbook EPUBFILE    read EPUBFILE
    termbook STRINGS     read matched STRINGS from history
    termbook NUMBER      read file from history
                         with associated NUMBER

Options:
    -r              print reading history
    -d              dump epub
    -h, --help      print short, long help
    --clean         reset to fresh state (delete all bookmarks)
    --debug         show debug info (chapter, position, build time)

Key Binding:
    Help             : ?
    Quit             : q
    Scroll down      : DOWN
    Scroll up        : UP
    Page down        : PGDN      RIGHT   SPC
    Page up          : PGUP      LEFT
    Next chapter     : n          (or next search match, if a search is active)
    Prev chapter     : p          (or prev search match, if a search is active)
    Beginning of ch  : HOME
    End of ch        : END
    Open image       : i
    Open URL         : u
    Search           : /
    ToC              : TAB       t
    Metadata         : m
    Save bookmark    : s
    Bookmarks        : b
    Switch colorsch  : c
"""

import os
import re
import sys
import json
import shutil
import curses
from difflib import SequenceMatcher as SM

from termbook import state, __version__, __license__, __author__, __url__
from termbook.epub import Epub
from termbook.text_render import HTMLtoLines
from termbook.ui.bookmarks import loadstate
from termbook.reader import preread


def main():
    
    termc, termr = shutil.get_terminal_size()

    args = []
    if sys.argv[1:] != []:
        args += sys.argv[1:]

    if len({"-h", "--help"} & set(args)) != 0:
        hlp = __doc__.rstrip()
        if "-h" in args:
            hlp = re.search("(\n|.)*(?=\n\nKey)", hlp).group()
        print(hlp)
        sys.exit()

    if len({"-v", "--version", "-V"} & set(args)) != 0:
        print(__version__)
        print(__license__, "License")
        print("Copyright (c) 2025", __author__)
        print(__url__)
        sys.exit()

    # Check for debug flag
        state.DEBUG_MODE = len({"--debug"} & set(args)) != 0

    if len({"--clean", "--reset"} & set(args)) != 0:
        # Clean up all saved state files
        cleaned_files = []
        
        # Check for state files in various locations
        state_locations = []
        bookmark_locations = []
        if os.getenv("HOME"):
            state_locations.append(os.path.join(os.getenv("HOME"), ".termbook"))
            state_locations.append(os.path.join(os.getenv("HOME"), ".config", "termbook", "config"))
            bookmark_locations.append(os.path.join(os.getenv("HOME"), ".termbook_bookmarks.json"))
            bookmark_locations.append(os.path.join(os.getenv("HOME"), ".config", "termbook", "bookmarks.json"))
        elif os.getenv("USERPROFILE"):
            state_locations.append(os.path.join(os.getenv("USERPROFILE"), ".termbook"))
            bookmark_locations.append(os.path.join(os.getenv("USERPROFILE"), ".termbook_bookmarks.json"))
        
        # Clean state files
        for state_file in state_locations:
            if os.path.exists(state_file):
                try:
                    os.remove(state_file)
                    cleaned_files.append(state_file)
                except OSError as e:
                    print(f"Warning: Could not remove {state_file}: {e}")
        
        # Clean bookmark files
        for bookmark_file in bookmark_locations:
            if os.path.exists(bookmark_file):
                try:
                    os.remove(bookmark_file)
                    cleaned_files.append(bookmark_file)
                except OSError as e:
                    print(f"Warning: Could not remove {bookmark_file}: {e}")
        
        if cleaned_files:
            print("Cleaned up the following state files:")
            for f in cleaned_files:
                print(f"  - {f}")
            print("\nTermbook has been reset to a fresh state.")
            print("All bookmarks and reading positions have been removed.")
        else:
            print("No state files found. Termbook is already in a fresh state.")
        sys.exit()

    if len({"-d"} & set(args)) != 0:
        args.remove("-d")
        dump = True
    else:
        dump = False

    loadstate()

    if args == []:
        file, todel = False, []
        for i in state.STATE:
            if not os.path.exists(i):
                todel.append(i)
            elif state.STATE[i]["lastread"] == str(1):
                file = i

        for i in todel:
            del state.STATE[i]

        if not file:
            print(__doc__)
            sys.exit("ERROR: Found no last read file.")

    elif os.path.isfile(args[0]):
        file = args[0]

    else:
        val = cand = 0
        todel = []
        for i in state.STATE.keys():
            if not os.path.exists(i):
                todel.append(i)
            else:
                match_val = sum([j.size for j in SM(None, i.lower(), " ".join(args).lower()).get_matching_blocks()])
                if match_val >= val:
                    val = match_val
                    cand = i
        for i in todel:
            del state.STATE[i]
        with open(state.STATEFILE, "w") as f:
            json.dump(state.STATE, f, indent=4)
        if len(args) == 1 and re.match(r"[0-9]+", args[0]) is not None:
            try:
                cand = list(state.STATE.keys())[int(args[0])-1]
                val = 1
            except IndexError:
                val = 0
        if val != 0 and len({"-r"} & set(args)) == 0:
            file = cand
        else:
            print("Reading history:")
            dig = len(str(len(state.STATE.keys())+1))
            for n, i in enumerate(state.STATE.keys()):
                print(str(n+1).rjust(dig) + ("* " if state.STATE[i]["lastread"] == "1" else "  ") + i)
            if len({"-r"} & set(args)) != 0:
                sys.exit()
            else:
                print()
                sys.exit("ERROR: Found no matching history.")

    if dump:
        epub = Epub(file)
        epub.initialize()
        for i in epub.contents:
            content = epub.file.open(i).read()
            content = content.decode("utf-8")
            parser = HTMLtoLines(dump_mode=True)
            try:
                parser.feed(content)
                parser.close()
            except Exception as e:
                if state.DEBUG_MODE:
                    print(f"HTML parsing failed for {i}: {e}", file=sys.stderr)
            src_lines, imgs, img_alts = parser.get_lines()
            # sys.stdout.reconfigure(encoding="utf-8")  # Python>=3.7
            for j in src_lines:
                sys.stdout.buffer.write((j+"\n").encode("utf-8"))
        sys.exit()

    else:
        if termc < 22 or termr < 12:
            sys.exit("ERR: Screen was too small (min 22cols x 12rows).")
        curses.wrapper(preread, file)



if __name__ == "__main__":
    main()
