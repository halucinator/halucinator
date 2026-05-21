# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
"""
This is the halucinator entry point
"""
from __future__ import annotations

from argparse import ArgumentParser
import logging
import threading
import os
import sys
import argparse
import signal
from typing import Any, List, Optional, Tuple, Union

from avatar2 import Avatar
from avatar2.peripherals.avatar_peripheral import AvatarPeripheral
from .peripheral_models import generic as peripheral_emulators

from .bp_handlers import intercepts
from .bp_handlers.debugger import Debugger
from .debug_adapter.debug_adapter import DAPServer
from .peripheral_models import peripheral_server as periph_server
from .util.profile_hals import State_Recorder
from .util import cortex_m_helpers as CM_helpers
from . import hal_stats
from . import hal_log, hal_config


log = logging.getLogger(__name__)
hal_log.setLogConfig()


PATCH_MEMORY_SIZE = 4096
INTERCEPT_RETURN_INSTR_ADDR = 0x20000000 - PATCH_MEMORY_SIZE


def get_qemu_target(
    name: str,
    config: Any,
    firmware: None = None,
    log_basic_blocks: Optional[str] = None,
    gdb_port: int = 1234,
    qemu_args: str = None,
) -> Tuple[Avatar, Any]:
    """
    Returns QEMU and Avatar objects needed to run the firmware.
    """

    # Get info from config
    avatar_arch = config.machine.get_avatar_arch()

    qemu_path = config.machine.get_qemu_path()
    outdir = os.path.join("tmp", name)
    hal_stats.set_filename(outdir + "/stats.yaml")

    avatar = Avatar(arch=avatar_arch, output_directory=outdir)
    avatar.config = config
    avatar.cpu_model = config.machine.cpu_model
    log.info("GDB_PORT: %s", gdb_port)
    log.info("QEMU Path: %s", qemu_path)

    qemu_target = config.machine.get_qemu_target()
    qemu = avatar.add_target(
        qemu_target,
        machine=config.machine.machine,
        cpu_model=config.machine.cpu_model,
        gdb_executable=config.machine.gdb_exe,
        gdb_port=gdb_port,
        qmp_port=gdb_port + 1,
        firmware=firmware,
        executable=qemu_path,
        entry_address=config.machine.entry_addr,
        name=name,
        qmp_unix_socket=f"/tmp/{name}-qmp",
    )

    qemu_log_dir = os.path.join(outdir, "logs")
    os.makedirs(qemu_log_dir, exist_ok=True)

    if log_basic_blocks == "irq":
        qemu.additional_args = [
            "-d", "in_asm,exec,int,cpu,guest_errors,avatar,trace:nvic*",
            "-D", os.path.join(qemu_log_dir, "qemu_asm.log"),
        ]
    elif log_basic_blocks == "regs":
        qemu.additional_args = [
            "-d", "in_asm,exec,cpu",
            "-D", os.path.join(qemu_log_dir, "qemu_asm.log"),
        ]
    elif log_basic_blocks == "regs-nochain":
        qemu.additional_args = [
            "-d", "in_asm,exec,cpu,nochain",
            "-D", os.path.join(qemu_log_dir, "qemu_asm.log"),
        ]
    elif log_basic_blocks == "exec":
        qemu.additional_args = [
            "-d", "exec",
            "-D", os.path.join(qemu_log_dir, "qemu_asm.log"),
        ]
    elif log_basic_blocks == "trace-nochain":
        qemu.additional_args = [
            "-d", "in_asm,exec,nochain",
            "-D", os.path.join(qemu_log_dir, "qemu_asm.log"),
        ]
    elif log_basic_blocks == "trace":
        qemu.additional_args = [
            "-d", "in_asm,exec",
            "-D", os.path.join(qemu_log_dir, "qemu_asm.log"),
        ]
    elif log_basic_blocks == "coverage":
        qemu.additional_args = [
            "-d", "in_asm",
            "-D", os.path.join(qemu_log_dir, "qemu_asm.log"),
        ]

    if qemu_args is not None:
        if not hasattr(qemu, 'additional_args') or qemu.additional_args is None:
            qemu.additional_args = []
        qemu.additional_args.extend(qemu_args.split())

    return avatar, qemu


