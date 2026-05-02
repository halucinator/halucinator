# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
"""
This is the halucinator entry point
"""

from argparse import ArgumentParser
import logging
from multiprocessing import Lock
import threading
import os
import sys
import argparse
import signal

from avatar2 import Avatar
from avatar2.peripherals.avatar_peripheral import AvatarPeripheral
from .peripheral_models import generic as peripheral_emulators

from .bp_handlers import intercepts
from .peripheral_models import peripheral_server as periph_server
from .util.profile_hals import State_Recorder
from .util import cortex_m_helpers as CM_helpers
from . import hal_stats
from . import hal_log, hal_config


log = logging.getLogger(__name__)
hal_log.setLogConfig()


PATCH_MEMORY_SIZE = 4096
INTERCEPT_RETURN_INSTR_ADDR = 0x20000000 - PATCH_MEMORY_SIZE
__HAL_EXIT_CODE = 0


def get_qemu_target(
    name,
    config,
    firmware=None,
    log_basic_blocks=False,
    gdb_port=1234,
    singlestep=False,
    qemu_args=None,
):  # pylint: disable=too-many-arguments
    """
    Instantiates QEMU instance that is used to run firmware using Avatar
    """
    outdir = os.path.join("tmp", name)
    os.makedirs(os.path.join(outdir, "logs"), exist_ok=True)
    hal_stats.set_filename(outdir + "/stats.yaml")

    # Get info from config
    avatar_arch = config.machine.get_avatar_arch()

    avatar = Avatar(arch=avatar_arch, output_directory=outdir)
    avatar.config = config
    avatar.cpu_model = config.machine.cpu_model

    qemu_path = config.machine.get_qemu_path()
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

    if log_basic_blocks == "irq":
        qemu.additional_args = [
            "-d",
            "in_asm,exec,int,cpu,guest_errors,avatar,trace:nvic*",
            "-D",
            os.path.join(outdir, "logs", "qemu_asm.log"),
        ]
    elif log_basic_blocks == "regs":
        qemu.additional_args = [
            "-d",
            "in_asm,exec,cpu",
            "-D",
            os.path.join(outdir, "logs", "qemu_asm.log"),
        ]
    elif log_basic_blocks == "regs-nochain":
        qemu.additional_args = [
            "-d",
            "in_asm,exec,cpu,nochain",
            "-D",
            os.path.join(outdir, "logs", "qemu_asm.log"),
        ]
    elif log_basic_blocks == "exec":
        qemu.additional_args = [
            "-d",
            "exec",
            "-D",
            os.path.join(outdir, "logs", "qemu_asm.log"),
        ]
    elif log_basic_blocks == "trace-nochain":
        qemu.additional_args = [
            "-d",
            "in_asm,exec,nochain",
            "-D",
            os.path.join(outdir, "logs", "qemu_asm.log"),
        ]
    elif log_basic_blocks == "trace":
        qemu.additional_args = [
            "-d",
            "in_asm,exec",
            "-D",
            os.path.join(outdir, "logs", "qemu_asm.log"),
        ]
    elif log_basic_blocks:
        qemu.additional_args = [
            "-d",
            "in_asm",
            "-D",
            os.path.join(outdir, "logs", "qemu_asm.log"),
        ]

    if singlestep:
        qemu.additional_args.append("-singlestep")
    if qemu_args is not None:
        qemu.additional_args.extend(qemu_args.split())

    return avatar, qemu


def setup_memory(avatar, memory, record_memories=None):
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


def fix_cortex_m_thumb_bit(config):
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
            config.machine.init_sp, entry_addr = CM_helpers.get_sp_and_entry(mem.file)
        # Only use the discoved entry point if one not explicitly set
        if config.machine.entry_addr is None:
            config.machine.entry_addr = entry_addr


