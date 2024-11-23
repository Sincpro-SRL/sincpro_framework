# 🚀 Sincpro Framework: Application Layer Framework within Hexagonal Architecture

## ⚡ Quick Start

Here's a quick example to get you started with the Sincpro Framework:

### 🏁 Quick Example

```python
from sincpro_framework import UseFramework, Feature, DataTransferObject

# 1. Initialize the framework
framework = UseFramework("cybersource")

# 2. Add Dependencies (Example dependencies)
from sincpro_framework import Database

db = Database()
framework.add_dependency("db", db)

# 3. Error Handler (Optional, use the built-in error handling feature)
framework.set_global_error_handler(lambda e: print(f"Error: {e}"))


# 4. Create a Use Case with DTOs
class GreetingParams(DataTransferObject):
    name: str


@framework.feature(GreetingParams)
class GreetingFeature(Feature):
    def execute(self, dto: GreetingParams) -> str:
        self.db.store(f"Greeting {dto.name}")
        return f"Hello, {dto.name}!"


# 5. Execute the Use Case
if __name__ == "__main__":
    # Create an instance of the parameter DTO
    greeting_dto = GreetingParams(name="Alice")
    # Execute the feature
    result = framework(greeting_dto)
    print(result)  # Output: Hello, Alice!
```

Now you are ready to explore more complex use cases! 🚀

## 📑 Table of Contents

