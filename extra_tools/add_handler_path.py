#!/usr/bin/python3
#
# Add directories to the parse paths and import paths
#

import os
import sys
import site
import argparse


def create_parser():
    """
    Usage: add_handler_path.py [-h] [-a APPEND]

    Parse python source files, extracting information about HALucinator breakpoint
    handler classes.

    optional arguments:
      -h, --help            show this help message and exit
      -a APPEND, --append APPEND
                            Add a source directory to be searched for handlers
                            (may be repeated). Once added this directory will
                            continue to be used, until removed.

    """
    parser = argparse.ArgumentParser(
        description="\nParse python source files, extracting information about HALucinator breakpoint handler classes.\n"
    )
    parser.add_argument(
        "-a",
        "--append",
        action="append",
        default=[],
        help="Add a source directory to be searched for handlers (may be repeated). Once added this directory will continue to be used, until removed.",
    )
    return parser


def main(args):
    """
    Add a specially coded .pth files to the python USER_SITE dir,
    thereby adding a intercept handler class path to halucinator.
    """
    args = create_parser().parse_args(args)

    if len(args.append) > 0:
        #
        # If no user site directory yet, create one.
        if not os.path.isdir(site.USER_SITE):
            os.makedirs(site.USER_SITE)

        #
        # For each path to be added, if it exists, create a file in the user site dir
        # with a name that matches the path (except slashes replaced by dots) and
        # (importantly) the name also includes the package name part of the class path
        for srcdir in args.append:
            if not os.path.isdir(srcdir):
                print(f"No such directory: {srcdir}")
                sys.exit(1)
            importdir = os.path.dirname(srcdir)
            pathfilename = srcdir.lstrip("/").replace("/", ".") + ".pth"
            fullfilename = os.path.join(site.USER_SITE, pathfilename)
            #
            # The contents of the file is one line that is the path to be added to
            # python's import paths
            with open(fullfilename, "w") as f:
                f.write(f"{importdir}\n")


if __name__ == "__main__":
    main(sys.argv[1:])
