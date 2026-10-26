"""The operations behind what a project holds: `EventPublication` puts a fact into a queue,
`EventDispatch` hands an event to the buses that hear it, `EventRelaying` runs one pass of the
outbox. The entrypoint's `Publisher`, `Subscriber` and `EventRelay` delegate to them.
"""
