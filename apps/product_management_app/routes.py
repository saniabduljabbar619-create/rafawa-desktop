# apps/product_management_app/routes.py
from typing import Dict, Callable
from PySide2.QtWidgets import QWidget

class Router:
    def __init__(self):
        self._factories: Dict[str, Callable[[], QWidget]] = {}

    def register(self, route: str, factory: Callable[[], QWidget]) -> None:
        self._factories[route] = factory

    def resolve(self, route: str) -> QWidget:
        if route not in self._factories:
            raise KeyError(f"Route not registered: {route}")
        return self._factories[route]()
