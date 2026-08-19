from league_stats_rpc.v1 import common_pb2 as _common_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class EnqueueJobRequest(_message.Message):
    __slots__ = ("puuid", "region", "match_id", "reason")
    PUUID_FIELD_NUMBER: _ClassVar[int]
    REGION_FIELD_NUMBER: _ClassVar[int]
    MATCH_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    puuid: str
    region: _common_pb2.Region
    match_id: str
    reason: str
    def __init__(self, puuid: _Optional[str] = ..., region: _Optional[_Union[_common_pb2.Region, str]] = ..., match_id: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

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
    __slots__ = ("job_id", "stage", "payload_json", "completed_at_unix")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    STAGE_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_JSON_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_AT_UNIX_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    stage: _common_pb2.Stage
    payload_json: str
    completed_at_unix: int
    def __init__(self, job_id: _Optional[str] = ..., stage: _Optional[_Union[_common_pb2.Stage, str]] = ..., payload_json: _Optional[str] = ..., completed_at_unix: _Optional[int] = ...) -> None: ...

class PeerBaselineReadyRequest(_message.Message):
    __slots__ = ("request_id", "champion", "lane", "rank", "baseline_json")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    CHAMPION_FIELD_NUMBER: _ClassVar[int]
    LANE_FIELD_NUMBER: _ClassVar[int]
    RANK_FIELD_NUMBER: _ClassVar[int]
    BASELINE_JSON_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    champion: str
    lane: str
    rank: str
    baseline_json: str
    def __init__(self, request_id: _Optional[str] = ..., champion: _Optional[str] = ..., lane: _Optional[str] = ..., rank: _Optional[str] = ..., baseline_json: _Optional[str] = ...) -> None: ...
