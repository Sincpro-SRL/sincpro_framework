"""Who hears every outcome of the process: what `completion_listeners.subscribe(listener)` and
`failure_listeners.subscribe(listener)` add to."""

from sincpro_framework.bus_pipeline.listeners import Listeners

completion_listeners = Listeners()
"""The completions of every bus of this process."""

failure_listeners = Listeners()
"""The failures of every bus of this process."""