def setup_memory(
    avatar: Avatar,
    memory: Any,
    record_memories: Optional[List[Tuple[int, int]]] = None,
) -> None:
    """
    Sets up memory regions for the emualted devices
    Args:
        avatar(Avatar):
        name(str):    Name for the memory
        memory(HALMemoryConfigdict):
    """
    if memory.emulate is not None:
        emulate = getattr(peripheral_emulators, memory.emulate)
    else:
        emulate = None
    log.info(
        "Adding Memory: %s Addr: 0x%08x Size: 0x%08x",
        memory.name,
        memory.base_addr,
        memory.size,
    )
    avatar.add_memory_range(
        memory.base_addr,
        memory.size,
        name=memory.name,
        file=memory.file,
        permissions=memory.permissions,
        emulate=emulate,
        qemu_name=memory.qemu_name,
        irq=memory.irq_config,
        qemu_properties=memory.properties,
    )

    if record_memories is not None:
        if "w" in memory.permissions:
            record_memories.append((memory.base_addr, memory.size))


def run_server(avatar: Avatar) -> None:
    try:
        periph_server.run_server()
    except KeyboardInterrupt:
        periph_server.stop()
        avatar.stop()
        avatar.shutdown()
        quit(-1)



def fix_cortex_m_thumb_bit(config: Any) -> None:
    """
    Fixes and bug in QEMU that makes so thumb bit doesn't get set on CPSR. Manually set it up
    """

    # Bug in QEMU about init stack pointer/entry point this works around
    if config.machine.arch == "cortex-m3":
        mem = (
            config.memories["init_mem"]
            if "init_mem" in config.memories
            else config.memories["flash"]
        )
        if mem is not None and mem.file is not None:
            file_sp, file_entry = CM_helpers.get_sp_and_entry(mem.file)
            if config.machine.init_sp is None:
                config.machine.init_sp = file_sp
            if config.machine.entry_addr is None:
                config.machine.entry_addr = file_entry


def register_intercepts(config: Any, avatar: Avatar, qemu: Any) -> None:
    """
    Create and registers the intercepts, must be called after avatar.init_targets()
    """
    # Instantiate the BP Handler Classes
    added_classes = []
    for intercept in config.intercepts:
        bp_cls = intercepts.get_bp_handler(intercept)
        if issubclass(bp_cls.__class__, AvatarPeripheral):
            name, addr, size, per = bp_cls.get_mmio_info()
            if bp_cls not in added_classes:
                log.info(
                    "Adding Memory Region for %s, (Name: %s, Addr: %s, Size:%s)",
                    bp_cls.__class__.__name__,
                    name,
                    hex(addr),
                    hex(size),
                )
                avatar.add_memory_range(
                    addr,
                    size,
                    name=name,
                    permissions=per,
                    forwarded=True,
                    forwarded_to=bp_cls,
                )
                added_classes.append(bp_cls)

    # Register Avatar watchman for Break points and watch points
    avatar.watchmen.add_watchman(
        "BreakpointHit", "before", intercepts.interceptor, is_async=True
    )
    avatar.watchmen.add_watchman(
        "WatchpointHit", "before", intercepts.interceptor, is_async=True
    )

    # Register the BP handlers
    for intercept in config.intercepts:
        if intercept.bp_addr is not None:
            log.info("Registering Intercept: %s", intercept)
            intercepts.register_bp_handler(qemu, intercept)


