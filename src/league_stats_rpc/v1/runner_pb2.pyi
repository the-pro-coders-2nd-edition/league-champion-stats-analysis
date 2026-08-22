from league_stats_rpc.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class JobKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    JOB_KIND_UNSPECIFIED: _ClassVar[JobKind]
    JOB_KIND_ANALYZE: _ClassVar[JobKind]
    JOB_KIND_REFRESH: _ClassVar[JobKind]
    JOB_KIND_REGENERATE: _ClassVar[JobKind]
JOB_KIND_UNSPECIFIED: JobKind
JOB_KIND_ANALYZE: JobKind
JOB_KIND_REFRESH: JobKind
JOB_KIND_REGENERATE: JobKind

class JobPlayer(_message.Message):
    __slots__ = ("riot_id", "tagline")
    RIOT_ID_FIELD_NUMBER: _ClassVar[int]
    TAGLINE_FIELD_NUMBER: _ClassVar[int]
    riot_id: str
    tagline: str
    def __init__(self, riot_id: _Optional[str] = ..., tagline: _Optional[str] = ...) -> None: ...

class EnqueueJobRequest(_message.Message):
    __slots__ = ("puuid", "region", "match_id", "reason", "kind", "riot_id", "tagline", "player_slug", "players", "filter_champion", "filter_role", "min_games")
    PUUID_FIELD_NUMBER: _ClassVar[int]
    REGION_FIELD_NUMBER: _ClassVar[int]
    MATCH_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    RIOT_ID_FIELD_NUMBER: _ClassVar[int]
    TAGLINE_FIELD_NUMBER: _ClassVar[int]
    PLAYER_SLUG_FIELD_NUMBER: _ClassVar[int]
    PLAYERS_FIELD_NUMBER: _ClassVar[int]
    FILTER_CHAMPION_FIELD_NUMBER: _ClassVar[int]
    FILTER_ROLE_FIELD_NUMBER: _ClassVar[int]
    MIN_GAMES_FIELD_NUMBER: _ClassVar[int]
    puuid: str
    region: _common_pb2.Region
    match_id: str
    reason: str
    kind: JobKind
    riot_id: str
    tagline: str
    player_slug: str
    players: _containers.RepeatedCompositeFieldContainer[JobPlayer]
    filter_champion: str
    filter_role: str
    min_games: int
    def __init__(self, puuid: _Optional[str] = ..., region: _Optional[_Union[_common_pb2.Region, str]] = ..., match_id: _Optional[str] = ..., reason: _Optional[str] = ..., kind: _Optional[_Union[JobKind, str]] = ..., riot_id: _Optional[str] = ..., tagline: _Optional[str] = ..., player_slug: _Optional[str] = ..., players: _Optional[_Iterable[_Union[JobPlayer, _Mapping]]] = ..., filter_champion: _Optional[str] = ..., filter_role: _Optional[str] = ..., min_games: _Optional[int] = ...) -> None: ...

class EnqueueJobResponse(_message.Message):
    __slots__ = ("job_id",)
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    def __init__(self, job_id: _Optional[str] = ...) -> None: ...

class StreamJobProgressRequest(_message.Message):
    __slots__ = ("job_id",)
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    def __init__(self, job_id: _Optional[str] = ...) -> None: ...

class StageResult(_message.Message):
    __slots__ = ("job_id", "stage", "payload_json", "completed_at_unix", "error", "final", "detail", "current", "total")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    STAGE_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_JSON_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_AT_UNIX_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    FINAL_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    CURRENT_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    stage: _common_pb2.Stage
    payload_json: str
    completed_at_unix: int
    error: str
    final: bool
    detail: str
    current: int
    total: int
    def __init__(self, job_id: _Optional[str] = ..., stage: _Optional[_Union[_common_pb2.Stage, str]] = ..., payload_json: _Optional[str] = ..., completed_at_unix: _Optional[int] = ..., error: _Optional[str] = ..., final: _Optional[bool] = ..., detail: _Optional[str] = ..., current: _Optional[int] = ..., total: _Optional[int] = ...) -> None: ...

class PeerBaselineReadyRequest(_message.Message):
    __slots__ = ("request_id", "champion", "lane", "rank", "baseline_json", "error", "still_refining")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    CHAMPION_FIELD_NUMBER: _ClassVar[int]
    LANE_FIELD_NUMBER: _ClassVar[int]
    RANK_FIELD_NUMBER: _ClassVar[int]
    BASELINE_JSON_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    STILL_REFINING_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    champion: str
    lane: str
    rank: str
    baseline_json: str
    error: str
    still_refining: bool
    def __init__(self, request_id: _Optional[str] = ..., champion: _Optional[str] = ..., lane: _Optional[str] = ..., rank: _Optional[str] = ..., baseline_json: _Optional[str] = ..., error: _Optional[str] = ..., still_refining: _Optional[bool] = ...) -> None: ...
