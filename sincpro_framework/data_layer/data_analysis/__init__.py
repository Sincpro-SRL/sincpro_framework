"""Data analysis: read a query once, and do everything else with what was read.

    cache = QueryCache()
    sales = cache.fetch_all(repository, InvoiceLine, posted)     # one read per page, never again
    sales.narrow({"field": "journal", "operator": "=", "value": "SAL"})   # no read at all
    sales.to_parquet()                                           # to a client
    polars.DataFrame(sales)                                      # to pandas, polars, DuckDB …

A utility, not an engine: it holds the rows a `Criteria` answered as a `DataFrame`, continues a
read page by page, answers a narrower filter from a complete read, and hands the rows on — as
Parquet or an Arrow stream to a client, through the Arrow PyCapsule interface to any dataframe
library. The computing is theirs.

The core needs nothing; Parquet and Arrow need pyarrow, the `[data-analysis]` extra.
"""

from sincpro_framework.data_layer.data_analysis.cache import QueryCache
from sincpro_framework.data_layer.data_analysis.frame import (
    DataFrame,
    NotComplete,
    SchemaMismatch,
)

__all__ = ["DataFrame", "NotComplete", "QueryCache", "SchemaMismatch"]
