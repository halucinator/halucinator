# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.

from __future__ import annotations

import yaml
'''
    Import this file and yaml to change yaml's default integer writing to hex
    Useage:
    import hexyaml
    import yaml

    use yaml as normal
'''


def hexint_presenter(dumper: yaml.Dumper, data: int) -> yaml.Node:
    return dumper.represent_int(hex(data))


yaml.add_representer(int, hexint_presenter)
