"""What an address of a transport raises."""

from sincpro_framework.exceptions import ProgrammingError


class InvalidAddress(ProgrammingError, ValueError):
    """An address no wire can reach — it names what was written and what to write instead."""
