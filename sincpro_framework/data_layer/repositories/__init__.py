"""The repositories that need no database: the `IRepository` and `INumbering` of `ddd` answered over
what the process holds — for tests, prototypes and one process."""

from sincpro_framework.data_layer.repositories.memory_numbering import MemoryNumbering
from sincpro_framework.data_layer.repositories.memory_repository import MemoryRepository

__all__ = ["MemoryNumbering", "MemoryRepository"]
