from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class RequestBaselineRequest(_message.Message):
    __slots__ = ("champion", "lane", "rank")
    CHAMPION_FIELD_NUMBER: _ClassVar[int]
    LANE_FIELD_NUMBER: _ClassVar[int]
    RANK_FIELD_NUMBER: _ClassVar[int]
    champion: str
    lane: str
    rank: str
    def __init__(self, champion: _Optional[str] = ..., lane: _Optional[str] = ..., rank: _Optional[str] = ...) -> None: ...

class RequestBaselineResponse(_message.Message):
    __slots__ = ("request_id", "cached", "baseline_json", "error")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    CACHED_FIELD_NUMBER: _ClassVar[int]
    BASELINE_JSON_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    cached: bool
    baseline_json: str
    error: str
    def __init__(self, request_id: _Optional[str] = ..., cached: _Optional[bool] = ..., baseline_json: _Optional[str] = ..., error: _Optional[str] = ...) -> None: ...
