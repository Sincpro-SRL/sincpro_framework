"""What a project holds to send and hear events: `Publisher` / `AsyncPublisher`,
`Subscriber` / `AsyncSubscriber`, and the outbox's `EventRelay` — facades over the services.

    relay = EventRelay(repository, source=BillingEvent, publisher=Publisher(queue))
    relay.run_once()      ->  RelayPass(read=3, delivered=3, …)
"""
