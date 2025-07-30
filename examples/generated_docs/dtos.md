# DTOs (Data Transfer Objects)

Data models including commands and responses for the framework.

### MakeTransactionCommand

**Module:** `__main__`

MakeTransactionCommand

**Fields:**

- `card_number` (<class 'str'>) - Required
  - Default: `PydanticUndefined`
- `amount` (<class 'float'>) - Required
  - Default: `PydanticUndefined`
- `merchant_id` (<class 'str'>) - Required
  - Default: `PydanticUndefined`


### ValidateCommand

**Module:** `__main__`

ValidateCommand

**Fields:**

- `card_number` (<class 'str'>) - Required
  - Default: `PydanticUndefined`
- `cvv` (<class 'str'>) - Required
  - Default: `PydanticUndefined`


### RefundCommand

**Module:** `__main__`

RefundCommand

**Fields:**

- `transaction_id` (<class 'str'>) - Required
  - Default: `PydanticUndefined`
- `amount` (<class 'float'>) - Required
  - Default: `PydanticUndefined`
- `reason` (<class 'str'>) - Required
  - Default: `PydanticUndefined`


### PaymentCommand

**Module:** `__main__`

PaymentCommand

**Fields:**

- `card_number` (<class 'str'>) - Required
  - Default: `PydanticUndefined`
- `amount` (<class 'float'>) - Required
  - Default: `PydanticUndefined`
- `merchant_id` (<class 'str'>) - Required
  - Default: `PydanticUndefined`

