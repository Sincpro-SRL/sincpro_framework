"""A name written in another case — the one word rule every wire, signal and reason derives
with, so `IssueInvoice` is `issue_invoice` on JSON-RPC, MCP and in a metric alike.

    snake("CreateQREconomico")      ->  "create_qr_economico"
    upper_snake("InvoiceNotFound")  ->  "INVOICE_NOT_FOUND"
    kebab("HTTPRequestLog")         ->  "http-request-log"
    pascal("sales_orders")          ->  "SalesOrders"

Context: a word starts at a capital after a lowercase letter or a digit, and at the last
capital of a run followed by a lowercase letter — so an acronym stays one word.
"""

import re

WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def snake(name: str) -> str:
    """A CamelCase, kebab-case or spaced name in snake_case — acronyms whole.

    snake("IssueInvoice")       ->  "issue_invoice"
    snake("CreateQREconomico")  ->  "create_qr_economico"
    snake("sincpro-billing")    ->  "sincpro_billing"
    """
    return WORD_BOUNDARY.sub("_", name).replace("-", "_").replace(" ", "_").lower()


def upper_snake(name: str) -> str:
    """A CamelCase name in UPPER_SNAKE — acronyms whole; what a class becomes as a `reason`.

    upper_snake("InvoiceNotFound")  ->  "INVOICE_NOT_FOUND"
    upper_snake("HTTPTimeout")      ->  "HTTP_TIMEOUT"
    """
    return WORD_BOUNDARY.sub("_", name).upper()


def kebab(name: str) -> str:
    """A CamelCase name in kebab-case — acronyms whole; what a REST path segment is.

    kebab("CommandIssueInvoice")  ->  "command-issue-invoice"
    kebab("HTTPRequest")          ->  "http-request"
    """
    return WORD_BOUNDARY.sub("-", name).lower()


def pascal(name: str) -> str:
    """A snake_case name in PascalCase — an inner capital is preserved.

    pascal("sales_orders")  ->  "SalesOrders"
    pascal("billing")       ->  "Billing"
    pascal("issueInvoice")  ->  "IssueInvoice"
    """
    return "".join(part[:1].upper() + part[1:] for part in name.split("_") if part)
