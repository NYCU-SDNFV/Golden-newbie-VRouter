from collections.abc import Callable
from typing import Protocol, TypeVar

_T = TypeVar("_T")
_T_co = TypeVar("_T_co", covariant=True)

class _SpawnedThread(Protocol[_T_co]):
    def wait(self) -> _T_co | None: ...

def spawn(func: Callable[[], _T], *, raise_error: bool = False) -> _SpawnedThread[_T]: ...
def sleep(seconds: float = 0) -> None: ...
