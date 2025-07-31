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
    - [Context Manager for Metadata Propagation](#context-manager-for-metadata-propagation)
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

### 📡 Context Manager for Metadata Propagation

- Provides automatic metadata propagation across Features and ApplicationServices without manual parameter passing.
- Uses Python's `contextvars` for thread-safe context storage and isolation.
- Supports nested contexts with override capabilities for complex workflows.
- Enriches exceptions with context information for better debugging and observability.

```python
# Simple context usage
with app.context({"correlation_id": "123", "user.id": "admin"}) as app_with_context:
    result = app_with_context(some_dto)  # Context automatically available in handlers

# Nested contexts with overrides
with app.context({"env": "prod", "user": "admin"}) as outer_app:
    with outer_app.context({"env": "staging"}) as inner_app:  # Override env, inherit user
        inner_app(dto)  # env="staging", user="admin"

# Access context in Features and ApplicationServices
class PaymentFeature(Feature):
    def execute(self, dto: PaymentDTO) -> PaymentResponse:
        correlation_id = self.context.get("correlation_id")
        user_id = self.context.get("user.id")
        # Use context in business logic...
```

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
from sincpro_payments_sdk.apps.cybersource.use_cases.tokenization import TokenizationParams, TokenizationResponse
from sincpro_payments_sdk.apps.cybersource.use_cases.payments import PaymentServiceParams, PaymentServiceResponse

# Example of executing a Feature
feature_dto = TokenizationParams(
    card_number="4111111111111111",
    expiration_date="12/25",
    cardholder_name="John Doe"
)

# Execute the feature
feature_result = cybersource(feature_dto, TokenizationResponse)
print(f"Tokenization Result: {feature_result.token}, Status: {feature_result.status}")

# Example of executing an Application Service
service_dto = PaymentServiceParams(
    card_number="4111111111111111",
    expiration_date="12/25",
    cardholder_name="John Doe",
    amount=100.00
)

# Execute the application service
service_result = cybersource(service_dto, PaymentServiceResponse)
print(f"Payment Status: {service_result.status}, Transaction ID: {service_result.transaction_id}")
```

## 📚 Summary

The Sincpro Framework provides a robust solution for managing the application layer within a hexagonal architecture. By
focusing on decoupling business logic from external dependencies, the framework promotes modularity, scalability, and
maintainability.

- **Features**: Handle specific, self-contained business actions.
- **ApplicationServices**: Orchestrate multiple features for cohesive workflows.

This structured approach ensures high-quality, maintainable software that can adapt to evolving business needs. 🚀

## 📖 Auto-Documentation

The Sincpro Framework includes a powerful **auto-documentation** feature that automatically generates comprehensive documentation for your framework instances. This documentation includes all your DTOs, Features, Application Services, Dependencies, and Middlewares in multiple formats optimized for different use cases.

### 🚀 Quick Documentation Generation

The easiest way to generate documentation for your project:

```python
from sincpro_framework.generate_documentation import build_documentation

# Import your framework instances from their respective modules
from apps.payment_gateway import payment_framework
from apps.user_management import user_framework

# Generate traditional markdown documentation (default)
build_documentation(
    [payment_framework, user_framework],
    output_dir="docs/generated"
)

# Generate AI-optimized JSON schema
build_documentation(
    [payment_framework, user_framework],
    output_dir="docs/generated",
    format="json"
)

# Generate both formats
build_documentation(
    [payment_framework, user_framework],
    output_dir="docs/generated", 
    format="both"
)
```

### 📋 Output Formats

#### 📝 Markdown Documentation (Traditional)
- **MkDocs-ready**: Complete documentation website with search
- **Human-readable**: Beautiful, professional documentation for developers
- **Interactive**: Searchable content with cross-references

#### 🤖 AI-Optimized JSON Schema (Enhanced!)
- **Complete AI Understanding**: Combines framework context with repository analysis
- **Framework Context**: How to use the Sincpro Framework (patterns, examples, best practices)
- **Repository Analysis**: What components exist in your specific codebase
- **Rich Metadata**: Business domains, complexity analysis, architectural patterns
- **Code Generation**: Comprehensive hints for AI-powered code generation
- **Embedding Support**: Optimized for semantic search and AI embeddings
- **Usage Synthesis**: Real examples combining framework knowledge with repository components

### 📁 Generated Documentation Structure

```
docs/generated/
├── mkdocs.yml                    # MkDocs configuration
├── requirements.txt              # Dependencies
├── framework_schema.json         # AI-optimized JSON with framework context
├── site/                        # Built HTML documentation
└── docs/                        # Markdown content
    ├── index.md                 # Overview
    ├── features.md              # Features documentation
    ├── dtos.md                  # DTOs documentation
    └── application-services.md  # Services documentation
```

### 🤖 AI-Optimized JSON Schema Features

The enhanced JSON schema combines framework context with repository analysis for complete AI understanding:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Repository Schema with Framework Context",
  "schema_type": "ai_optimized_complete",
  
  "framework_context": {
    "framework_name": "Sincpro Framework",
    "core_principles": {/* Framework usage patterns and principles */},
    "key_features": {/* Framework capabilities and features */},
    "framework_execution_patterns": {/* How to execute features/services */}
  },
  
  "repository_analysis": {
    "metadata": {
      "architecture_patterns": ["DDD", "Clean Architecture"],
      "component_summary": { /* counts and statistics */ }
    },
    "components": {
      "dtos": [/* with AI hints for type classification */],
      "features": [/* with business domain inference */],
      "application_services": [/* with orchestration patterns */]
    }
  },
  
  "ai_integration": {
    "framework_integration": {
      "execution_patterns": {/* How to use framework with repository components */},
      "available_features": {/* Framework capabilities */}
    },
    "complete_understanding": {
      "framework_knowledge": "Loaded from hardcoded guide",
      "repository_knowledge": "Generated from code analysis",
      "ai_capability": "Complete understanding of framework usage + repository components"
    },
    "usage_synthesis": {
      "how_to_execute_features": {/* Real examples combining framework + repo */},
      "how_to_execute_services": {/* Real examples combining framework + repo */}
    },
    "embedding_suggestions": {
      "primary_entities": ["PaymentCommand", "UserCommand"],
      "business_capabilities": ["PaymentFeature", "UserFeature"]
    },
    "code_generation_hints": {
      "framework_patterns": ["command_pattern", "dependency_injection"],
      "common_imports": ["from sincpro_framework import..."]
    },
    "complexity_analysis": {
      "overall_complexity": "medium",
      "most_complex_components": ["ComplexService"]
    }
  }
}
```

### ✨ Documentation Features

#### Traditional Markdown
- **🎨 Sincpro Theme**: Beautiful violet corporate colors and professional styling
- **📱 Responsive Design**: Works perfectly on desktop and mobile devices
- **🔍 Full-Text Search**: Find any component, method, or parameter instantly
- **📊 Component Overview**: Summary tables with component counts and descriptions

#### AI-Optimized JSON
- **🧠 Business Domain Inference**: Automatic categorization (payments, users, orders)
- **📈 Complexity Assessment**: Automatic complexity analysis for optimization
- **🔍 Pattern Recognition**: Identification of architectural patterns
- **🤖 AI Hints**: Rich metadata for AI understanding and code generation

### 🎯 Best Practices

1. **Document your DTOs**: Add clear docstrings to your Data Transfer Objects
2. **Describe your Features**: Include comprehensive docstrings for execute methods
3. **Name components clearly**: Use descriptive names for better auto-generated docs
4. **Organize by domain**: Group related features and services logically

Example of well-documented code:

```python
class PaymentCommand(DataTransferObject):
    """Command for processing credit card payments.

    This DTO contains all necessary information to process
    a payment transaction through the payment gateway.
    """
    card_number: str  # Credit card number (PCI compliant)
    amount: float     # Payment amount in USD
    merchant_id: str  # Unique merchant identifier

@framework.feature(PaymentCommand)
class PaymentFeature(Feature):
    """Process payment transactions through external gateway.

    This feature handles the complete payment flow including
    validation, gateway communication, and response processing.
    """

    def execute(self, dto: PaymentCommand) -> PaymentResponse:
        """Execute payment processing.

        Args:
            dto: Payment command with card and amount information

        Returns:
            PaymentResponse: Transaction result with ID and status

        Raises:
            PaymentError: When payment fails or card is invalid
        """
        # Implementation here...
```

### 🚀 Integration with AI Systems

The JSON schema format enables powerful AI integrations:

- **Code Generation**: AI can understand patterns and generate similar code
- **Documentation**: AI can explain components and their relationships  
- **Analysis**: AI can identify optimization opportunities and suggest improvements
- **Migration**: AI can understand dependencies for migration planning

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

### Environment Variable Handling

When using `$ENV:` prefix in your configuration files, the framework will:

1. Look for the environment variable specified after `$ENV:`
2. If the environment variable exists, use its value
3. If the environment variable doesn't exist:
   - Use the default value defined in your configuration class
   - Issue a warning indicating that the environment variable is missing
   - Proceed with execution rather than raising an error

This behavior allows applications to run with partial configurations in development environments or when not all environment variables are available, while still logging appropriate warnings.

Example of fallback to default values:

```python
# Configuration class with default
class ApiConfig(SincproConfig):
    api_key: str = "dev_default_key"  # Default value as fallback

# In config.yml
api_key: "$ENV:API_KEY"  # References environment variable

# If API_KEY environment variable is not set, the framework will:
# 1. Log a warning: "Environment variable [API_KEY] is not set for field [api_key]. Using default value: dev_default_key"
# 2. Use the default value "dev_default_key"
# 3. Continue execution without error
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
