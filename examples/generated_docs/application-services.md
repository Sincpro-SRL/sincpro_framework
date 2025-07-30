# Application Services

Business logic and application layer services.

### MakeTransaction

**Module:** `__main__`

Second layer of the framework, orchestration of features

**Methods:**

#### execute

```python
(self, dto: __main__.MakeTransactionCommand) -> __main__.MakeTransactionResponse
```

Execute transaction processing
Args:
    dto (MakeTransactionCommand): Data transfer object containing transaction details
Returns:
    MakeTransactionResponse: Response containing transaction details

