from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class AttachmentRef:
    kind: str
    path: str
    name: str
    transcript: str = ""
    size: int = 0
    media_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AttachmentRef":
        return cls(
            kind=str(data.get("kind", "file") or "file"),
            path=str(data.get("path", "") or ""),
            name=str(data.get("name", "") or ""),
            transcript=str(data.get("transcript", "") or ""),
            size=int(data.get("size", 0) or 0),
            media_key=str(data.get("media_key", "") or ""),
        )


@dataclass
class InboundMessage:
    message_id: int
    from_user_id: str
    to_user_id: str
    context_token: str
    text: str
    attachments: list[AttachmentRef]
    raw: dict[str, Any]


@dataclass
class WorkerEvent:
    event: str
    payload: dict[str, Any]