def emulate_binary(
    config: Any,
    target_name: Optional[str] = None,
    log_basic_blocks: Optional[str] = None,
    rx_port: int = 5555,
    tx_port: int = 5556,
    gdb_port: int = 1234,
    elf_file: None = None,
    db_name: None = None,
    qemu_args: str = None,
    dap_port: Optional[int] = None,
    dap_bind: str = "127.0.0.1",
    gdb_server_port: Optional[int] = None,
) -> None:
    """
    Run binary on the emulated hardware.

    config.prepare_and_validate() MUST have been already called!
    """
    # Bug in QEMU about init stack pointer/entry point this works around
    if config.machine.arch == "cortex-m3":
        mem = (
            config.memories["init_mem"]
            if "init_mem" in config.memories
            else config.memories["flash"]
        )
        if mem is not None and mem.file is not None:
            file_sp, file_entry = CM_helpers.get_sp_and_entry(mem.file)
            # Only use discovered values if not explicitly set in config
            if config.machine.init_sp is None:
                config.machine.init_sp = file_sp
            if config.machine.entry_addr is None:
                config.machine.entry_addr = file_entry

    qemu_target_name = target_name if target_name else "halucinator"
    avatar, qemu = get_qemu_target(
        qemu_target_name,
        config,
        log_basic_blocks=log_basic_blocks,
        gdb_port=gdb_port,
        qemu_args=qemu_args,
    )

    if "remove_bitband" in config.options and config.options["remove_bitband"]:
        log.info("Removing Bitband")
        qemu.remove_bitband = True

    # Setup Memory Regions
    record_memories = []
    for memory in config.memories.values():
        setup_memory(avatar, memory, record_memories)

    # Add recorder to avatar
    # Used for debugging peripherals
    if elf_file is not None:
        if db_name is None:
            db_name = ".".join(
                (os.path.splitext(elf_file)[0], str(target_name), "sqlite")
            )

        avatar.recorder = State_Recorder(
            db_name, qemu, record_memories, elf_file
        )
    else:
        avatar.recorder = None

    qemu.gdb_port = gdb_port
    avatar.config = config
    log.info("Initializing Avatar Targets")
    avatar.init_targets()

    register_intercepts(config, avatar, qemu)
    config.initialize_target(qemu)

    # Set initial stack pointer if configured
    if config.machine.init_sp is not None:
        qemu.regs.sp = config.machine.init_sp

    # Cortex-M3 specific init
    if config.machine.arch == "cortex-m3":
        qemu.regs.cpsr |= 0x20  # Make sure the thumb bit is set
        qemu.set_vector_table_base(config.machine.vector_base)

    # Emulate the Binary
    periph_server.start(rx_port, tx_port, qemu)

    def signal_handler(sig: int, frame: Any) -> None:
        print("You pressed Ctrl+C!")
        avatar.stop()
        avatar.shutdown()
        periph_server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # Start GDB RSP server if requested
    if gdb_server_port is not None:
        avatar.load_plugin('gdbserver')
        server = avatar.spawn_gdb_server(
            qemu, gdb_server_port,
            stop_filter=lambda target, pc: intercepts.check_hal_bp(pc),
        )
        log.info("GDB RSP server listening on port %d", gdb_server_port)

    # Start DAP server if requested
    if dap_port is not None:
        debugger = Debugger(qemu, avatar, None)
        # Tell the intercept machinery we're in a debug session. This makes
        # HAL intercept handlers wait for the monitor thread's
        # emulation_detected ack before calling target.cont(), eliminating
        # the race where monitor_running sees STOPPED, enters the post-loop
        # branch, and then finds the target already RUNNING again by the
        # time _read_register("pc") is called.
        intercepts.debug_session = True
        # Start the debugger's monitor thread NOW so request_queue is always
        # being processed. Without this, send_request from DAP handlers blocks
        # forever until the queue gets serviced.
        debugger.start_monitoring(add_shell_callback=False)
        dap_thread = threading.Thread(
            target=DAPServer(debugger, dap_port, bind_addr=dap_bind),
            daemon=True,
        )
        dap_thread.start()
        log.info(
            "DAP server listening on %s:%d%s",
            dap_bind,
            dap_port,
            "" if dap_bind == "127.0.0.1" else " (EXPOSED: no auth)",
        )

    if dap_port is not None or gdb_server_port is not None:
        # When a debug server (DAP or GDB RSP) is enabled, leave QEMU
        # paused at entry so the debug client can attach, set breakpoints,
        # and resume explicitly. Without this, QEMU runs past entry before
        # the client has a chance to connect.
        log.info("QEMU paused at entry — connect a debug client to start execution")
    else:
        log.info("Letting QEMU Run")
        qemu.cont()

    run_server(avatar)


