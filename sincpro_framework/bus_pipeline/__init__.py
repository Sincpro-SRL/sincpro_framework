"""The pipeline every execution of a bus goes through, in three layers:

    interceptors/   control       wrap the handler; may veto, adjust or answer
    incomes/        observation   a use case begins, announced before the interceptors
    outcomes/       observation   a use case ends, announced after them

Control may change the answer; observation never does. A complete world — auth, caching — hangs on
the interceptors and stays in its own place. What is sent to an external backend (traces, metrics,
error reports) is not here: it is `sincpro_framework.observability`, which this package does not
import.
"""
