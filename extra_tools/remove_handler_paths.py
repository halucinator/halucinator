#!/usr/bin/python3
#
# Remove all directories that had been added for parsing and python importing
#

import os
import sys
import site


def main(args):
    """
    Clean up the specially coded .pth files from the python USER_SITE dir,
    thereby removing all halucinator added paths.
    """

    filenames = next(os.walk(site.USER_SITE), (None, None, []))[2]
    for filename in filenames:
        if not filename.endswith(".pth"):
            continue
        with open(os.path.join(site.USER_SITE, filename), "r") as f:
            lines = f.readlines()
        for line in lines:
            if line.strip()[0] != "#":
                importdir = line.strip()
                break
        if importdir:
            # Sanity check: import dir should be encoded in file name
            # If that is not true, leave the file alone.
            if importdir.replace("/", ".").lstrip(".") in filename:
                os.remove(os.path.join(site.USER_SITE, filename))


if __name__ == "__main__":
    main(sys.argv[1:])