def register_intercepts(config, avatar, qemu):
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
    config,
    target_name=None,
    log_basic_blocks=None,
    rx_port=5555,
    tx_port=5556,
    gdb_port=1234,
    elf_file=None,
    db_name=None,
    singlestep=False,
    qemu_args=None,
    gdb_server_port=None,
    print_qemu_command=None,
    emulator: str = "avatar2",
):  # pylint: disable=too-many-arguments,too-many-locals
    """
    Start emulation of the firmware.

    emulator: backend to use — "avatar2" (default), "qemu", or "unicorn".
    """

    # Non-avatar2 backends go through the new HalBackend factory path.
    if emulator != "avatar2":
        return _emulate_with_backend(
            config=config,
            emulator=emulator,
            target_name=target_name,
            log_basic_blocks=log_basic_blocks,
            rx_port=rx_port,
            tx_port=tx_port,
            gdb_port=gdb_port,
            elf_file=elf_file,
            db_name=db_name,
            singlestep=singlestep,
            qemu_args=qemu_args,
            gdb_server_port=gdb_server_port,
            print_qemu_command=print_qemu_command,
        )

    # Legacy avatar2 path — unchanged behaviour.
    avatar, qemu = get_qemu_target(
        target_name,
        config,
        log_basic_blocks=log_basic_blocks,
        gdb_port=gdb_port,
        singlestep=singlestep,
        qemu_args=qemu_args,
    )
    if print_qemu_command:
        print("QEMU Command")
        print(" ".join(qemu.assemble_cmd_line()))
        sys.exit(0)

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

        avatar.recorder = State_Recorder(db_name, qemu, record_memories, elf_file)
    else:
        avatar.recorder = None

    qemu.gdb_port = gdb_port
    avatar.config = config
    log.info("Initializing Avatar Targets")
    avatar.init_targets()

    if gdb_server_port is not None and gdb_server_port >= 0:
        avatar.load_plugin("gdbserver")
        # pylint: disable=no-member
        avatar.spawn_gdb_server(qemu, gdb_server_port, do_forwarding=False)

    register_intercepts(config, avatar, qemu)

    # Do post qemu creation initialization
    config.initialize_target(qemu)

    # Work around Avatar-QEMU's improper init of Cortex-M3
    if config.machine.arch == "cortex-m3":
        qemu.regs.cpsr |= 0x20  # Make sure the thumb bit is set
        qemu.regs.sp = config.machine.init_sp  # Set SP as Qemu doesn't init correctly
        qemu.set_vector_table_base(config.machine.vector_base)
    elif config.machine.init_sp is not None:
        # Other archs (arm64/mips/ppc/ppc64) need SP set manually — the
        # firmware's _start prologue assumes a valid stack.
        qemu.regs.sp = config.machine.init_sp

    _start_execution(avatar, qemu, rx_port, tx_port, gdb_server_port)


def _start_execution(avatar, qemu, rx_addr, tx_addr, gdb_server_port):
    """
    Starts the actual execution of qemu,
    peripheral server with handlers to enable clean
    exiting
    """
    # Emulate the Binary
    periph_server.start(rx_addr, tx_addr, qemu)

    # Removed because of issues in python 3.10 which is default in ubuntu 22.04
    # exit_code_lock = Lock()

    def halucinator_shutdown(exit_code):
        """
        Perform a clean shutdown of halucinator
        """
        global __HAL_EXIT_CODE  # pylint: disable=global-statement
        # with exit_code_lock:

        if threading.current_thread() != threading.main_thread():
            # Main thread must kill everything
            signal.raise_signal(signal.SIGINT)
        else:
            __HAL_EXIT_CODE = exit_code
            avatar.stop()
            avatar.shutdown()
            periph_server.stop()
            sys.exit(__HAL_EXIT_CODE)

    def int_signal_handler(sig, frame):  # pylint: disable=unused-argument
        print(f"Halucinator Exiting with status {__HAL_EXIT_CODE}!")
        halucinator_shutdown(__HAL_EXIT_CODE)

    signal.signal(signal.SIGINT, int_signal_handler)
    qemu.halucinator_shutdown = halucinator_shutdown
    log.info("Letting QEMU Run")

    if gdb_server_port is not None:
        print(f"GDB Server Running on localhost:{gdb_server_port}")
        print("Connect GDB and continue to run")
    else:
        qemu.cont()
    try:
        periph_server.run_server()  # Blocks Forever
    except KeyboardInterrupt:
        pass
    halucinator_shutdown(0)


def _emulate_with_backend(config, emulator, target_name=None,
                          log_basic_blocks=None, rx_port=5555, tx_port=5556,
                          gdb_port=1234, elf_file=None, db_name=None,
                          singlestep=False, qemu_args=None,
                          gdb_server_port=None, print_qemu_command=None):
    """
    Non-avatar2 emulation entry point using the HalBackend abstraction.
    """
    if emulator == "qemu":
        return _emulate_with_qemu_backend(
            config, target_name=target_name,
            log_basic_blocks=log_basic_blocks, rx_port=rx_port,
            tx_port=tx_port, gdb_port=gdb_port, elf_file=elf_file,
            singlestep=singlestep, qemu_args=qemu_args,
            print_qemu_command=print_qemu_command,
            gdb_server_port=gdb_server_port,
        )
    if emulator == "unicorn":
        return _emulate_with_unicorn_backend(
            config, target_name=target_name, rx_port=rx_port, tx_port=tx_port,
        )
    if emulator == "renode":
        return _emulate_with_renode_backend(
            config, target_name=target_name, rx_port=rx_port, tx_port=tx_port,
            gdb_server_port=gdb_server_port,
        )
    if emulator == "ghidra":
        return _emulate_with_ghidra_backend(
            config, target_name=target_name, rx_port=rx_port, tx_port=tx_port,
        )
    raise NotImplementedError(
        f"Backend {emulator!r} not yet wired into main.py. "
        f"Supported: 'avatar2' (default), 'qemu', 'unicorn', 'renode', 'ghidra'."
    )


