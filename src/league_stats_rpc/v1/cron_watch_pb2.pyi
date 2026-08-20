from league_stats_rpc.v1 import common_pb2 as _common_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class RegisterAccountRequest(_message.Message):
    __slots__ = ("puuid", "region")
    PUUID_FIELD_NUMBER: _ClassVar[int]
    REGION_FIELD_NUMBER: _ClassVar[int]
    puuid: str
    region: _common_pb2.Region
    def __init__(self, puuid: _Optional[str] = ..., region: _Optional[_Union[_common_pb2.Region, str]] = ...) -> None: ...

class ForceRefreshRequest(_message.Message):
    __slots__ = ("puuid",)
    PUUID_FIELD_NUMBER: _ClassVar[int]
    puuid: str
    def __init__(self, puuid: _Optional[str] = ...) -> None: ...

class WatchUpdatesRequest(_message.Message):
    __slots__ = ("puuid",)
    PUUID_FIELD_NUMBER: _ClassVar[int]
    puuid: str
    def __init__(self, puuid: _Optional[str] = ...) -> None: ...

class WelcomeBackUpdate(_message.Message):
    __slots__ = ("puuid", "new_match_id", "match_summary_json", "detected_at_unix")
    PUUID_FIELD_NUMBER: _ClassVar[int]
    NEW_MATCH_ID_FIELD_NUMBER: _ClassVar[int]
    MATCH_SUMMARY_JSON_FIELD_NUMBER: _ClassVar[int]
    DETECTED_AT_UNIX_FIELD_NUMBER: _ClassVar[int]
    puuid: str
    new_match_id: str
    match_summary_json: str
    detected_at_unix: int
    def __init__(self, puuid: _Optional[str] = ..., new_match_id: _Optional[str] = ..., match_summary_json: _Optional[str] = ..., detected_at_unix: _Optional[int] = ...) -> None: ...
