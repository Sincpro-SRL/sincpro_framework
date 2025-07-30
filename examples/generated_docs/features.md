# Features

Framework features and capabilities.

### ValidateFeature

**Module:** `__main__`

Feature to validate payment card details

**Methods:**

#### execute

```python
(self, dto: __main__.ValidateCommand) -> __main__.ValidateResponse
```

Execute card validation
Args:
    dto (ValidateCommand): Data transfer object containing card details
Returns:
    ValidateResponse: Response indicating if the card is valid


### RefundFeature

**Module:** `__main__`

Feature to handle refund processing

**Methods:**

#### execute

```python
(self, dto: __main__.RefundCommand) -> __main__.RefundResponse
```

Execute refund processing
Args:
    dto (RefundCommand): Data transfer object containing refund details
Returns:
    RefundResponse: Response containing refund details


### PaymentFeature

**Module:** `__main__`

Feature to handle payment processing

**Methods:**

#### execute

```python
(self, dto: __main__.PaymentCommand) -> __main__.PaymentResponse
```

Execute payment processing
Args:
    dto (PaymentCommand): Data transfer object containing payment details
Returns:
    PaymentResponse: Response containing transaction details

