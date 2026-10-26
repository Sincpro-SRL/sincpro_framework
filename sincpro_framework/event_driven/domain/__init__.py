"""The ports and policies of event delivery: `Queue` (what a publisher puts into), `Subscribes`
(what a queue hands an event to), `Broker` and `Delivery` (what the queue wire needs of a
message broker), `Envelope` (what travels with a message), `Publishes` and `RelayPass` (what the
relay hands to and answers), and the delivery failure policies.

    from sincpro_framework.event_driven.domain.failure import ParkAndContinue, RetryLater

    policy = RetryLater(then=ParkAndContinue())
    EventRelay(repository, BillingEvent, publisher, on_failure=policy)
"""
