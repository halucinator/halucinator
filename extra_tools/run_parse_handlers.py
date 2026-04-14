#!/usr/bin/python3
#
# Run the parse_bp_handlers.py script with arguments
# collected from special files in the user site directory
#

import os
import sys
import site
import subprocess

parse_script = "/halucinator/extra_tools/parse_bp_handlers.py"
output_handler_file = "/home/haluser/project/.hal_bp_handlers.json"
source_list = ["/halucinator/src/halucinator"]


def add_project_path():
    """
    Adds the path to the project directory for python imports,
    if it has not already been added.
    """
    if not os.path.exists(
        os.path.join(site.USER_SITE, "home.haluser.project.pth")
    ):
        try:
            command = [
                "python3",
                "/halucinator/extra_tools/add_handler_path.py",
                "-a",
                "/home/haluser/project",
            ]
            result = subprocess.check_output(command)
        except:
            print(f"Unable to add project path: {result}")
            sys.exit(1)


def get_searchdir_from_filename(filename):
    """
    Process *.pth files to get the path that needs to be searched.
    The path file only adds a python import path, but the name of the file
    encodes the full name of the directory to be searched.
    Takes: Filename of PTH file found in python site directory
    Returns: The source directory path to be searched by the parser
    """
    if filename[-4:] == ".pth":
        # Get the directory name, which is the last part of the file name
        filename_asa_list = filename[:-4].split(".")
        subdir = filename_asa_list[-1]
        # Get the file contents, which is the python import dir
        with open(os.path.join(site.USER_SITE, filename), "r") as f:
            lines = f.readlines()
        # Get the first line that is not a comment
        for line in lines:
            if line.strip()[0] != "#":
                importdir = line.strip()
                break
        if importdir:
            # Sanity check: import dir should be encoded in file name
            if importdir.replace("/", ".").lstrip(".") in filename:
                # Add the tail subdir to get the full seearch path
                searchdir = os.path.join(importdir, subdir)
                if os.path.isdir(searchdir):
                    return searchdir

    # One or more of the above test do not pass, so do not consider this a valid file
    return None


def main(args):
    """
    Parse python src file to extract names of BP handlers
    """

    #
    # Collect added paths to search for classes
    add_project_path()
    filenames = next(os.walk(site.USER_SITE), (None, None, []))[2]
    for filename in filenames:
        searchdir = get_searchdir_from_filename(filename)
        if searchdir:
            source_list.append(searchdir)

    command = ["python3", parse_script]
    for srcdir in source_list:
        command.extend(["-s", srcdir])
    command.extend(["-o", output_handler_file])
    try:
        result = subprocess.check_output(command)
    except Exception as e:
        print(f"Error {result}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
