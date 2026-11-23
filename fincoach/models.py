"""Core domain dataclasses."""

from dataclasses import dataclass, field


@dataclass
class Source:
    id: int | None = None
    kind: str = ""
    filename: str = ""
    mime: str = ""
    size: int = 0
    sha256: str = ""
    created_at: str = ""
    status: str = "pending"
    error: str = ""
    txn_count: int = 0
    rejected_count: int = 0


@dataclass
class Transaction:
    id: int | None = None
    source_id: int | None = None
    date: str = ""
    description: str = ""
    merchant: str = ""
    amount: float = 0.0
    currency: str = "INR"
    type: str = "other"
    category: str = ""
    origin: str = "verified"
    note: str = ""
    hash: str = ""
    created_at: str = ""


@dataclass
class CategoryRule:
    id: int | None = None
    pattern: str = ""
    category: str = ""
    priority: int = 0


@dataclass
class Recurring:
    id: int | None = None
    merchant: str = ""
    amount: float = 0.0
    frequency: str = "monthly"
    next_date: str = ""
    category: str = ""


@dataclass
class Goal:
    id: int | None = None
    name: str = ""
    target: float = 0.0
    current: float = 0.0
    deadline: str = ""
    created_at: str = ""
    contribs: list = field(default_factory=list)


@dataclass
class Profile:
    id: int | None = None
    name: str = ""
    data: str = "{}"
    created_at: str = ""
