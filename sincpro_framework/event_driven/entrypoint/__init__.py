"""What a project holds to send and hear events: `Publisher` / `AsyncPublisher` and
`Subscriber` / `AsyncSubscriber` — facades over the services.

    Publisher(queue).publish(event)                      ->  None
    Publisher(queue, forwarding=True).publish(event)     ->  Future[Forwarded]
"""
