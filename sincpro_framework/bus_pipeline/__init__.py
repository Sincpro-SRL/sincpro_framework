"""The pipeline every execution of a bus goes through: what wraps the handler (`interceptors`) and
what is announced when it ends (`outcomes`). A complete world — auth, caching, observability —
hangs on it through the interceptors and stays in its own place."""
