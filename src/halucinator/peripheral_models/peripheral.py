# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
from __future__ import annotations

from typing import Any, Callable, TypeVar

CallableVar = TypeVar("CallableVar", bound=Callable[..., Any])


def requires_tx_map(method: CallableVar) -> CallableVar:
    '''
        Decorator which register class methods as requiring a tx map
    '''
    method.req_tx_map = True  # type: ignore
    return method


def requires_rx_map(method: CallableVar) -> CallableVar:
    '''
        Decorator which register class methods as requiring a rx map
    '''
    method.req_rx_map = True  # type: ignore
    return method


def requires_interrupt_map(method: CallableVar) -> CallableVar:
    '''
        Decorator which register class methods as requiring a interrupt map
    '''
    method.req_interrupt_map = True  # type: ignore
    return method
