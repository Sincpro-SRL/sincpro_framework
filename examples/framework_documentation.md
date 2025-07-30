# payment-system - Documentación Automática

> 🤖 Generado automáticamente el 2025-07-29 23:40:47 por andru1236

## 📊 Resumen del Framework

- **DTOs (Pydantic):** 4 modelos
- **Funciones de Dependencias:** 1
- **Objetos de Dependencias:** 2
- **Funciones Middleware:** 0
- **Objetos Middleware:** 1
- **Features:** 3
- **Application Services:** 1

---

# 📋 Data Transfer Objects (DTOs)

Modelos Pydantic con validación automática y schemas JSON

**Total de modelos:** 4

## MakeTransactionCommand

**Módulo:** `__main__`

MakeTransactionCommand

**Herencia:** DataTransferObject

### Campos:

- **card_number** (`<class 'str'>`) - ✅ Requerido
  - Default: `PydanticUndefined`
- **amount** (`<class 'float'>`) - ✅ Requerido
  - Default: `PydanticUndefined`
- **merchant_id** (`<class 'str'>`) - ✅ Requerido
  - Default: `PydanticUndefined`

### Schema JSON:
```json
{'description': 'MakeTransactionCommand', 'properties': {'card_number': {'title': 'Card Number', 'type': 'string'}, 'amount': {'title': 'Amount', 'type': 'number'}, 'merchant_id': {'title': 'Merchant Id', 'type': 'string'}}, 'required': ['card_number', 'amount', 'merchant_id'], 'title': 'MakeTransactionCommand', 'type': 'object'}
```

## ValidateCommand

**Módulo:** `__main__`

ValidateCommand

**Herencia:** DataTransferObject

### Campos:

- **card_number** (`<class 'str'>`) - ✅ Requerido
  - Default: `PydanticUndefined`
- **cvv** (`<class 'str'>`) - ✅ Requerido
  - Default: `PydanticUndefined`

### Schema JSON:
```json
{'description': 'ValidateCommand', 'properties': {'card_number': {'title': 'Card Number', 'type': 'string'}, 'cvv': {'title': 'Cvv', 'type': 'string'}}, 'required': ['card_number', 'cvv'], 'title': 'ValidateCommand', 'type': 'object'}
```

## RefundCommand

**Módulo:** `__main__`

RefundCommand

**Herencia:** DataTransferObject

### Campos:

- **transaction_id** (`<class 'str'>`) - ✅ Requerido
  - Default: `PydanticUndefined`
- **amount** (`<class 'float'>`) - ✅ Requerido
  - Default: `PydanticUndefined`
- **reason** (`<class 'str'>`) - ✅ Requerido
  - Default: `PydanticUndefined`

### Schema JSON:
```json
{'description': 'RefundCommand', 'properties': {'transaction_id': {'title': 'Transaction Id', 'type': 'string'}, 'amount': {'title': 'Amount', 'type': 'number'}, 'reason': {'title': 'Reason', 'type': 'string'}}, 'required': ['transaction_id', 'amount', 'reason'], 'title': 'RefundCommand', 'type': 'object'}
```

## PaymentCommand

**Módulo:** `__main__`

PaymentCommand

**Herencia:** DataTransferObject

### Campos:

- **card_number** (`<class 'str'>`) - ✅ Requerido
  - Default: `PydanticUndefined`
- **amount** (`<class 'float'>`) - ✅ Requerido
  - Default: `PydanticUndefined`
- **merchant_id** (`<class 'str'>`) - ✅ Requerido
  - Default: `PydanticUndefined`

### Schema JSON:
```json
{'description': 'PaymentCommand', 'properties': {'card_number': {'title': 'Card Number', 'type': 'string'}, 'amount': {'title': 'Amount', 'type': 'number'}, 'merchant_id': {'title': 'Merchant Id', 'type': 'string'}}, 'required': ['card_number', 'amount', 'merchant_id'], 'title': 'PaymentCommand', 'type': 'object'}
```

# 🔌 Dependency Injection System

Funciones y objetos del sistema de inyección de dependencias

## Funciones de Dependencias

### check_health

**Módulo:** `__main__`
**Signature:** `()`


Health check function for the framework

## Objetos de Dependencias

### Database_0

**Clase:** `Database`
**Módulo:** `__main__`


Database class to handle transactions

**Representación:** `<__main__.Database object at 0x7f145c1c48c0>`

