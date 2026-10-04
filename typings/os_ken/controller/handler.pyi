from collections.abc import Callable
from typing import ParamSpec, TypeVar

_P = ParamSpec("_P")
_R = TypeVar("_R")
CONFIG_DISPATCHER: str
MAIN_DISPATCHER: str

def set_ev_cls(
    ev_cls: type[object] | list[type[object]],
    dispatchers: str | list[str] | None = None,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]: ...
