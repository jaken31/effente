"""Thermal page (optional sensor).

    from app.pages.thermal import ThermalPage
"""

__all__ = ["ThermalPage"]


def __getattr__(name):
    if name == "ThermalPage":
        from app.pages.thermal.page import ThermalPage

        return ThermalPage
    raise AttributeError(name)
