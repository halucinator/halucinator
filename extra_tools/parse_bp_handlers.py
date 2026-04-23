#!/usr/bin/python3
#
# Parse python source directories, collecting information about intercept handler classes
#

import sys
import argparse
import glob
import json
import os

CLASS_TAG = "class "
BPHANDLER_TAG = "@bp_handler"
DEF_TAG = "def "
OPEN_ROUND_BRACKET = "("
CLOSE_ROUND_BRACKET = ")"

DEBUG = False


def extract_name(line):
    """
    Extract name of a class or function for the follwing format
    class XXXX(
    def ZZZZZ(
    """
    return (
        line.strip().split(" ", 1)[1].split(OPEN_ROUND_BRACKET, 1)[0].strip()
    )


def extract_decorator_func_list(line):
    """
        Extract the list of functions that are intercepted
        using the next function
        @bp_handler(['option_1', 'option_2'])
        would return ['option_1', 'option_2']

    @bp_handler(
        ["HAL_UART_Transmit", "HAL_UART_Transmit_IT", "HAL_UART_Transmit_DMA"]
    )
        Args:
            line (str): line as a string from the python bp_handlers file
        Returns a list
    """
    line = "".join(line.split())
    line = "".join(line.split('"'))
    line = "".join(line.split("'"))
    line = line.strip(BPHANDLER_TAG).strip("([])").split(",")
    return line


def extract_handlers(input_file):
    """
    Extract classes and their bp handlers from the input python file
    """
    output_list = {}
    class_flag = False
    bph_flag = False
    more_bph_to_parse = False
    class_name = ""
    bph_dict = {}
    line_to_parse = ""
    bph_dec_options = []
    with open(input_file, "r") as fp:
        for line in fp:
            if OPEN_ROUND_BRACKET in line and line.strip().startswith(
                CLASS_TAG
            ):
                bph_dec_options = []
                # if the flag not set that means the first class is hit
                # and nothing needs to be stored
                if class_flag:
                    if bph_dict:
                        output_list[class_name] = bph_dict
                else:
                    class_flag = True
                class_name = extract_name(line)
                bph_dict = {}
                bph_flag = False
                continue
            if BPHANDLER_TAG in line:
                bph_flag = True
                # we need to make sure we parse this
                # line for the function names
                if OPEN_ROUND_BRACKET in line:
                    more_bph_to_parse = True
            if (
                more_bph_to_parse
            ):  # notice no continue in last case, this assumes that
                # get until the end of the bp_handler list,
                # may need to go multiple lines
                line_to_parse += line
                if CLOSE_ROUND_BRACKET in line:
                    # we have the end of the bp_handler list, can parse now
                    more_bph_to_parse = False
                    # process the line now, then continue
                    bph_dec_options = extract_decorator_func_list(
                        line_to_parse
                    )
                    line_to_parse = ""
                continue
            if (
                OPEN_ROUND_BRACKET in line
                and line.strip().startswith(DEF_TAG)
                and bph_flag
            ):
                bph_flag = False
                bph_name = extract_name(line)
                bph_dict[bph_name] = bph_dec_options

    if bph_dict:
        output_list[class_name] = bph_dict
    return output_list


def create_parser():
    """
    Creates the argument parser:

    Usage: parse_bp_handlers.py [-h] [-s SRCDIR] [-o OUTPUT_JSON_FILE]

    Parse python source files, extracting information about HALucinator breakpoint
    handler classes.

    optional arguments:
      -h, --help            show this help message and exit
      -s SRCDIR, --srcdir SRCDIR
                            A source directory to be searched for handlers (may be
                            repeated).
      -o OUTPUT_JSON_FILE, --output_json_file OUTPUT_JSON_FILE
                            The JSON file to be created or updated with extracted
                            data

    e.g.
    python3 parse_bp_handlers.py  -s /halucinator/src/halucinator -o /home/haluser/project/.hal_bp_handlers.json
    """
    parser = argparse.ArgumentParser(
        description="\nParse python source files, extracting information about HALucinator breakpoint handler classes.\n"
    )
    parser.add_argument(
        "-s",
        "--srcdir",
        action="append",
        default=[],
        help="A source directory to be searched for handlers (may be repeated).",
    )
    parser.add_argument(
        "-o",
        "--output_json_file",
        default="-",
        help="The JSON file to be created or updated with extracted data",
    )
    return parser


def create_class_path(srcdir, fullpath):
    """
    Converts the file path where a class was found into a class path that can be used
    for importing the class.
    NOTE: There is an assumption here that srcdir is a path that python will be able to
          import from (that it is included in PYTHONPATH, for example).
    srcdir:   The absolute path of the directory to (potentially) be added to the list
              of directories to search for handler classes
    fullpath: The absolute path of a python file that contains handler classes

    """
    importdir = os.path.dirname(srcdir)
    #
    # Check that fullpath starts with srcdir, if not, do not go on
    if fullpath[: len(importdir)] == importdir:
        classpath = fullpath[len(importdir) + 1 :]
        return classpath.replace("/", ".").replace(".py", "")
    return None


def main(args):
    """
    Parse python src file to extract names of BP handlers
    """
    args = create_parser().parse_args(args)

    classfile_object_list = []
    for thisdir in args.srcdir:
        # Remove optional trailing slash, for consistency
        srcdir = thisdir.rstrip("/")
        if DEBUG:
            print(f"- Parsing {srcdir}")

        # Make source source directory exists
        if not os.path.isdir(srcdir):
            if DEBUG:
                print(f"File path {srcdir} not found, so skipping it.")
            continue

        # TODO Managing whether or not to require bp_handlers subdir should be/will be handled by the caller!
        # Search for handlers under this source directory
        files = glob.glob(srcdir + "/**/*.py", recursive=True)
        classfile_list = []
        for file in files:

            # Special case: for halucinator and project, don't search any subdir other than "bp_handlers"
            if (
                srcdir == "/halucinator/src/halucinator"
                or srcdir == "/home/haluser/project"
            ):
                if not file.endswith(
                    "bp_handlers", 0, len(srcdir + "/bp_handlers")
                ):
                    if DEBUG:
                        print(f"Rejecting non-bp_handlers dir: {file}")
                    continue

            bp = extract_handlers(file)
            if bp:
                classfile_list.append((file, bp))

        # Create a list of classfile objects for this srcdir
        for classfile in classfile_list:
            # classfile[0] is the absolute path to the file
            # classfile[1] is the dict of classes in that file
            classpath = create_class_path(srcdir, classfile[0])
            if not classpath:
                continue
            classes = classfile[1]
            for aclass in classes:
                class_dict = {}
                class_dict["class"] = classpath + "." + aclass
                class_dict["funcs"] = list(classes[aclass].keys())
                mapping = {}
                for func_name, option_list in classes[aclass].items():
                    for opt in option_list:
                        mapping[opt] = func_name
                class_dict["mapping"] = mapping
                classfile_object_list.append(class_dict)

    if args.output_json_file == "-":
        outf = sys.stdout
    else:
        outf = open(args.output_json_file, "w")
    json.dump(classfile_object_list, outf, ensure_ascii=False, indent=4)


if __name__ == "__main__":
    main(sys.argv[1:])
