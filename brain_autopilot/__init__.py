"""Brain Autopilot: Continually learning autonomous verification and playtesting engine
for Web Apps, Mobile Apps (Expo/React Native/Flutter/ADB), and 3D/WebGL Apps.
"""

from __future__ import annotations

__version__ = "2.0.0"

from .kit import App, Harness, Option, Result, Status, Step

__all__ = [
    "__version__",
    "App",
    "Harness",
    "Option",
    "Result",
    "Status",
    "Step",
]
