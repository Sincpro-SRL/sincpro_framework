# AI-Optimized JSON Schema Generation

This document describes the enhanced AI-optimized JSON schema generation feature that combines framework context with repository analysis to provide complete AI understanding.

## Overview

The framework now generates structured JSON schemas that merge:

1. **Framework Context**: How to use the Sincpro Framework (loaded from hardcoded guide)
2. **Repository Analysis**: What components exist in your specific codebase (from code inspection)

This combination enables AI systems to have complete understanding for code generation, semantic search, and intelligent assistance.

## Enhanced Features

### 1. Complete AI Understanding

The JSON schema combines:

- **Framework Context**: Usage patterns, execution examples, and best practices from the Sincpro Framework
- **Repository Analysis**: Specific components found in your codebase
- **AI Integration**: Enhanced metadata that synthesizes framework knowledge with repository components
- **Usage Synthesis**: Real examples showing how to use framework patterns with your specific components

### 2. Framework Context Integration

Automatically loaded from the framework's AI guide:

- **Core Principles**: Framework architecture and design patterns
- **Execution Patterns**: How to properly execute features and services
- **Best Practices**: What to do and what to avoid
- **Usage Examples**: Real code examples and patterns

### 3. Rich Component Analysis

Each component includes:

- **Type Information**: Detailed type analysis with Pydantic field information
- **Pattern Recognition**: Identification of architectural patterns (Command, DDD, etc.)
- **Complexity Assessment**: Automatic complexity level analysis
- **Business Domain Inference**: Automatic categorization by business domain
- **Framework Integration**: How each component fits with framework patterns

### 4. Enhanced AI Metadata

- **Complete Understanding**: Framework knowledge + repository knowledge
- **Code Generation**: Comprehensive hints combining framework patterns with repository components
- **Execution Guidance**: How to execute your specific features and services
- **Best Practices**: Framework best practices applied to your codebase

### 5. Multiple Output Formats

- **JSON Schema Only**: Generate just the AI-optimized schema
- **Markdown Only**: Generate traditional MkDocs documentation (existing functionality)
- **Both Formats**: Generate both markdown and JSON schema simultaneously

## Usage

### Basic Usage

```python
from sincpro_framework.generate_documentation import build_documentation

# Generate JSON schema only
json_path = build_documentation(
    framework_instance, 
    output_dir="docs",
    format="json"
)

# Generate both formats
both_path = build_documentation(
    framework_instance,
    output_dir="docs", 
    format="both"
)
```

### Direct JSON Schema Generation

```python
from sincpro_framework.generate_documentation import generate_json_schema
from sincpro_framework.generate_documentation.infrastructure.sincpro_introspector import component_finder
from sincpro_framework.generate_documentation.infrastructure.framework_docs_extractor import doc_extractor

# Extract framework documentation
introspector_instance = component_finder.introspect(framework_instance)
doc = doc_extractor.extract_framework_docs(introspector_instance)

# Generate JSON schema directly
schema_path = generate_json_schema([doc], "output_dir")
```

### Using the JSON Schema Generator Class

```python
from sincpro_framework.generate_documentation.infrastructure.json_schema_generator import AIOptimizedJSONSchemaGenerator

# Create generator with framework docs
generator = AIOptimizedJSONSchemaGenerator(framework_docs)

# Generate complete schema
schema = generator.generate_complete_schema()

# Save to file
generator.save_to_file("framework_schema.json")
```

## Enhanced Schema Structure

