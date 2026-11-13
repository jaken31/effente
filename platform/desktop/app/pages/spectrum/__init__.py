"""Spectrum page and the shared band chart.

    from app.pages.spectrum import SpectrumPage
    from app.pages.spectrum.band_chart import BandChart   # the Live page embeds this

Imports are lazy, so the band chart loads without the page.
"""

__all__ = ["BandChart", "SpectrumPage"]


def __getattr__(name):
    if name == "BandChart":
        from app.pages.spectrum.band_chart import BandChart

        return BandChart
    if name == "SpectrumPage":
        from app.pages.spectrum.page import SpectrumPage

        return SpectrumPage
    raise AttributeError(name)
