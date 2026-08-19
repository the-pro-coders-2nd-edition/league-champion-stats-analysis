from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class Region(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    REGION_UNSPECIFIED: _ClassVar[Region]
    EUROPE: _ClassVar[Region]
    AMERICAS: _ClassVar[Region]
    ASIA: _ClassVar[Region]
    SEA: _ClassVar[Region]

class Stage(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    STAGE_UNSPECIFIED: _ClassVar[Stage]
    SUMMARY: _ClassVar[Stage]
    PERFORMANCE: _ClassVar[Stage]
    DEEP_DIVE: _ClassVar[Stage]
    CHAMPION: _ClassVar[Stage]
REGION_UNSPECIFIED: Region
EUROPE: Region
AMERICAS: Region
ASIA: Region
SEA: Region
STAGE_UNSPECIFIED: Stage
SUMMARY: Stage
PERFORMANCE: Stage
DEEP_DIVE: Stage
CHAMPION: Stage

class Ack(_message.Message):
    __slots__ = ("ok", "message")
    OK_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    ok: bool
    message: str
    def __init__(self, ok: _Optional[bool] = ..., message: _Optional[str] = ...) -> None: ...