def _preregister_avatar_peripherals(config, avatar):
    """Walk the intercepts list, instantiate each AvatarPeripheral
    subclass, and register a forwarded memory range for it. Must run
    before the QEMU config JSON is generated so avatar-rmemory regions
    land in the config file."""
    added = []
    for intercept in config.intercepts:
        bp_cls = intercepts.get_bp_handler(intercept)
        if not issubclass(bp_cls.__class__, AvatarPeripheral):
            continue
        if bp_cls in added:
            continue
        name, addr, size, per = bp_cls.get_mmio_info()
        log.info(
            "Adding MMIO forwarding for %s (name=%s addr=0x%x size=0x%x)",
            bp_cls.__class__.__name__, name, addr, size,
        )
        avatar.add_memory_range(
            addr, size, name=name, permissions=per,
            forwarded=True, forwarded_to=bp_cls,
        )
        added.append(bp_cls)
    return added


class _MMIOForwardingDispatcher(threading.Thread):
    """Pulls RemoteMemoryReadMessage/WriteMessage off the avatar.queue and
    dispatches them to the appropriate AvatarPeripheral's read_memory /
    write_memory, then sends the response back via the protocol's send_response.

    This replaces avatar2's normal event loop (which we don't run in the
    direct-QEMU path) for the specific job of handling MMIO forwarding."""

    def __init__(self, avatar, rmp):
        super().__init__(daemon=True, name="mmio_forwarding")
        self.avatar = avatar
        self.rmp = rmp
        self._stop_evt = threading.Event()

    def run(self):
        from avatar2.message import (
            RemoteMemoryReadMessage, RemoteMemoryWriteMessage,
        )
        import queue as _queue
        while not self._stop_evt.is_set():
            try:
                msg = self.avatar.queue.get(timeout=0.2)
            except _queue.Empty:
                continue
            try:
                if isinstance(msg, RemoteMemoryReadMessage):
                    self._handle_read(msg)
                elif isinstance(msg, RemoteMemoryWriteMessage):
                    self._handle_write(msg)
                else:
                    # Unknown message type — log and drop
                    log.debug("MMIO dispatcher: ignoring %r", type(msg).__name__)
            except Exception:
                log.exception("MMIO forwarding dispatcher error")

    def _handle_read(self, msg):
        rng = self.avatar.get_memory_range(msg.address)
        if rng is None or not rng.forwarded or rng.forwarded_to is None:
            self.rmp.send_response(msg.id, 0, False)
            return
        try:
            val = rng.forwarded_to.read_memory(
                msg.address, msg.size, num_words=msg.num_words, raw=msg.raw,
            )
            if isinstance(val, (bytes, bytearray)):
                val = int.from_bytes(val[:msg.size], "little")
            self.rmp.send_response(msg.id, int(val), True)
        except Exception:
            log.exception("AvatarPeripheral read failed")
            self.rmp.send_response(msg.id, 0, False)

    def _handle_write(self, msg):
        rng = self.avatar.get_memory_range(msg.address)
        if rng is None or not rng.forwarded or rng.forwarded_to is None:
            self.rmp.send_response(msg.id, 0, False)
            return
        try:
            rng.forwarded_to.write_memory(
                msg.address, msg.size, msg.value,
            )
            self.rmp.send_response(msg.id, 0, True)
        except Exception:
            log.exception("AvatarPeripheral write failed")
            self.rmp.send_response(msg.id, 0, False)

    def stop(self):
        self._stop_evt.set()


def _start_mmio_forwarding(avatar, qemu_target):
    """If any memory range is forwarded, create a RemoteMemoryProtocol on
    the mqueue pair QEMU opened for avatar-rmemory and start the
    dispatcher thread. Returns the dispatcher (for shutdown) or None if
    there's nothing to forward."""
    forwarded = [m for (_, _, m) in avatar.memory_ranges.iter()
                 if getattr(m, "forwarded", False)]
    if not forwarded:
        return None
    try:
        from avatar2.protocols.remote_memory import RemoteMemoryProtocol
    except ImportError:
        log.warning("RemoteMemoryProtocol unavailable; MMIO forwarding disabled")
        return None
    rmp = RemoteMemoryProtocol(
        qemu_target._rmem_tx_queue_name,  # QEMU's tx = our rx
        qemu_target._rmem_rx_queue_name,  # QEMU's rx = our tx
        avatar.queue,
        origin=qemu_target,
    )
    # Avatar2's main thread (started by Avatar.__init__) races our dispatcher
    # for the same queue; whichever picks up a RemoteMemory{Read,Write}Message
    # first, the avatar2 handler does `message.origin.protocols.remote_memory
    # .send_response(...)`. Register the protocol so that path doesn't crash.
    qemu_target.protocols.remote_memory = rmp
    if not rmp.connect():
        log.error("RemoteMemoryProtocol failed to connect; MMIO forwarding off")
        return None
    log.info("MMIO forwarding listener attached (rx=%s tx=%s)",
             qemu_target._rmem_tx_queue_name, qemu_target._rmem_rx_queue_name)
    dispatcher = _MMIOForwardingDispatcher(avatar, rmp)
    dispatcher.start()
    return dispatcher