def main(cli_args: List[str] = None) -> None:
    """
    The entry point of HALucinator
    """
    parser = ArgumentParser()
    parser.add_argument(
        "-c",
        "--config",
        action="append",
        required=True,
        help="Config file(s) used to run emulation files are "
        "appended to each other with later files taking precedence",
    )
    parser.add_argument(
        "-s",
        "--symbols",
        action="append",
        default=[],
        help="CSV file with each row having symbol, first_addr, last_addr",
    )
    parser.add_argument(
        "--log_blocks",
        default=False,
        const=True,
        nargs="?",
        help="Enables QEMU's logging of basic blocks, "
        "options [irq, regs, exec, trace, trace-nochain]",
    )
    parser.add_argument(
        "-n",
        "--name",
        default="HALucinator",
        help="Name of target for avatar, used for logging",
    )
    parser.add_argument(
        "-r",
        "--rx_port",
        default=5555,
        type=int,
        help="Port number to receive zmq messages for IO on",
    )
    parser.add_argument(
        "-t",
        "--tx_port",
        default=5556,
        type=int,
        help="Port number to send IO messages via zmq",
    )
    parser.add_argument("-p", "--gdb_port", default=1234, type=int, help="GDB_Port")
    parser.add_argument(
        "-e", "--elf", default=None, help="Elf file, required to use recorder"
    )
    parser.add_argument(
        "--dap",
        type=int,
        nargs="?",
        const=34157,
        default=None,
        metavar="PORT",
        help="Start Debug Adapter Protocol server (default port: 34157)",
    )
    parser.add_argument(
        "--dap-bind",
        type=str,
        default="127.0.0.1",
        metavar="ADDR",
        dest="dap_bind",
        help=(
            "Interface for the DAP server to bind. Defaults to 127.0.0.1 "
            "(loopback-only) because no authentication is implemented. "
            "Use 0.0.0.0 to accept remote connections — only on trusted "
            "networks."
        ),
    )
    parser.add_argument(
        "--gdb-server",
        type=int,
        nargs="?",
        const=3333,
        default=None,
        metavar="PORT",
        dest="gdb_server",
        help="Start GDB RSP server for external debuggers (default port: 3333)",
    )
    parser.add_argument(
        "-q",
        "--qemu_args",
        nargs=argparse.REMAINDER,
        default=None,
        help="Additional arguments for QEMU",
    )

    args = parser.parse_args(sys.argv[1:] if cli_args is None else cli_args)

    # Build configuration
    config = hal_config.HalucinatorConfig()
    for conf_file in args.config:
        log.info("Parsing config file: %s", conf_file)
        config.add_yaml(conf_file)

    for csv_file in args.symbols:
        log.info("Parsing csv symbol file: %s", csv_file)
        config.add_csv_symbols(csv_file)

    if not config.prepare_and_validate():
        log.error("Config invalid")
        exit(-1)

    qemu_args = None
    if args.qemu_args:
        qemu_args = " ".join(args.qemu_args)

    emulate_binary(
        config,
        args.name,
        args.log_blocks,
        args.rx_port,
        args.tx_port,
        elf_file=args.elf,
        gdb_port=args.gdb_port,
        qemu_args=qemu_args,
        dap_port=args.dap,
        dap_bind=args.dap_bind,
        gdb_server_port=args.gdb_server,
    )


if __name__ == "__main__":
    main()
