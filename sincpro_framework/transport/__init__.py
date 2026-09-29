"""The mechanics of each technology, exposing nothing — what the entrypoints and remote execution
both stand on (PRD_15 §0.1).

    transport.failures   the one classification of failures every wire encodes its own way
    transport.grpc       a gRPC server, health, request context and credentials in metadata

Context: an entrypoint publishes a contract to others; remote execution keeps a codebase whole
across machines. Neither imports the other, and this package imports neither — only the
application layer. Each technology module imports its library at module level: importing it is
already "I want to speak this technology".
"""