1. [Overview of Hexagonal Architecture](#overview-of-hexagonal-architecture)
    - [Key Layers of Hexagonal Architecture](#key-layers-of-hexagonal-architecture)
    - [Why Use a Unified Bus Pattern?](#why-use-a-unified-bus-pattern)
2. [Key Features of the Sincpro Framework](#key-features-of-the-sincpro-framework)
    - [DTO Validation with Pydantic](#dto-validation-with-pydantic)
    - [Dependency Injection](#dependency-injection)
    - [Inversion of Control (IoC)](#inversion-of-control-ioc)
    - [Error Handling at Different Levels](#error-handling-at-different-levels)
    - [Bus Pattern for Component Communication](#bus-pattern-for-component-communication)
    - [Decoupled Logic Execution](#decoupled-logic-execution)
    - [Application Service Orchestration](#application-service-orchestration)
    - [IDE Support with Typing](#ide-support-with-typing)
3. [Features vs. Application Service](#features-vs-application-service)
4. [Example Usage for a Payment Gateway](#example-usage-for-a-payment-gateway)
    - [Configuring the Framework](#configuring-the-framework)
    - [Best Practices for Imports](#best-practices-for-imports)
    - [Sample Configuration in ](#sample-configuration-in-init-py)[`__init__.py`](#sample-configuration-in-init-py)
5. [Creating a Feature](#creating-a-feature)
    - [Example of Creating a Feature](#example-of-creating-a-feature)
6. [Creating an Application Service](#creating-an-application-service)
    - [Example of Creating an Application Service](#example-of-creating-an-application-service)
7. [Executing a Use Case](#executing-a-use-case)
    - [Example of Executing a Use Case](#example-of-executing-a-use-case)
8. [Summary](#summary)
9. [Configuration or settings](#variables)
10. [Variables](#variables)

## 🔍 Overview of Hexagonal Architecture

Hexagonal Architecture, also known as **Ports and Adapters**, is an architectural approach that aims to decouple core
business logic from external dependencies. It organizes the system into distinct layers: domain, application, and
infrastructure, enhancing maintainability, scalability, and adaptability.

### 🏗️ Key Layers of Hexagonal Architecture

- **Core Domain**: This layer encapsulates essential entities, value objects, and domain services representing the core
  business rules and behaviors. It is kept strictly independent from infrastructure concerns, preserving business logic
  integrity.
- **Application Layer**: Orchestrates user requests, processes domain responses, and mediates interactions between
  domain and external systems to ensure effective workflow execution.
- **Infrastructure Layer**: Contains adapters for interacting with databases, APIs, messaging systems, and other
  services. It handles data transformation to ensure compatibility with the domain and application layers.

### 🤔 Why Use a Unified Bus Pattern?

The Sincpro Framework adopts a **unified bus pattern** as a single point of entry for managing use cases, dependencies,
and services within a bounded context. This simplifies the architecture by encapsulating all requirements of a given
context, ensuring a clear and consistent structure.

Using a unified bus allows developers to access all dependencies through a single environment, eliminating the need for
repeated imports or initialization. This approach ensures each bounded context is self-sufficient, independently
scalable, and minimizes coupling while enhancing modularity.

## 🔑 Key Features of the Sincpro Framework

The Sincpro Framework follows hexagonal architecture principles, promoting modularity, scalability, and development
efficiency. Here are its core features:

### ✅ DTO Validation with Pydantic

- Utilizes **Pydantic** to validate Data Transfer Objects (DTOs).
- Ensures only well-structured data is allowed into core business logic, reducing errors and maintaining data integrity.

### 🧩 Dependency Injection

- Facilitates integration of user-defined dependencies, promoting modular design.
- Enhances unit testing by allowing easy mocking or replacement of dependencies.

### 🔄 Inversion of Control (IoC)

- Automates the instantiation and configuration of components, reducing boilerplate code.
- Encourages loose coupling, making systems more adaptable and maintainable.

### ⚠️ Error Handling at Different Levels

- Provides centralized error handling at multiple levels: **global**, **Service Application**, and **Feature** levels.
- Ensures consistent error management, improving overall reliability.

### 🚌 Bus Pattern for Component Communication

- Implements a bus mechanism to facilitate communication between **Feature** and **ApplicationService** components.
- Decouples component interactions, resulting in more flexible and scalable business logic.

### 🧩 Decoupled Logic Execution

- Supports independent execution of use cases through the **Feature** component, promoting separation of concerns.
- For example, a user registration workflow can be broken down into steps like input validation, profile creation, and
  email notification.

### 🎻 Application Service Orchestration

- Uses a **feature bus** to orchestrate multiple features into complex business workflows (e.g., customer onboarding).
- Integrates smaller use cases into cohesive flows to manage entire business processes effectively.

### 💻 IDE Support with Typing

- Uses type hints to enhance code quality and support features like autocompletion and type checking.
- Improves development efficiency and reliability.

## ⚙️ Features vs. Application Service

- **Feature**: Represents a discrete, self-contained use case focused on specific functionality, easy to develop and
  maintain.
- **ApplicationService**: Orchestrates multiple features for broader business objectives, providing reusable components
  and workflows.

## 💳 Example Usage for a Payment Gateway

The following example shows how to configure the Sincpro Framework for a payment gateway integration, such as
CyberSource. It is recommended to name the framework instance to clearly represent the bounded context it serves.

### 🔧 Configuring the Framework

To set up the Sincpro Framework, configuration should be performed at the application layer within the `use_cases`
directory of each bounded context.

```plaintext
sincpro_payments_sdk/
├── pyproject.toml
├── README.md
├── apps/
│   ├── cybersource/
│   │   ├── adapters/
│   │   │   ├── cybersource_rest_api_adapter.py
│   │   │   └── __init__.py
│   │   ├── domain/
│   │   │   ├── card.py
│   │   │   ├── customer.py
│   │   │   └── __init__.py
│   │   ├── infrastructure/
│   │   │   ├── logger.py
│   │   │   ├── aws_services.py
│   │   │   ├── orm.py
│   │   │   └── __init__.py
│   │   └── use_cases/
│   │       ├── tokenization/
│   │       │   ├── new_tokenization_feature.py
│   │       │   └── __init__.py
│   │       ├── payments/
│   │       │   ├── token_and_payment_service.py
│   │       │   └── __init__.py
│   │       └── __init__.py
│   ├── qr/
│   ├── sms_payment/
│   ├── bank_api/
│   ├── online_payment_gateway/
│   └── paypal_integration/
└── tests
```

### 📋 Best Practices for Imports

Each use case should import both the **DTO for input parameters** and the **DTO for responses** to maintain clarity and
consistency.

### 📝 Sample Configuration in `__init__.py`

```python
from typing import Type

from sincpro_framework import Feature as _Feature
from sincpro_framework import UseFramework as _UseFramework
from sincpro_framework import ApplicationService as _ApplicationService

from sincpro_payments_sdk.apps.cybersource.adapters.cybersource_rest_api_adapter import (
    ESupportedCardType,
    TokenizationAdapter,
)
from sincpro_payments_sdk.infrastructure.orm import with_transaction as db_session
from sincpro_payments_sdk.infrastructure.aws_services import AwsService as aws_service

# Create an instance of the framework
cybersource = _UseFramework()

# Register dependencies
cybersource.add_dependency("token_adapter", TokenizationAdapter())
cybersource.add_dependency("ECardType", ESupportedCardType)
cybersource.add_dependency("db_session", db_session)
cybersource.add_dependency("aws_service", aws_service)


# Define a custom Feature class to access the dependencies
class Feature(_Feature):
    token_adapter: TokenizationAdapter
    ECardType: Type[ESupportedCardType]
    db_session: ...
    aws_service: ...
    logger: ...


# Define a custom Application Service class to access dependencies
class ApplicationService(_ApplicationService):
    token_adapter: TokenizationAdapter
    ECardType: Type[ESupportedCardType]
    db_session: ...
    aws_service: ...
    logger: ...
    feature_bus: ...


# Add use cases (Application Services and Features)
from . import tokenization

__all__ = ["cybersource", "tokenization", "Feature"]
```

## 🛠️ Creating a Feature

To create a new **Feature**, follow these steps:

1. **Create a Module for the Feature**: Add a new Python file in the appropriate folder under `use_cases`.
2. **Import the Framework and Required Classes**: Import the configured framework instance and `DataTransferObject`.
3. **Define the Parameter and Response DTOs**: Use `DataTransferObject` to create classes for input parameters and
   responses.
4. **Create the Feature Class**: Define the `Feature` class by inheriting from the custom `Feature` class.

### 🖋️ Example of Creating a Feature

```python
from sincpro_payments_sdk.apps.cybersource import cybersource, DataTransferObject, Feature


# Define parameter DTO
class TokenizationParams(DataTransferObject):
    card_number: str
    expiration_date: str
    cardholder_name: str


# Define response DTO
class TokenizationResponse(DataTransferObject):
    token: str
    status: str


# Create the Feature class
@cybersource.feature(TokenizationParams)
class NewTokenizationFeature(Feature):
    def execute(self, dto: TokenizationParams) -> TokenizationResponse:
        # Example usage of dependencies
        cybersource.logger.info("Starting tokenization process")
        token = self.token_adapter.create_token(
            card_number=dto.card_number,
            expiration_date=dto.expiration_date,
            cardholder_name=dto.cardholder_name
        )
        return TokenizationResponse(token=token, status="success")
```

## 🔄 Creating an Application Service

**ApplicationService** is used to coordinate multiple features while maintaining reusability and consistency. It
orchestrates features into cohesive workflows.

### 💡 Example of Creating an Application Service

```python
from sincpro_payments_sdk.apps.cybersource import cybersource, DataTransferObject, ApplicationService
from sincpro_payments_sdk.apps.cybersource.use_cases.tokenization import TokenizationParams


# Define parameter DTO
class PaymentServiceParams(DataTransferObject):
    card_number: str
    expiration_date: str
    cardholder_name: str
    amount: float


# Define response DTO
class PaymentServiceResponse(DataTransferObject):
    status: str
    transaction_id: str


# Create the Application Service class
@cybersource.app_service(PaymentServiceParams)
class PaymentOrchestrationService(ApplicationService):
    def execute(self, dto: PaymentServiceParams) -> PaymentServiceResponse:
        # Create the command DTO for tokenization
        tokenization_command = TokenizationParams(
            card_number=dto.card_number,
            expiration_date=dto.expiration_date,
            cardholder_name=dto.cardholder_name
        )
        tokenization_result = self.feature_bus.execute(tokenization_command)

        # Example usage of dependencies
        cybersource.logger.info("Proceeding with payment after tokenization")
        # Proceed with payment using the token (pseudo code for payment processing)
        transaction_id = "12345"  # Simulated transaction ID
        return PaymentServiceResponse(status="success", transaction_id=transaction_id)
```

## ⚙️ Executing a Use Case

Once a **Feature** or **ApplicationService** is defined, it can be executed by passing the appropriate **DTO** instance.

### 📌 Example of Executing a Use Case

```python
from sincpro_payments_sdk.apps.cybersource import cybersource
from sincpro_payments_sdk.apps.cybersource.use_cases.tokenization import TokenizationParams
from sincpro_payments_sdk.apps.cybersource.use_cases.payments import PaymentServiceParams

# Example of executing a Feature
feature_dto = TokenizationParams(
    card_number="4111111111111111",
    expiration_date="12/25",
    cardholder_name="John Doe"
)

# Execute the feature
feature_result = cybersource.feature_bus.execute(feature_dto)
print(f"Tokenization Result: {feature_result.token}, Status: {feature_result.status}")

# Example of executing an Application Service
service_dto = PaymentServiceParams(
    card_number="4111111111111111",
    expiration_date="12/25",
    cardholder_name="John Doe",
    amount=100.00
)

# Execute the application service
service_result = cybersource(service_dto)
print(f"Payment Status: {service_result.status}, Transaction ID: {service_result.transaction_id}")
```

## 📚 Summary

The Sincpro Framework provides a robust solution for managing the application layer within a hexagonal architecture. By
focusing on decoupling business logic from external dependencies, the framework promotes modularity, scalability, and
maintainability.

- **Features**: Handle specific, self-contained business actions.
- **ApplicationServices**: Orchestrate multiple features for cohesive workflows.

This structured approach ensures high-quality, maintainable software that can adapt to evolving business needs. 🚀

## Configuration or settings

The framework comes with a module or component to allow us to create configuratio or settings based on files or
environment variables.
You need to inherit from `SincproConfig` from module `sincpro_framework.sincpro_conf`

```python
from sincpro_framework.sincpro_conf import SincproConfig


class PostgresConf(SincproConfig):
    host: str = "localhost"
    port: int = 5432
    user: str = "my_user"


class MyConfig(SincproConfig):
    log_level: str = "DEBUG"
    token: str = "defult_my_token"
    postgresql: PostgresConf = PostgresConf()

```

This class should be mapped based on yaml file like this, we have a feature to use ENV variables in the yaml file
using the prefix `$ENV:`

```yaml
log_level: "INFO"
token: "$ENV:MY_SECRET_TOKEN"
postgresql:
  host: localhost
  port: 12345
  user: custom_user
```

Then you can use the config object in your code where it will be loaded all the settings from the yaml file
for that you will require use the following funciton `build_config_obj`

```python
from sincpro_framework.sincpro_conf import build_config_obj
from .my_config import MyConfig

config = build_config_obj(MyConfig, '/path/to/your/config.yml')

assert isinstance(config.log_level, str)
assert isinstance(config.postgresql, PostgresConf)
```




## 📦 Variables

The framework use a default setting file where live in the module folder inside of
`sincpro_framework/conf/sincpro_framework_conf.yml`
where you can define some behavior currently we support the following settings:

- log level(`sincpro_framework_log_level`): Define the log level for the logger, the default is `DEBUG`

Override the config file using another

```bash
export SINCPRO_FRAMEWORK_CONFIG_FILE = /path/to/your/config.yml
```