"""The vocabulary of executing a bounded context in another service.

    address        where it is hosted: `Scheme`, `HostedAt`, `HostedContext` — the settings declare it too
    payload        the message a call and its answer travel as
    transport      `Transport`, what every wire answers
    exceptions     what a failed call raises
    failure_codec  `FailureCodec`: an exception written by the host, raised as itself by the caller

Standard library and pydantic only.
"""
