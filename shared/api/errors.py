# shared/api/errors.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ApiError(Exception):
    status_code: int
    message: str
    details: Optional[object] = None

    def __str__(self) -> str:
        return f"ApiError({self.status_code}): {self.message}"
