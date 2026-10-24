"""The queues an event travels by — each a `Queue`: `SyncQueue` (in this call),
`BackgroundQueue` (another process), `RepositoryQueue` (kept for the relay), and behind their
extras `faststream.FastStreamQueue` and `aws.SnsQueue` / `SqsQueue` / `EventBridgeQueue`.

    Publisher(SyncQueue(Subscriber(planning))).publish(event)
"""