**Métodos públicos:**

- **get_transaction**`(tx_id)`
- **save_transaction**`(data)`

### VisaAdapter_1

**Clase:** `VisaAdapter`
**Módulo:** `__main__`


Adapter for Visa payment processing

**Representación:** `<__main__.VisaAdapter object at 0x7f143f078650>`

**Métodos públicos:**

- **check_status**`(tx_id)`
- **make_request**`(card_data)`

# 🔄 Middleware System

Funciones y objetos middleware del framework

## Objetos Middleware

### ValidatePayment_0

**Clase:** `ValidatePayment`
**Módulo:** `__main__`


Middleware to validate payment data

**Representación:** `<__main__.ValidatePayment object at 0x7f143f0787a0>`

**Herencia:** Middleware → Protocol → Generic

# ⚡ Framework Features

Características y funcionalidades principales del framework

### ValidateFeature_0

**Clase:** `ValidateFeature`
**Módulo:** `__main__`


Feature to validate payment card details

**Representación:** `<__main__.ValidateFeature object at 0x7f143f19c6e0>`

**Herencia:** Feat → Feature → ABC

**Atributos públicos:**

- **database** (`Database`): `<__main__.Database object at 0x7f145c1c48c0>`
- **visa_adapter** (`VisaAdapter`): `<__main__.VisaAdapter object at 0x7f143f078650>`

**Métodos públicos:**

- **execute**`(dto: __main__.ValidateCommand) -> __main__.ValidateResponse`
  - Execute card validation
Args:
    dto (ValidateCommand): Data transfer object containing card details
Returns:
    ValidateResponse: Response indicating if the card is valid
- **health_check**`()`
  - Health check function for the framework

### RefundFeature_1

**Clase:** `RefundFeature`
**Módulo:** `__main__`


Feature to handle refund processing

**Representación:** `<__main__.RefundFeature object at 0x7f143ed17a40>`

**Herencia:** Feat → Feature → ABC

**Atributos públicos:**

- **database** (`Database`): `<__main__.Database object at 0x7f145c1c48c0>`
- **visa_adapter** (`VisaAdapter`): `<__main__.VisaAdapter object at 0x7f143f078650>`

**Métodos públicos:**

- **execute**`(dto: __main__.RefundCommand) -> __main__.RefundResponse`
  - Execute refund processing
Args:
    dto (RefundCommand): Data transfer object containing refund details
Returns:
    RefundResponse: Response containing refund details
- **health_check**`()`
  - Health check function for the framework

### PaymentFeature_2

**Clase:** `PaymentFeature`
**Módulo:** `__main__`


Feature to handle payment processing

**Representación:** `<__main__.PaymentFeature object at 0x7f143ed16c30>`

**Herencia:** Feat → Feature → ABC

**Atributos públicos:**

- **database** (`Database`): `<__main__.Database object at 0x7f145c1c48c0>`
- **visa_adapter** (`VisaAdapter`): `<__main__.VisaAdapter object at 0x7f143f078650>`

**Métodos públicos:**

- **execute**`(dto: __main__.PaymentCommand) -> __main__.PaymentResponse`
  - Execute payment processing
Args:
    dto (PaymentCommand): Data transfer object containing payment details
Returns:
    PaymentResponse: Response containing transaction details
- **health_check**`()`
  - Health check function for the framework

# 🏢 Application Services

Servicios de la capa de aplicación del framework

### MakeTransaction_0

**Clase:** `MakeTransaction`
**Módulo:** `__main__`


Second layer of the framework, orchestration of features

**Representación:** `<__main__.MakeTransaction object at 0x7f145c1c4f80>`

**Herencia:** Services → ApplicationService → ABC

**Atributos públicos:**

- **database** (`Database`): `<__main__.Database object at 0x7f145c1c48c0>`
- **feature_bus** (`FeatureBus`): `<sincpro_framework.bus.FeatureBus object at 0x7f145c1c60c0>`
- **visa_adapter** (`VisaAdapter`): `<__main__.VisaAdapter object at 0x7f143f078650>`

**Métodos públicos:**

- **execute**`(dto: __main__.MakeTransactionCommand) -> __main__.MakeTransactionResponse`
  - Execute transaction processing
Args:
    dto (MakeTransactionCommand): Data transfer object containing transaction details
Returns:
    MakeTransactionResponse: Response containing transaction details
- **health_check**`()`
  - Health check function for the framework
