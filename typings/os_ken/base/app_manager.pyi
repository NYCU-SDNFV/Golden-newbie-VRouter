from logging import Logger
from typing import ClassVar

class OSKenApp:
    OFP_VERSIONS: ClassVar[list[int] | None]
    logger: Logger
    name: str
    def __init__(self, *_args: object, **_kwargs: object) -> None: ...
