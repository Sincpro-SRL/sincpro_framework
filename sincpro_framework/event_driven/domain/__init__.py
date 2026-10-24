"""The ports and policies of event delivery: `Queue` (what a publisher puts into), `Broker`
and `Delivery` (what the queue wire needs of a message broker), and the delivery failure
policies.

    from sincpro_framework.event_driven.domain.failure import ParkAndContinue, RetryLater

    policy = RetryLater(then=ParkAndContinue())
    EventRelay(repository, BillingEvent, publisher, on_failure=policy)
"""
