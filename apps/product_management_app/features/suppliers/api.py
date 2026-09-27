# apps/product_management_app/features/suppliers/api.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from shared.api.client import ApiClient


client = ApiClient()


def list_suppliers() -> list[dict]:
    return client.get("/api/suppliers")


def create_supplier(payload: dict) -> dict:
    return client.post("/api/suppliers", json=payload)
