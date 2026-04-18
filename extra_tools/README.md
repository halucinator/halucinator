# Extra Tools

Utilities for HALucinator Docker workflows and VSCode extension support.

## VSCode Extension Support

### parse_bp_handlers.py
Parses `@bp_handler` decorators from Python source to generate `bpdata.json`, used by the HALucinator VSCode extensions for autocomplete and intercept management.

```bash
python3 parse_bp_handlers.py -s /path/to/halucinator/src/halucinator -o bpdata.json
```

### run_parse_handlers.py
Orchestrates `parse_bp_handlers.py` with dynamically registered handler directories (via `.pth` files).

### add_handler_path.py / remove_handler_paths.py
Add or remove custom bp_handler search directories so the VSCode extensions can discover third-party handler classes.

```bash
python3 add_handler_path.py -a /path/to/custom/handlers
python3 remove_handler_paths.py
```

### vscode-extension-installer.sh
Downloads the HALucinator VSCode extensions from the
[halucinator-vscode releases page](https://github.com/GrammaTech/halucinator-vscode/releases)
and installs them into the host VSCode.

```bash
./vscode-extension-installer.sh          # latest release
./vscode-extension-installer.sh v1.0.0   # pinned release tag
```

## Cross-Compiler Installation

### install_aux_tools.sh
Installs GCC ARM cross-compiler versions 4.8 through 10.2 with `update-alternatives` support. Run inside a Docker container as root.

```bash
sudo ./install_aux_tools.sh
update-alternatives --config arm-none-eabi-gcc  # switch versions
```
