"""The ports of event delivery and what they report: `Queue` (what a publisher puts into),
`Subscribes` (what a queue hands an event to), `Broker` and `Delivery` (what the queue wire needs
of a message broker), `Envelope` (what travels with a message), and `Forwarded` /
`ForwardingHook` (how a forwarded message went, and who hears it).

    class Audited(ForwardingHook):
        def after_delivery(self, message, forwarded): ...

    Publisher(queue, forwarding=Forwarding(hooks=[Audited()]))
"""
