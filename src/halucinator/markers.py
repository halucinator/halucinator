from __future__ import annotations

import functools
import logging
import os
from typing import Any, Callable, Mapping, Sequence, TypeVar, cast

log = logging.getLogger(__name__)


class KnownOrSuspectedBug(Exception):
    def __init__(self, description: str) -> None:
        super().__init__(description)


class KnownOrSuspectedUnusedCode(Exception):
    def __init__(self, description: str) -> None:
        super().__init__(description)


def get_should_stop_execution() -> bool:
    CONT_STRINGS = ["yes", "true", "1"]
    STOP_STRINGS = ["no", "false", "0"]

    var = os.getenv("HALUCINATOR_CONTINUE_AFTER_BUG", "0")
    if var not in CONT_STRINGS and var not in STOP_STRINGS:
        logging.warning(
            "Invalid setting for $HALUCINATOR_CONTINUE_AFTER_BUG: %s", var
        )

    return var in STOP_STRINGS


def BUG(description: str) -> None:
    logging.critical("Known or suspected bug reached: %s", description)
    if get_should_stop_execution():
        raise KnownOrSuspectedBug(description)


def UNUSED(description: str) -> None:
    logging.critical(
        "Known or suspected unused code was reached: %s", description
    )
    raise KnownOrSuspectedUnusedCode(description)


ACallable = TypeVar("ACallable", bound=Callable[..., Any])


def unused_function(func: ACallable) -> ACallable:
    @functools.wraps(func)
    def wrapper(*args: Sequence[Any], **kwargs: Mapping[str, Any]) -> Any:
        UNUSED(func.__name__)
        return func(*args, **kwargs)

    return cast(ACallable, wrapper)
