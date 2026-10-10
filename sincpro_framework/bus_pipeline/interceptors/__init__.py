"""Interceptors: code that runs around one use case, from outside it.

@billing.interceptor(CommandCreateInvoice)
def credit_check(dto: CommandCreateInvoice, call_next: CallNext[ResponseCreateInvoice]):
    return call_next(dto)
"""

from sincpro_framework.bus_pipeline.interceptors.interceptor import CallNext, Interceptor

__all__ = ["CallNext", "Interceptor"]