### Top-Level Structure

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Repository Schema with Framework Context",
  "description": "AI-optimized schema combining framework context and repository analysis",
  "version": "1.0.0",
  "generated_at": "2025-07-30 16:00:00",
  "generated_by": "sincpro_framework",
  "schema_type": "ai_optimized_complete",
  
  "framework_context": {
    "framework_name": "Sincpro Framework",
    "version": "1.0.0",
    "core_principles": { /* Framework usage patterns */ },
    "key_features": { /* Framework capabilities */ },
    "framework_execution_patterns": { /* How to execute features/services */ }
  },
  
  "repository_analysis": {
    "metadata": { /* Repository-specific metadata */ },
    "components": { /* DTOs, Features, Services, etc. */ },
    "relationships": { /* Component relationships */ }
  },
  
  "ai_integration": {
    "framework_integration": { /* How framework works with repository */ },
    "complete_understanding": { /* AI capabilities description */ },
    "usage_synthesis": { /* Combined examples and patterns */ }
  }
  "metadata": { /* Framework metadata */ },
  "components": { /* Component schemas */ },
  "relationships": { /* Component relationships */ },
  "ai_metadata": { /* AI-specific metadata */ }
}
```

### Component Types

#### DTOs (Data Transfer Objects)
```json
{
  "type": "data_transfer_object",
  "name": "PaymentCommand",
  "purpose": "data_validation_serialization",
  "validation_framework": "pydantic",
  "fields": {
    "amount": {
      "type": "float",
      "required": true,
      "ai_hints": {
        "is_amount": true,
        "is_identifier": false
      }
    }
  },
  "ai_hints": {
    "is_input_type": true,
    "complexity_level": "simple",
    "validation_rules": ["amount_required"]
  }
}
```

#### Features
```json
{
  "type": "feature",
  "name": "PaymentFeature", 
  "pattern": "command_pattern",
  "purpose": "business_logic_execution",
  "execute_method": {
    "signature": "(self, dto: PaymentCommand) -> PaymentResponse",
    "ai_hints": {
      "is_main_entry_point": true,
      "follows_command_pattern": true
    }
  },
  "ai_hints": {
    "is_synchronous": true,
    "has_side_effects": true,
    "complexity_level": "simple",
    "business_domain": "payments"
  }
}
```

#### Application Services
```json
{
  "type": "application_service",
  "name": "PaymentProcessor",
  "pattern": "service_layer_pattern", 
  "purpose": "orchestration_coordination",
  "ai_hints": {
    "orchestrates_features": true,
    "handles_transactions": true,
    "complexity_level": "medium"
  }
}
```

### AI Metadata

The schema includes special AI metadata for enhanced understanding:

```json
{
  "ai_metadata": {
    "embedding_suggestions": {
      "primary_entities": ["PaymentCommand", "UserCommand"],
      "business_capabilities": ["PaymentFeature", "UserFeature"],
      "integration_points": ["PaymentGateway", "UserRepository"],
      "data_flow_patterns": ["command_input", "service_orchestration"]
    },
    "code_generation_hints": {
      "framework_patterns": [
        "decorator_based_registration",
        "dependency_injection",
        "command_pattern"
      ],
      "common_imports": [
        "from sincpro_framework import UseFramework, Feature, DataTransferObject"
      ],
      "naming_conventions": {
        "dtos": "PascalCase ending with Command/Query/Response",
        "features": "PascalCase ending with Feature"
      }
    },
    "complexity_analysis": {
      "overall_complexity": "medium",
      "most_complex_components": ["service:ComplexProcessor"],
      "simplest_components": ["dto:SimpleCommand"]
    }
  }
}
```

## AI Optimization Features

### 1. Business Domain Inference

The system automatically infers business domains from component names:

- **Payment**: Components with "payment", "transaction", "billing"
- **User Management**: Components with "user", "auth", "account"
- **Order Management**: Components with "order", "cart", "checkout"
- **Catalog**: Components with "product", "item", "inventory"

### 2. Complexity Assessment

Components are automatically assessed for complexity:

- **Simple**: Few fields/methods, basic functionality
- **Medium**: Moderate complexity, some orchestration
- **Complex**: Many fields/methods, complex business logic

### 3. Pattern Recognition

The system identifies common architectural patterns:

- **Command Pattern**: Features with execute methods
- **Service Layer Pattern**: Application Services that orchestrate
- **Dependency Injection**: Injected dependencies
- **Middleware Pipeline**: Middleware components

### 4. Type Analysis

Advanced type analysis includes:

- **Field Purpose**: Identification of IDs, amounts, text fields, status fields
- **Input/Output Types**: Classification of DTOs as input or output types
- **Validation Rules**: Extraction of Pydantic validation rules
- **Default Values**: Detection of fields with default values

## Integration with AI Systems

### For Code Generation

The schema provides rich metadata for AI code generation:

1. **Framework Patterns**: Common patterns used in the framework
2. **Import Statements**: Typical imports needed
3. **Naming Conventions**: Consistent naming patterns
4. **Component Templates**: Structure for different component types

### For Semantic Search and Embeddings

The schema identifies key entities for embedding:

1. **Primary Entities**: Main DTOs and business objects
2. **Business Capabilities**: Core features and services
3. **Integration Points**: External system connections
4. **Data Flow Patterns**: How data moves through the system

### For Documentation and Analysis

The schema enables AI systems to:

1. **Generate Documentation**: Understand component purposes and relationships
2. **Suggest Improvements**: Identify complexity hotspots and simplification opportunities
3. **Code Review**: Understand architectural patterns and adherence
4. **Migration Planning**: Analyze dependencies and relationships

## Backward Compatibility

The new JSON schema generation is fully backward compatible:

- **Existing Code**: No changes required to existing code
- **Existing Documentation**: Markdown generation continues to work unchanged
- **API Compatibility**: All existing API methods remain functional
- **Optional Feature**: JSON generation is opt-in via format parameter

## Performance Considerations

- **Generation Time**: JSON schema generation adds minimal overhead
- **File Size**: JSON schemas are typically smaller than markdown documentation
- **Memory Usage**: Uses the same introspection data as markdown generation
- **Caching**: Schema generation benefits from existing caching mechanisms

## Examples

See the test files in `tests/test_auto_docs.py` for comprehensive examples of using the JSON schema generation functionality.

## Future Enhancements

Planned improvements include:

1. **OpenAPI Schema Generation**: Generate OpenAPI 3.0 specifications
2. **GraphQL Schema Generation**: Generate GraphQL schemas
3. **Custom AI Hints**: Allow manual AI hints and metadata
4. **Enhanced Relationship Analysis**: More sophisticated dependency analysis
5. **Integration with Popular AI Tools**: Direct integration with LangChain, etc.