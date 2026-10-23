"""Who hears every outcome of the process: what `completions.subscribe(listener)` and
`failures.subscribe(listener)` add to."""

from sincpro_framework.bus_pipeline.listeners import Listeners

completions = Listeners()
"""The completions of every bus of this process."""

failures = Listeners()
"""The failures of every bus of this process."""
