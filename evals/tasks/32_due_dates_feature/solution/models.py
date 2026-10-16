
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class Todo:
    id: int
    title: str
    done: bool = False
    due: Optional[date] = None
