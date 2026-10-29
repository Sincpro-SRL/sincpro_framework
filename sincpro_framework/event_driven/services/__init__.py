"""The operations behind what a project holds: `EventPublication` puts a message into a queue,
`Forwarding` holds a publisher's messages and forwards them in order, `EventDispatch` hands an
event to the buses that hear it. The entrypoint's `Publisher` and `Subscriber` delegate to them.
"""