def _emulate_with_qemu_backend(config, target_name, log_basic_blocks,
                               rx_port, tx_port, gdb_port, elf_file,
                               singlestep, qemu_args, print_qemu_command,
                               gdb_server_port=None):
    """
    Direct-QEMU path: spawns QEMU ourselves and drives it via GDB RSP + QMP
    through QEMUBackend, without avatar2 in the loop for runtime control.

    We still reuse avatar2's QemuTarget for the configurable-machine JSON
    and command-line assembly — that logic is non-trivial and duplicating
    it here would just create drift.
    """
    import subprocess
    import time
    from halucinator.backends.qemu_backend import QEMUBackend

    # Step 1: build avatar + qemu_target (no init_targets, so no spawn).
    avatar, qemu_target = get_qemu_target(
        target_name, config, log_basic_blocks=log_basic_blocks,
        gdb_port=gdb_port, singlestep=singlestep, qemu_args=qemu_args,
    )
    # Force TCP QMP (our _QMPClient only speaks TCP).
    qemu_target.qmp_unix_socket = None
    qemu_target.qmp_port = gdb_port + 1

    # Step 2: wire memory regions into avatar (same as avatar2 path).
    record_memories = []
    for memory in config.memories.values():
        setup_memory(avatar, memory, record_memories)

    if "remove_bitband" in config.options and config.options["remove_bitband"]:
        qemu_target.remove_bitband = True

    # Step 2b: pre-register AvatarPeripheral-subclass bp handlers as
    # forwarded memory ranges. This has to happen BEFORE the QEMU config
    # JSON is emitted so the avatar-rmemory hw regions land in the config
    # and QEMU creates the POSIX mqueues we'll listen on after launch.
    _preregister_avatar_peripherals(config, avatar)

    # Step 3: write QEMU configurable-machine JSON and assemble the command.
    os.makedirs(avatar.output_directory, exist_ok=True)
    avatar.save_config(
        file_name=qemu_target.qemu_config_file,
        config=qemu_target.generate_qemu_config(),
    )
    cmd_line = qemu_target.assemble_cmd_line()
    if print_qemu_command:
        print("QEMU Command")
        print(" ".join(cmd_line))
        sys.exit(0)

    # Step 4: spawn QEMU.
    out_path = f"{avatar.output_directory}/{target_name}_out.txt"
    err_path = f"{avatar.output_directory}/{target_name}_err.txt"
    log.info("Spawning QEMU (direct): %s", " ".join(cmd_line))
    qemu_out = open(out_path, "wb")
    qemu_err = open(err_path, "wb")
    qemu_proc = subprocess.Popen(cmd_line, stdout=qemu_out, stderr=qemu_err)

    # Step 5: connect QEMUBackend (GDB + QMP).
    backend = QEMUBackend(arch=config.machine.arch, gdb_port=gdb_port,
                          qmp_port=gdb_port + 1)
    backend._process = qemu_proc
    # Give QEMU a moment to open its listening sockets.
    time.sleep(0.5)
    backend.launch()

    # periph_server.start() needs qemu.avatar.output_directory; graft it on.
    backend.avatar = avatar

    # Step 6: apply PC/SP init (same rules as avatar2 path).
    config.initialize_target(backend)
    if config.machine.arch == "cortex-m3":
        backend.regs.cpsr = backend.regs.cpsr | 0x20
        backend.regs.sp = config.machine.init_sp
        # Set VTOR via avatar-qemu's QMP command so cortex-m's reset
        # behavior reads SP/PC from the correct vector table base.
        try:
            backend._qmp.execute(  # pylint: disable=protected-access
                "avatar-armv7m-set-vector-table-base",
                {"base": config.machine.vector_base, "num_cpu": 0},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to set VTOR via QMP: %s", exc)
    elif config.machine.init_sp is not None:
        backend.regs.sp = config.machine.init_sp

    # Step 7: register intercepts directly against the backend (no avatar2
    # watchmen — we dispatch stop events ourselves below).
    for intercept in config.intercepts:
        if intercept.bp_addr is not None:
            log.info("Registering Intercept: %s", intercept)
            intercepts.register_bp_handler(backend, intercept)

    # Step 7b: start the avatar-rmemory (MMIO-forwarding) listener in a
    # background thread. It opens the POSIX mqueue pair QEMU created for
    # each avatar-rmemory region and dispatches read/write requests to
    # the AvatarPeripheral instances we registered in step 2b.
    mmio_listener = _start_mmio_forwarding(avatar, qemu_target)

    # Optional: open a user-facing GDB proxy so an external gdb/lldb
    # can attach. When a user connects, the dispatch loop pauses until
    # they disconnect.
    gdb_proxy = None
    if gdb_server_port is not None and gdb_server_port >= 0:
        from halucinator.backends.gdb_proxy import GdbProxy
        backend._gdb_user_paused = threading.Event()
        gdb_proxy = GdbProxy(
            gdb_server_port, backend,
            pause_cb=backend._gdb_user_paused.set,
            resume_cb=backend._gdb_user_paused.clear,
        )
        gdb_proxy.start()
        print(f"GDB proxy listening on :{gdb_server_port} "
              f"(forwards to backend GDB stub)")

    # Step 8: start peripheral server and run its message consumption loop
    # in a background thread — the main thread owns the GDB dispatch loop.
    periph_server.start(rx_port, tx_port, backend)
    periph_thread = threading.Thread(
        target=periph_server.run_server, daemon=True, name="periph_server"
    )
    periph_thread.start()

    def _shutdown():
        if gdb_proxy is not None:
            gdb_proxy.stop()
        try:
            backend.shutdown()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
        if mmio_listener is not None:
            mmio_listener.stop()
        try:
            qemu_proc.terminate()
        except Exception:
            pass
        periph_server.stop()
        qemu_out.close()
        qemu_err.close()

    def _sigint(_sig, _frame):
        print(f"Halucinator Exiting with status {__HAL_EXIT_CODE}!")
        _shutdown()
        sys.exit(__HAL_EXIT_CODE)

    signal.signal(signal.SIGINT, _sigint)

    # Dispatch loop: run QEMU, on each stop dispatch to the matching handler.
    log.info("Letting QEMU Run (direct backend)")
    try:
        _qemu_backend_dispatch_loop(backend)
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown()


def _qemu_backend_dispatch_loop(backend):
    """
    Drive QEMU via GDB RSP: continue, wait for stop, dispatch handler.

    Runs until QEMU exits or stop() is called externally. If a GDB
    proxy is open and a user client is attached, pauses until the user
    disconnects — the two can't share the GDB RSP stream.
    """
    pause_evt = getattr(backend, "_gdb_user_paused", None)
    backend.cont()
    while True:
        if pause_evt is not None and pause_evt.is_set():
            # External GDB is driving the stub; idle until it goes away.
            pause_evt.wait()  # returns immediately since set; subtle:
            # actually we want to block *while* set. Invert with a loop.
            while pause_evt.is_set():
                threading.Event().wait(0.1)
            # Re-issue a continue — the user may have left the CPU halted.
            backend.cont()
        stop = backend._gdb.wait_for_stop(timeout=None)  # pylint: disable=protected-access
        if stop is None:
            continue
        raw_pc = backend.read_register("pc")
        pc = raw_pc & ~1  # mask Thumb bit for ARM
        bp_id = intercepts.addr2bp_lut.get(pc)
        if bp_id is None:
            log.debug("Stopped at 0x%x with no registered handler; continuing",
                      pc)
            backend.cont()
            continue

        info = intercepts.bp2handler_lut[bp_id]
        cls, method = info.cls, info.handler
        hal_stats.stats[bp_id]["count"] += 1
        hal_stats.write_on_update(
            "used_intercepts", hal_stats.stats[bp_id]["function"]
        )
        try:
            do_intercept, ret_value = method(cls, backend, pc)
        except Exception:
            log.exception("Error in bp_handler for 0x%x", pc)
            raise
        if do_intercept:
            hal_stats.write_on_update(
                "bypassed_funcs", hal_stats.stats[bp_id]["function"]
            )
            backend.execute_return(ret_value)  # calls backend.cont()
        else:
            backend.cont()


def _emulate_with_unicorn_backend(config, target_name, rx_port, tx_port):
    """
    In-process emulation via unicorn-engine. No subprocess, no GDB/QMP —
    firmware runs inside the Python process via UnicornBackend.

    Considerably faster than the QEMU paths for short-running firmware
    that doesn't need full hardware peripheral timing, but only supports
    ARM at the moment (UnicornBackend._ARCH_MAP).
    """
    from halucinator.backends.hal_backend import MemoryRegion
    from halucinator.backends.unicorn_backend import UnicornBackend

    arch = config.machine.arch
    from halucinator.backends.unicorn_backend import _ARCH_MAP
    if arch not in _ARCH_MAP:
        raise NotImplementedError(
            f"UnicornBackend has no mapping for arch={arch!r}. "
            f"Supported: {sorted(_ARCH_MAP.keys())!r}. "
            f"Use --emulator avatar2 or qemu instead."
        )

    outdir = os.path.join("tmp", target_name)
    os.makedirs(os.path.join(outdir, "logs"), exist_ok=True)
    hal_stats.set_filename(outdir + "/stats.yaml")

    backend = UnicornBackend(arch=arch)

    # Register memory regions from config, loading any firmware file bytes
    # into the region on add.
    for memory in config.memories.values():
        region = MemoryRegion(
            name=memory.name,
            base_addr=memory.base_addr,
            size=memory.size,
            permissions=memory.permissions or "rwx",
            file=memory.file,
        )
        log.info("Adding Memory: %s Addr: 0x%08x Size: 0x%08x",
                 memory.name, memory.base_addr, memory.size)
        backend.add_memory_region(region)

    backend.init()

    # periph_server references qemu.avatar.output_directory; bp_handlers
    # (e.g. zephyr_uart) reach for qemu.avatar.config to look up symbol
    # addresses. Graft a shim that exposes both.
    from types import SimpleNamespace
    backend.avatar = SimpleNamespace(output_directory=outdir, config=config)

    # Apply PC/SP init.
    if config.machine.entry_addr is not None:
        backend.regs.pc = config.machine.entry_addr
    if config.machine.arch == "cortex-m3":
        backend.regs.sp = config.machine.init_sp
        # Tell the backend where the vector table lives so inject_irq
        # can look up ISR addresses for NVIC-delivered interrupts.
        backend.set_vtor(config.machine.vector_base)
    elif config.machine.init_sp is not None:
        backend.regs.sp = config.machine.init_sp

    # Register intercepts — each becomes a UnicornBackend breakpoint.
    for intercept in config.intercepts:
        if intercept.bp_addr is not None:
            log.info("Registering Intercept: %s", intercept)
            intercepts.register_bp_handler(backend, intercept)

    # Start peripheral server on a background thread for zmq consumption.
    periph_server.start(rx_port, tx_port, backend)
    periph_thread = threading.Thread(
        target=periph_server.run_server, daemon=True, name="periph_server"
    )
    periph_thread.start()

    def _shutdown():
        try:
            backend.shutdown()
        except Exception:  # noqa: BLE001
            pass
        periph_server.stop()

    def _sigint(_sig, _frame):
        _shutdown()
        sys.exit(__HAL_EXIT_CODE)

    signal.signal(signal.SIGINT, _sigint)

    log.info("Letting Unicorn Run (direct backend)")
    try:
        _in_process_dispatch_loop(backend)
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown()


def _in_process_dispatch_loop(backend):
    """
    Drive any in-process HalBackend (UnicornBackend, GhidraBackend, and
    anything else whose cont() blocks until a breakpoint fires). Read
    PC after each halt, dispatch to the registered bp_handler, and
    resume. Exits cleanly when cont() returns at an address that has
    no registered handler — i.e. the firmware ran off the rails.
    """
    backend.cont()  # blocks until first breakpoint
    while True:
        pc = backend.read_register("pc") & ~1  # mask Thumb bit on ARM
        bp_id = intercepts.addr2bp_lut.get(pc)
        if bp_id is None:
            log.info("%s stopped at 0x%x with no handler; exiting",
                     type(backend).__name__, pc)
            return

        info = intercepts.bp2handler_lut[bp_id]
        cls, method = info.cls, info.handler
        hal_stats.stats[bp_id]["count"] += 1
        hal_stats.write_on_update(
            "used_intercepts", hal_stats.stats[bp_id]["function"]
        )
        try:
            do_intercept, ret_value = method(cls, backend, pc)
        except Exception:
            log.exception("Error in bp_handler for 0x%x", pc)
            raise
        if do_intercept:
            hal_stats.write_on_update(
                "bypassed_funcs", hal_stats.stats[bp_id]["function"]
            )
            backend.execute_return(ret_value)  # sets pc=lr, blocks until
                                                # next breakpoint
        else:
            # Not yet supported: single-step past bp then continue.
            # For now treat as terminal.
            log.warning("Non-intercept bp_handler return on unicorn "
                        "not yet supported; stopping.")
            return


def _renode_mmio_setup(config, backend, outdir):
    """Instantiate AvatarPeripheral bp handlers and register them with
    a TCP server that Renode's Python peripheral bridge scripts talk
    to. Returns the server (so the caller can stop it on shutdown) or
    None if no peripherals."""
    from halucinator.backends.renode_mmio import (
        RenodeMMIOServer, emit_repl_python_peripherals,
    )
    peripherals = []
    instances = []
    added = set()
    for intercept in config.intercepts:
        bp_cls = intercepts.get_bp_handler(intercept)
        if not issubclass(bp_cls.__class__, AvatarPeripheral):
            continue
        if bp_cls in added:
            continue
        added.add(bp_cls)
        name, addr, size, _ = bp_cls.get_mmio_info()
        # .repl identifiers must not start with a digit.
        repl_name = name if name and name[0].isalpha() else f"mmio_{addr:x}"
        peripherals.append((repl_name, addr, size))
        instances.append((addr, bp_cls))
        log.info("Adding Renode MMIO forwarding for %s "
                 "(name=%s addr=0x%x size=0x%x)",
                 bp_cls.__class__.__name__, repl_name, addr, size)
    if not peripherals:
        return None
    server = RenodeMMIOServer()
    port = server.start()
    for addr, bp_cls in instances:
        server.register(addr, bp_cls)
    extra_lines = emit_repl_python_peripherals(peripherals, outdir, port)
    # Attach the lines to the backend so _write_resc_script picks them up.
    backend._extra_repl_lines = extra_lines
    return server


def _emulate_with_renode_backend(config, target_name, rx_port, tx_port,
                                  gdb_server_port=None):
    """
    Direct-Renode path: spawn Antmicro Renode, generate a .resc from the
    config memories, drive register/memory/breakpoint access via GDB and
    machine setup via the Monitor TCP socket.
    """
    from halucinator.backends.hal_backend import MemoryRegion
    from halucinator.backends.renode_backend import (
        RenodeBackend, _ARCH_MAP as RENODE_ARCH_MAP,
    )

    arch = config.machine.arch
    if arch not in RENODE_ARCH_MAP:
        raise NotImplementedError(
            f"RenodeBackend has no mapping for arch={arch!r}. "
            f"Supported: {sorted(RENODE_ARCH_MAP.keys())!r}."
        )

    outdir = os.path.join("tmp", target_name)
    os.makedirs(os.path.join(outdir, "logs"), exist_ok=True)
    hal_stats.set_filename(outdir + "/stats.yaml")

    backend = RenodeBackend(arch=arch)
    # Stamp initial PC/SP into the .resc so Renode's CPU state matches
    # halucinator's expectation before the first continue — the GDB-level
    # register writes alone sometimes don't propagate to the CPU.
    backend.set_initial_state(
        pc=config.machine.entry_addr,
        sp=config.machine.init_sp,
    )
    # Renode's MappedMemory backs each region with host RAM, so a 512 MB
    # logger sink eats 512 MB of host RAM at startup and a region that
    # overlaps the ARMv7-M private peripheral bus (0xE0000000–0xE00FFFFF
    # where the NVIC lives) silently hangs Renode at platform-load.
    # Halucinator marks Python-emulated regions with
    # `emulate_required=True` (set when the YAML's `peripherals:` block
    # is parsed). For Renode we cap each such region to a stub size that
    # avoids both problems:
    #   * 256 MB if the region sits entirely below the PPB
    #     (covers e.g. STM32F4 peripherals at 0x40023800 RCC, GPIO etc.)
    #   * 4 KB if the original region would extend into / past the PPB
    #     (zephyr's logger2 region at 0xE0000000 — the firmware's data
    #     path is intercept-driven anyway, so 4 KB is enough sink).
    _RENODE_STUB_LARGE = 0x10000000   # 256 MB
    _RENODE_STUB_TINY = 0x1000         # 4 KB
    _PPB_BASE = 0xE0000000
    for memory in config.memories.values():
        size = memory.size
        if getattr(memory, "emulate_required", False):
            base = memory.base_addr
            if base >= _PPB_BASE or base + size > _PPB_BASE > base:
                cap = _RENODE_STUB_TINY
            else:
                cap = _RENODE_STUB_LARGE
            if size > cap:
                log.info(
                    "Capping emulated-peripheral region %s "
                    "(0x%x bytes -> 0x%x for Renode)",
                    memory.name, size, cap,
                )
                size = cap
        region = MemoryRegion(
            name=memory.name,
            base_addr=memory.base_addr,
            size=size,
            permissions=memory.permissions or "rwx",
            file=memory.file,
        )
        log.info("Adding Memory: %s Addr: 0x%08x Size: 0x%08x",
                 memory.name, memory.base_addr, size)
        backend.add_memory_region(region)

    # Pre-register AvatarPeripheral-subclass bp handlers as Python
    # peripheral bridge entries so they show up in the generated .repl.
    mmio_server = _renode_mmio_setup(config, backend, outdir)

    backend.launch(script_dir=outdir)

    from types import SimpleNamespace
    backend.avatar = SimpleNamespace(output_directory=outdir, config=config)

    # Skip config.initialize_target: PC/SP are already baked into the
    # .resc via cpu PC / cpu SP (set by RenodeBackend.set_initial_state
    # above). Sending an additional GDB 'G' packet at this point writes
    # all registers including ones Renode's cortex-m stub doesn't expose,
    # which seems to silently clear software breakpoints we just set.

    for intercept in config.intercepts:
        if intercept.bp_addr is not None:
            log.info("Registering Intercept: %s", intercept)
            intercepts.register_bp_handler(backend, intercept)

    gdb_proxy = None
    if gdb_server_port is not None and gdb_server_port >= 0:
        from halucinator.backends.gdb_proxy import GdbProxy
        backend._gdb_user_paused = threading.Event()
        gdb_proxy = GdbProxy(
            gdb_server_port, backend,
            pause_cb=backend._gdb_user_paused.set,
            resume_cb=backend._gdb_user_paused.clear,
        )
        gdb_proxy.start()
        print(f"GDB proxy listening on :{gdb_server_port} "
              f"(forwards to Renode GDB stub)")

    periph_server.start(rx_port, tx_port, backend)
    periph_thread = threading.Thread(
        target=periph_server.run_server, daemon=True, name="periph_server"
    )
    periph_thread.start()

    def _shutdown():
        if gdb_proxy is not None:
            gdb_proxy.stop()
        if mmio_server is not None:
            mmio_server.stop()
        try:
            backend.shutdown()
        except Exception:  # noqa: BLE001
            pass
        periph_server.stop()

    signal.signal(signal.SIGINT, lambda *_: (_shutdown(), sys.exit(0)))
    log.info("Letting Renode Run")
    try:
        _qemu_backend_dispatch_loop(backend)  # same GDB-based loop
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown()


def _emulate_with_ghidra_backend(config, target_name, rx_port, tx_port):
    """
    In-process Ghidra PCode emulation via pyghidra. Slower than Unicorn
    but broader arch coverage since PCode runs any language Ghidra
    supports (ARM, AArch64, MIPS, PPC32/64, RISC-V, …).
    """
    from halucinator.backends.hal_backend import MemoryRegion
    from halucinator.backends.ghidra_backend import (
        GhidraBackend, _LANGUAGE_MAP as GHIDRA_LANG_MAP,
    )

    arch = config.machine.arch
    if arch not in GHIDRA_LANG_MAP:
        raise NotImplementedError(
            f"GhidraBackend has no language mapping for arch={arch!r}. "
            f"Supported: {sorted(GHIDRA_LANG_MAP.keys())!r}."
        )

    outdir = os.path.join("tmp", target_name)
    os.makedirs(os.path.join(outdir, "logs"), exist_ok=True)
    hal_stats.set_filename(outdir + "/stats.yaml")

    backend = GhidraBackend(arch=arch)
    for memory in config.memories.values():
        region = MemoryRegion(
            name=memory.name,
            base_addr=memory.base_addr,
            size=memory.size,
            permissions=memory.permissions or "rwx",
            file=memory.file,
        )
        log.info("Adding Memory: %s Addr: 0x%08x Size: 0x%08x",
                 memory.name, memory.base_addr, memory.size)
        backend.add_memory_region(region)
    backend.init()

    from types import SimpleNamespace
    backend.avatar = SimpleNamespace(output_directory=outdir, config=config)

    if config.machine.entry_addr is not None:
        backend.regs.pc = config.machine.entry_addr
    if config.machine.init_sp is not None:
        backend.regs.sp = config.machine.init_sp

    for intercept in config.intercepts:
        if intercept.bp_addr is not None:
            log.info("Registering Intercept: %s", intercept)
            intercepts.register_bp_handler(backend, intercept)

    periph_server.start(rx_port, tx_port, backend)
    periph_thread = threading.Thread(
        target=periph_server.run_server, daemon=True, name="periph_server"
    )
    periph_thread.start()

    def _shutdown():
        try:
            backend.shutdown()
        except Exception:  # noqa: BLE001
            pass
        periph_server.stop()

    signal.signal(signal.SIGINT, lambda *_: (_shutdown(), sys.exit(0)))
    log.info("Letting Ghidra Run (direct backend)")
    try:
        _in_process_dispatch_loop(backend)  # same blocking-cont loop
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown()


class DebugShell:
    """IPython-based interactive shell for halucinator debugging."""

    def __init__(self, debugger, avatar):
        self.debugger = debugger
        self.avatar = avatar

    def start_prompt(self):
        import IPython
        IPython.embed(header="HALucinator Debug Shell", colors="neutral")


def debug_shell(debugger, avatar, shutdown: bool):
    """
    Launch an interactive debug shell. If shutdown=True, call debugger.shutdown()
    immediately and return. Otherwise, start the peripheral server in a background
    thread and open an IPython shell.
    """
    if shutdown:
        debugger.shutdown()
        return

    t = threading.Thread(target=run_server, args=(avatar,))
    t.daemon = True
    t.start()

    shell = DebugShell(debugger, avatar)
    shell.start_prompt()


def run_server(avatar):
    """Run the peripheral server until KeyboardInterrupt, then shut down avatar."""
    try:
        periph_server.run_server()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            periph_server.stop()
        except Exception:
            pass
        try:
            avatar.stop()
        except Exception:
            pass
        try:
            avatar.shutdown()
        except Exception:
            pass
        raise SystemExit(0)


def main():
    """
    Halucinator Main
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
        "--singlestep",
        default=False,
        const=True,
        nargs="?",
        help="Enables QEMU single stepping instructions",
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
        "-d",
        "--gdb_server_port",
        default=None,
        type=int,
        help="Port to run GDB Server port",
    )
    parser.add_argument(
        "-e", "--elf", default=None, help="Elf file, required to use recorder"
    )
    parser.add_argument(
        "--emulator",
        default=None,
        choices=["avatar2", "qemu", "unicorn", "renode", "ghidra"],
        help="Emulator backend to use (default: avatar2, or value from config emulator: key)",
    )
    parser.add_argument(
        "--print_qemu_command",
        action="store_true",
        default=None,
        help="Just print the QEMU Command",
    )
    parser.add_argument(
        "-q",
        "--qemu_args",
        nargs=argparse.REMAINDER,
        default=[],
        help="Additional arguments for QEMU",
    )

    args = parser.parse_args()

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
        sys.exit(-1)

    if config.elf_program is not None:
        args.qemu_args.append(f"-device loader,file={config.elf_program.elf_filename}")

    qemu_args = None
    if args.qemu_args:
        qemu_args = " ".join(args.qemu_args)

    # emulator key can come from YAML config options or CLI
    emulator = getattr(args, "emulator", None) or config.options.get("emulator", "avatar2")

    emulate_binary(
        config,
        args.name,
        args.log_blocks,
        args.rx_port,
        args.tx_port,
        elf_file=args.elf,
        gdb_port=args.gdb_port,
        singlestep=args.singlestep,
        qemu_args=qemu_args,
        gdb_server_port=args.gdb_server_port,
        print_qemu_command=args.print_qemu_command,
        emulator=emulator,
    )


if __name__ == "__main__":
    main()
