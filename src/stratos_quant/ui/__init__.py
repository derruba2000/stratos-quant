"""Gradio control board for Stratos Quant."""

from .app import build_app
from .controller import DashboardController

__all__ = ["DashboardController", "build_app"]
