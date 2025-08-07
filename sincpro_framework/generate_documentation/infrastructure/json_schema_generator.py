"""
JSON Schema Generator for AI Consumption

Generates JSON Schema optimized for AI consumption and embedding processes.
This generator creates structured data that can be easily consumed by AI models
for understanding framework components and generating code.

The generator merges framework context (how to use the framework) with
repository-specific components (what exists in a specific codebase) to provide
complete AI understanding.
"""

import json
import os
from typing import Any, Dict, List, Optional

from sincpro_framework.generate_documentation.domain.models import (
    ClassMetadata,
    FrameworkDocs,
    FunctionMetadata,
)


class AIOptimizedJSONSchemaGenerator:
    """
    Generates JSON Schema optimized for AI consumption and embedding processes.

    The output is structured to be easily parsed by AI models and contains
    rich metadata for understanding framework components. This generator combines:

    1. Framework context (how to use the Sincpro Framework) - from hardcoded guide
    2. Repository components (what exists in specific codebase) - from inspection

    This provides complete AI understanding of both framework usage and specific implementations.
    """

    def __init__(self, framework_docs: FrameworkDocs):
        self.framework_docs = framework_docs
        self.schema_version = "1.0.0"
        self.framework_context = self._load_framework_context()

    def _load_framework_context(self) -> Dict[str, Any]:
        """
        Load the framework context from the hardcoded AI guide JSON.

        This provides AI with knowledge about how to use the Sincpro Framework,
        complementing the repository-specific component analysis.
        """
        try:
            # Get the path to the framework AI guide
            current_dir = os.path.dirname(__file__)
            guide_path = os.path.join(current_dir, "..", "sincpro_framework_ai_guide.json")
            guide_path = os.path.abspath(guide_path)

            with open(guide_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            # Fallback to minimal context if guide is not available
            print(f"Warning: Could not load framework AI guide: {e}")
            return {
                "framework_name": "Sincpro Framework",
                "description": "Application Layer Framework within Hexagonal Architecture",
                "note": "Framework context not available - using minimal fallback",
            }

    def generate_complete_schema(self) -> Dict[str, Any]:
        """
        Generate complete JSON schema optimized for AI consumption.

        Combines framework context (how to use Sincpro Framework) with
        repository-specific components (what exists in this codebase) to provide
        complete understanding for AI systems.

        Returns:
            Dict[str, Any]: Complete JSON schema with framework context and components
        """
        # Generate repository-specific component schema
        repository_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": f"{self.framework_docs.framework_name} Repository Schema",
            "description": f"AI-optimized schema combining framework context and repository components for {self.framework_docs.framework_name}",
            "version": self.schema_version,
            "generated_at": self.framework_docs.generated_at,
            "generated_by": self.framework_docs.generated_by,
            "schema_type": "ai_optimized_complete",
            # Framework context provides "how to use the framework"
            "framework_context": self.framework_context,
            # Repository analysis provides "what exists in this specific codebase"
            "repository_analysis": {
                "metadata": self._generate_repository_metadata(),
                "components": {
                    "dtos": self._generate_dto_schemas(),
                    "features": self._generate_feature_schemas(),
                    "application_services": self._generate_application_service_schemas(),
                    "dependencies": self._generate_dependency_schemas(),
                    "middlewares": self._generate_middleware_schemas(),
                },
                "relationships": self._generate_component_relationships(),
            },
            # AI-specific metadata for both framework and repository
            "ai_integration": self._generate_enhanced_ai_metadata(),
        }

        return repository_schema

    def _generate_repository_metadata(self) -> Dict[str, Any]:
        """Generate repository-specific metadata for AI understanding"""
        summary = self.framework_docs.summary

        return {
            "repository_name": self.framework_docs.framework_name,
            "uses_framework": "sincpro_framework",
            "repository_type": "framework_implementation",
            "architecture_patterns": [
                "Domain-Driven Design",
                "Clean Architecture",
                "Hexagonal Architecture",
                "Command Query Separation",
            ],
            "component_summary": {
                "total_components": summary.total_components if summary else 0,
                "dtos_count": summary.dtos_count if summary else 0,
                "features_count": summary.features_count if summary else 0,
                "application_services_count": (
                    summary.application_services_count if summary else 0
                ),
                "middlewares_count": summary.middlewares_count if summary else 0,
                "dependencies_count": summary.dependencies_count if summary else 0,
            },
            "capabilities": self._extract_repository_capabilities(),
        }

    def _generate_dto_schemas(self) -> List[Dict[str, Any]]:
        """Generate AI-optimized DTO schemas"""
        dto_schemas = []

        for dto in self.framework_docs.dtos:
            schema = {
                "type": "data_transfer_object",
                "name": dto.name,
                "module": dto.module,
                "description": dto.docstring or f"Data Transfer Object: {dto.name}",
                "purpose": "data_validation_serialization",
                "validation_framework": "pydantic",
                "fields": self._convert_pydantic_fields_to_ai_schema(dto.fields),
                "json_schema": dto.model_schema,
                "usage_patterns": [
                    "command_input",
                    "query_input",
                    "response_output",
                    "event_payload",
                ],
                "ai_hints": {
                    "is_input_type": self._is_input_dto(dto.name),
                    "is_output_type": self._is_output_dto(dto.name),
                    "complexity_level": self._assess_dto_complexity(dto.fields),
                    "validation_rules": self._extract_validation_rules(dto.fields),
                },
            }
            dto_schemas.append(schema)

        return dto_schemas

    def _generate_feature_schemas(self) -> List[Dict[str, Any]]:
        """Generate AI-optimized Feature schemas"""
        feature_schemas = []

        for feature in self.framework_docs.features:
            schema = {
                "type": "feature",
                "name": feature.name,
                "module": feature.module,
                "description": feature.docstring or f"Feature: {feature.name}",
                "purpose": "business_logic_execution",
                "pattern": "command_pattern",
                "methods": self._convert_methods_to_ai_schema(feature.methods),
                "execute_method": self._extract_execute_method_details(feature),
                "dependencies": self._extract_feature_dependencies(feature),
                "ai_hints": {
                    "is_synchronous": True,
                    "has_side_effects": True,
                    "input_types": self._extract_input_types(feature),
                    "output_types": self._extract_output_types(feature),
                    "complexity_level": self._assess_feature_complexity(feature),
                    "business_domain": self._infer_business_domain(feature.name),
                },
            }
            feature_schemas.append(schema)

        return feature_schemas

    def _generate_application_service_schemas(self) -> List[Dict[str, Any]]:
        """Generate AI-optimized Application Service schemas"""
        service_schemas = []

        for service in self.framework_docs.application_services:
            schema = {
                "type": "application_service",
                "name": service.name,
                "module": service.module,
                "description": service.docstring or f"Application Service: {service.name}",
                "purpose": "orchestration_coordination",
                "pattern": "service_layer_pattern",
                "methods": self._convert_methods_to_ai_schema(service.methods),
                "orchestration_capabilities": True,
                "ai_hints": {
                    "orchestrates_features": True,
                    "handles_transactions": True,
                    "coordinates_external_services": True,
                    "complexity_level": self._assess_service_complexity(service),
                    "business_domain": self._infer_business_domain(service.name),
                },
            }
            service_schemas.append(schema)

        return service_schemas

    def _generate_dependency_schemas(self) -> List[Dict[str, Any]]:
        """Generate AI-optimized Dependency schemas"""
        dependency_schemas = []

        for dep in self.framework_docs.dependencies:
            if isinstance(dep, FunctionMetadata):
                schema = {
                    "type": "dependency_function",
                    "name": dep.name,
                    "module": dep.module,
                    "description": dep.docstring or f"Dependency Function: {dep.name}",
                    "purpose": "utility_service_provision",
                    "signature": dep.signature,
                    "parameters": dep.parameters,
                    "return_type": dep.return_type,
                    "is_async": dep.is_async,
                    "ai_hints": {
                        "is_pure_function": self._is_pure_function(dep),
                        "has_side_effects": self._has_side_effects(dep),
                        "complexity_level": "low",
                    },
                }
            elif isinstance(dep, ClassMetadata):
                schema = {
                    "type": "dependency_class",
                    "name": dep.name,
                    "module": dep.module,
                    "description": dep.docstring or f"Dependency Class: {dep.name}",
                    "purpose": "service_provision",
                    "methods": self._convert_methods_to_ai_schema(dep.methods),
                    "attributes": dep.attributes,
                    "ai_hints": {
                        "is_stateful": len(dep.attributes) > 0,
                        "provides_external_integration": self._is_external_integration(dep),
                        "complexity_level": self._assess_dependency_complexity(dep),
                    },
                }

            dependency_schemas.append(schema)

        return dependency_schemas

    def _generate_middleware_schemas(self) -> List[Dict[str, Any]]:
        """Generate AI-optimized Middleware schemas"""
        middleware_schemas = []

        for middleware in self.framework_docs.middlewares:
            if isinstance(middleware, FunctionMetadata):
                schema = {
                    "type": "middleware_function",
                    "name": middleware.name,
                    "module": middleware.module,
                    "description": middleware.docstring
                    or f"Middleware Function: {middleware.name}",
                    "purpose": "cross_cutting_concerns",
                    "pattern": "middleware_pattern",
                    "execution_order": "pre_post_processing",
                    "ai_hints": {
                        "modifies_request": True,
                        "modifies_response": True,
                        "has_side_effects": True,
                        "complexity_level": "medium",
                    },
                }
            elif isinstance(middleware, ClassMetadata):
                schema = {
                    "type": "middleware_class",
                    "name": middleware.name,
                    "module": middleware.module,
                    "description": middleware.docstring
                    or f"Middleware Class: {middleware.name}",
                    "purpose": "cross_cutting_concerns",
                    "pattern": "middleware_pattern",
                    "methods": self._convert_methods_to_ai_schema(middleware.methods),
                    "ai_hints": {
                        "is_stateful": len(middleware.attributes) > 0,
                        "complexity_level": self._assess_middleware_complexity(middleware),
                    },
                }

            middleware_schemas.append(schema)

        return middleware_schemas

    def _generate_component_relationships(self) -> Dict[str, Any]:
        """Generate component relationship mappings for AI understanding"""
        return {
            "dto_usage": self._map_dto_usage(),
            "feature_dependencies": self._map_feature_dependencies(),
            "service_orchestration": self._map_service_orchestration(),
            "middleware_chain": self._map_middleware_chain(),
            "dependency_injection": self._map_dependency_injection(),
        }

    def _generate_enhanced_ai_metadata(self) -> Dict[str, Any]:
        """
        Generate enhanced AI metadata that combines framework context with repository analysis.

        This provides AI with complete understanding by merging:
        1. Framework usage patterns and examples from the context
        2. Repository-specific components and their relationships
        """
        base_ai_metadata = self._generate_ai_metadata()

        # Enhance with framework context integration
        enhanced_metadata = {
            **base_ai_metadata,
            "framework_integration": {
                "framework_version": self.framework_context.get("version", "unknown"),
                "architecture_pattern": self.framework_context.get(
                    "architecture_pattern", "unknown"
                ),
                "execution_patterns": self._extract_execution_patterns_from_context(),
                "available_features": self._extract_framework_features_from_context(),
            },
            "complete_understanding": {
                "framework_knowledge": "Loaded from hardcoded guide - provides usage patterns and examples",
                "repository_knowledge": "Generated from code analysis - provides specific components",
                "ai_capability": "Can understand both how to use framework AND what exists in this repository",
                "code_generation_ready": True,
                "semantic_search_optimized": True,
            },
            "usage_synthesis": {
                "how_to_execute_features": self._synthesize_feature_execution(),
                "how_to_execute_services": self._synthesize_service_execution(),
                "common_patterns_in_repository": self._identify_repository_patterns(),
                "framework_best_practices": self._extract_best_practices_from_context(),
            },
        }

        return enhanced_metadata

    def _generate_ai_metadata(self) -> Dict[str, Any]:
        """Generate base metadata specifically for AI consumption (backward compatibility)"""
        return {
            "embedding_suggestions": {
                "primary_entities": [dto.name for dto in self.framework_docs.dtos],
                "business_capabilities": [
                    feature.name for feature in self.framework_docs.features
                ],
                "integration_points": self._identify_integration_points(),
                "data_flow_patterns": self._identify_data_flow_patterns(),
            },
            "code_generation_hints": {
                "framework_patterns": [
                    "decorator_based_registration",
                    "dependency_injection",
                    "command_pattern",
                    "middleware_pipeline",
                ],
                "common_imports": self._extract_common_imports(),
                "naming_conventions": self._extract_naming_conventions(),
            },
            "complexity_analysis": {
                "overall_complexity": self._assess_overall_complexity(),
                "most_complex_components": self._identify_complex_components(),
                "simplest_components": self._identify_simple_components(),
            },
        }

    def _extract_repository_capabilities(self) -> List[str]:
        """Extract repository-specific capabilities for AI understanding"""
        capabilities = ["dependency_injection", "command_execution"]

        if self.framework_docs.middlewares:
            capabilities.append("middleware_pipeline")
        if self.framework_docs.application_services:
            capabilities.append("service_orchestration")
        if any(
            "async" in str(method)
            for feature in self.framework_docs.features
            for method in feature.methods.values()
        ):
            capabilities.append("async_processing")

        return capabilities

    def _extract_execution_patterns_from_context(self) -> Dict[str, Any]:
        """Extract execution patterns from framework context"""
        execution_info = self.framework_context.get("framework_execution_patterns", {})
        return {
            "unified_execution": execution_info.get(
                "unified_execution_pattern",
                "Use framework(dto, ResponseClass) for all executions",
            ),
            "feature_execution": execution_info.get("feature_execution_example", {}),
            "service_execution": execution_info.get(
                "application_service_execution_example", {}
            ),
            "anti_patterns": execution_info.get("anti_patterns", {}).get(
                "forbidden_patterns", []
            ),
        }

    def _extract_framework_features_from_context(self) -> Dict[str, Any]:
        """Extract framework features from context"""
        features = self.framework_context.get("key_features", {})
        return {
            "dto_validation": features.get("dto_validation", {}),
            "dependency_injection": features.get("dependency_injection", {}),
            "error_handling": features.get("multi_level_error_handling", {}),
            "inversion_of_control": features.get("inversion_of_control", {}),
        }

    def _synthesize_feature_execution(self) -> Dict[str, Any]:
        """Synthesize how to execute features based on context + repository components"""
        execution_guidance = self.framework_context.get("framework_execution_patterns", {})
        feature_example = execution_guidance.get("feature_execution_example", {})

        repository_features = [f.name for f in self.framework_docs.features]

        return {
            "execution_pattern": execution_guidance.get(
                "unified_execution_pattern", "framework_instance(dto, ResponseClass)"
            ),
            "example_from_context": feature_example.get("code", []),
            "features_in_repository": repository_features,
            "note": "Use framework_instance(dto, ResponseClass) to execute any feature listed in features_in_repository",
        }

    def _synthesize_service_execution(self) -> Dict[str, Any]:
        """Synthesize how to execute application services based on context + repository components"""
        execution_guidance = self.framework_context.get("framework_execution_patterns", {})
        service_example = execution_guidance.get("application_service_execution_example", {})

        repository_services = [s.name for s in self.framework_docs.application_services]

        return {
            "execution_pattern": execution_guidance.get(
                "unified_execution_pattern", "framework_instance(dto, ResponseClass)"
            ),
            "example_from_context": service_example.get("code", []),
            "services_in_repository": repository_services,
            "note": "Use framework_instance(dto, ResponseClass) to execute any service listed in services_in_repository",
        }

    def _identify_repository_patterns(self) -> List[str]:
        """Identify common patterns used in this repository"""
        patterns = []

        # Analyze DTOs for patterns
        dto_names = [dto.name.lower() for dto in self.framework_docs.dtos]
        if any("command" in name for name in dto_names):
            patterns.append("command_pattern")
        if any("query" in name for name in dto_names):
            patterns.append("query_pattern")
        if any("response" in name for name in dto_names):
            patterns.append("response_pattern")

        # Analyze features and services
        if self.framework_docs.features:
            patterns.append("feature_based_architecture")
        if self.framework_docs.application_services:
            patterns.append("service_layer_pattern")
        if self.framework_docs.middlewares:
            patterns.append("middleware_pipeline")

        return patterns

    def _extract_best_practices_from_context(self) -> List[str]:
        """Extract best practices from framework context"""
        execution_patterns = self.framework_context.get("framework_execution_patterns", {})
        anti_patterns = execution_patterns.get("anti_patterns", {})
        ai_guidance = self.framework_context.get("ai_guidance", {})

        practices = []

        # Extract from anti-patterns (what NOT to do)
        forbidden = anti_patterns.get("forbidden_patterns", [])
        if forbidden:
            practices.append(
                "Never use internal buses directly - always use framework(dto, ResponseClass)"
            )

        # Extract from AI guidance
        if ai_guidance.get("execution_principles"):
            practices.append("Use unified execution pattern: framework(dto, ResponseClass)")

        # Extract from execution patterns
        if execution_patterns.get("execution_principles"):
            practices.append("Let framework handle automatic DTO to handler resolution")

        return practices

    def _convert_pydantic_fields_to_ai_schema(
        self, fields: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Convert Pydantic fields to AI-friendly schema"""
        ai_fields = {}
        for field_name, field_info in fields.items():
            ai_fields[field_name] = {
                "type": field_info.get("type", "any"),
                "required": field_info.get("required", False),
                "default": field_info.get("default"),
                "description": field_info.get("description", ""),
                "ai_hints": {
                    "is_identifier": "id" in field_name.lower(),
                    "is_amount": "amount" in field_name.lower()
                    or "price" in field_name.lower(),
                    "is_text": "name" in field_name.lower()
                    or "description" in field_name.lower(),
                    "is_status": "status" in field_name.lower()
                    or "state" in field_name.lower(),
                },
            }
        return ai_fields

    def _convert_methods_to_ai_schema(
        self, methods: Dict[str, FunctionMetadata]
    ) -> Dict[str, Any]:
        """Convert class methods to AI-friendly schema"""
        ai_methods = {}
        for method_name, method_info in methods.items():
            ai_methods[method_name] = {
                "signature": method_info.signature,
                "description": method_info.docstring or "",
                "parameters": method_info.parameters,
                "return_type": method_info.return_type,
                "is_async": method_info.is_async,
                "ai_hints": {
                    "is_entry_point": method_name == "execute",
                    "is_property": method_name.startswith("get_")
                    or method_name.startswith("is_"),
                    "is_action": method_name.startswith("create_")
                    or method_name.startswith("update_")
                    or method_name.startswith("delete_"),
                },
            }
        return ai_methods

    def _extract_execute_method_details(
        self, feature: ClassMetadata
    ) -> Optional[Dict[str, Any]]:
        """Extract details about the execute method for AI understanding"""
        execute_method = feature.methods.get("execute")
        if not execute_method:
            return None

        return {
            "signature": execute_method.signature,
            "description": execute_method.docstring or "Main execution method",
            "parameters": execute_method.parameters,
            "return_type": execute_method.return_type,
            "ai_hints": {
                "is_main_entry_point": True,
                "follows_command_pattern": True,
                "processes_dto": True,
            },
        }

    def _extract_framework_capabilities(self) -> List[str]:
        """Extract framework capabilities for AI understanding"""
        capabilities = ["dependency_injection", "command_execution"]

        if self.framework_docs.middlewares:
            capabilities.append("middleware_pipeline")
        if self.framework_docs.application_services:
            capabilities.append("service_orchestration")
        if any(
            "async" in str(method)
            for feature in self.framework_docs.features
            for method in feature.methods.values()
        ):
            capabilities.append("async_processing")

        return capabilities

    def _is_input_dto(self, dto_name: str) -> bool:
        """Determine if DTO is typically used for input"""
        return (
            "command" in dto_name.lower()
            or "query" in dto_name.lower()
            or "request" in dto_name.lower()
        )

    def _is_output_dto(self, dto_name: str) -> bool:
        """Determine if DTO is typically used for output"""
        return (
            "response" in dto_name.lower()
            or "result" in dto_name.lower()
            or "event" in dto_name.lower()
        )

    def _assess_dto_complexity(self, fields: Dict[str, Dict[str, Any]]) -> str:
        """Assess DTO complexity for AI hints"""
        field_count = len(fields)
        if field_count <= 3:
            return "simple"
        elif field_count <= 7:
            return "medium"
        else:
            return "complex"

    def _assess_feature_complexity(self, feature: ClassMetadata) -> str:
        """Assess feature complexity for AI hints"""
        method_count = len(feature.methods)
        if method_count <= 2:
            return "simple"
        elif method_count <= 5:
            return "medium"
        else:
            return "complex"

    def _assess_service_complexity(self, service: ClassMetadata) -> str:
        """Assess application service complexity"""
        method_count = len(service.methods)
        if method_count <= 3:
            return "simple"
        elif method_count <= 6:
            return "medium"
        else:
            return "complex"

    def _assess_dependency_complexity(self, dependency: ClassMetadata) -> str:
        """Assess dependency complexity"""
        method_count = len(dependency.methods)
        if method_count <= 2:
            return "simple"
        elif method_count <= 5:
            return "medium"
        else:
            return "complex"

    def _assess_middleware_complexity(self, middleware: ClassMetadata) -> str:
        """Assess middleware complexity"""
        method_count = len(middleware.methods)
        if method_count <= 1:
            return "simple"
        elif method_count <= 3:
            return "medium"
        else:
            return "complex"

    def _infer_business_domain(self, component_name: str) -> str:
        """Infer business domain from component name"""
        name_lower = component_name.lower()

        domain_keywords = {
            "payment": "payments",
            "user": "user_management",
            "order": "order_management",
            "product": "catalog",
            "inventory": "inventory",
            "auth": "authentication",
            "notification": "notifications",
            "report": "reporting",
            "analytics": "analytics",
        }

        for keyword, domain in domain_keywords.items():
            if keyword in name_lower:
                return domain

        return "general"

    def _extract_validation_rules(self, fields: Dict[str, Dict[str, Any]]) -> List[str]:
        """Extract validation rules for AI understanding"""
        rules = []
        for field_name, field_info in fields.items():
            if field_info.get("required", False):
                rules.append(f"{field_name}_required")
            if field_info.get("default") is not None:
                rules.append(f"{field_name}_has_default")
        return rules

    def _extract_feature_dependencies(self, feature: ClassMetadata) -> List[str]:
        """Extract feature dependencies from attributes"""
        # This would need to be enhanced based on actual dependency injection patterns
        dependencies = []
        for attr_name, attr_type in feature.attributes.items():
            if not attr_name.startswith("_"):
                dependencies.append(attr_name)
        return dependencies

    def _extract_input_types(self, feature: ClassMetadata) -> List[str]:
        """Extract input types from feature execute method"""
        execute_method = feature.methods.get("execute")
        if execute_method and execute_method.parameters:
            return [
                param.get("type", "any")
                for param in execute_method.parameters.values()
                if param.get("name") != "self"
            ]
        return []

    def _extract_output_types(self, feature: ClassMetadata) -> List[str]:
        """Extract output types from feature execute method"""
        execute_method = feature.methods.get("execute")
        if execute_method:
            return [execute_method.return_type]
        return []

    def _is_pure_function(self, func: FunctionMetadata) -> bool:
        """Determine if function is pure (no side effects)"""
        # This is a heuristic - could be enhanced with static analysis
        return not any(
            keyword in func.name.lower()
            for keyword in ["save", "create", "update", "delete", "send", "post"]
        )

    def _has_side_effects(self, func: FunctionMetadata) -> bool:
        """Determine if function has side effects"""
        return not self._is_pure_function(func)

    def _is_external_integration(self, dep: ClassMetadata) -> bool:
        """Determine if dependency provides external integration"""
        integration_keywords = [
            "adapter",
            "client",
            "api",
            "service",
            "gateway",
            "repository",
        ]
        return any(keyword in dep.name.lower() for keyword in integration_keywords)

    # Relationship mapping methods

    def _map_dto_usage(self) -> Dict[str, List[str]]:
        """Map which DTOs are used by which features/services"""
        # This would need enhancement based on actual usage analysis
        return {}

    def _map_feature_dependencies(self) -> Dict[str, List[str]]:
        """Map feature to dependency relationships"""
        return {}

    def _map_service_orchestration(self) -> Dict[str, List[str]]:
        """Map which services orchestrate which features"""
        return {}

    def _map_middleware_chain(self) -> List[str]:
        """Map middleware execution order"""
        return [m.name for m in self.framework_docs.middlewares]

    def _map_dependency_injection(self) -> Dict[str, List[str]]:
        """Map dependency injection relationships"""
        return {}

    def _identify_integration_points(self) -> List[str]:
        """Identify external integration points"""
        integration_points = []
        for dep in self.framework_docs.dependencies:
            if isinstance(dep, ClassMetadata) and self._is_external_integration(dep):
                integration_points.append(dep.name)
        return integration_points

    def _identify_data_flow_patterns(self) -> List[str]:
        """Identify common data flow patterns"""
        patterns = ["command_input"]
        if self.framework_docs.application_services:
            patterns.append("service_orchestration")
        if self.framework_docs.middlewares:
            patterns.append("middleware_processing")
        return patterns

    def _extract_common_imports(self) -> List[str]:
        """Extract common import patterns"""
        imports = [
            "from sincpro_framework import UseFramework, Feature, DataTransferObject",
            "from typing import Any",
        ]
        if self.framework_docs.application_services:
            imports.append("from sincpro_framework import ApplicationService")
        if self.framework_docs.middlewares:
            imports.append("from sincpro_framework import Middleware")
        return imports

    def _extract_naming_conventions(self) -> Dict[str, str]:
        """Extract naming conventions for AI understanding"""
        return {
            "dtos": "PascalCase ending with Command/Query/Response",
            "features": "PascalCase ending with Feature",
            "services": "PascalCase ending with Service",
            "dependencies": "camelCase or snake_case",
            "methods": "snake_case",
        }

    def _assess_overall_complexity(self) -> str:
        """Assess overall framework complexity"""
        total_components = (
            len(self.framework_docs.dtos)
            + len(self.framework_docs.features)
            + len(self.framework_docs.application_services)
        )

        if total_components <= 5:
            return "simple"
        elif total_components <= 15:
            return "medium"
        else:
            return "complex"

    def _identify_complex_components(self) -> List[str]:
        """Identify most complex components"""
        complex_components = []

        for feature in self.framework_docs.features:
            if self._assess_feature_complexity(feature) == "complex":
                complex_components.append(f"feature:{feature.name}")

        for service in self.framework_docs.application_services:
            if self._assess_service_complexity(service) == "complex":
                complex_components.append(f"service:{service.name}")

        return complex_components

    def _identify_simple_components(self) -> List[str]:
        """Identify simplest components"""
        simple_components = []

        for dto in self.framework_docs.dtos:
            if self._assess_dto_complexity(dto.fields) == "simple":
                simple_components.append(f"dto:{dto.name}")

        return simple_components

    def save_to_file(self, output_path: str = "framework_schema.json"):
        """Save the JSON schema to a file"""
        schema = self.generate_complete_schema()

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(schema, f, indent=2, ensure_ascii=False, default=str)

        return output_path


class ChunkedAIJSONSchemaGenerator:
    """
    Generates chunked JSON schemas optimized for AI consumption with progressive discovery.

    Creates multiple smaller JSON files organized for token efficiency:
    - Framework context (shared knowledge)
    - Instance overview (lightweight per-framework context)
    - Component chunks (DTOs, Features, etc. in digestible pieces)
    - Detail chunks (full information when needed)
    """

    def __init__(self, framework_docs: FrameworkDocs = None):
        self.framework_docs = framework_docs
        self.schema_version = "1.0.0"

    def _load_framework_context(self) -> Dict[str, Any]:
        """Load the shared framework context from AI guide"""
        try:
            current_dir = os.path.dirname(__file__)
            guide_path = os.path.join(current_dir, "..", "sincpro_framework_ai_guide.json")
            guide_path = os.path.abspath(guide_path)

            with open(guide_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {
                "framework_name": "Sincpro Framework",
                "description": "Application Layer Framework within Hexagonal Architecture",
                "note": "Framework context not available - using minimal fallback",
            }

    def generate_framework_context(self) -> Dict[str, Any]:
        """Generate the shared framework context file"""
        context = self._load_framework_context()

        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Sincpro Framework Context",
            "description": "General framework knowledge for understanding how to use Sincpro Framework",
            "version": self.schema_version,
            "schema_type": "framework_context",
            "content_type": "framework_knowledge",
            "ai_usage": {
                "purpose": "Provides foundational understanding of Sincpro Framework patterns and usage",
                "token_efficiency": "Shared across all instances to avoid duplication",
                "next_steps": "Load specific instance context files for implementation details",
            },
            "framework_context": context,
        }

    def generate_instance_overview(self, instance_number: int) -> Dict[str, Any]:
        """Generate lightweight overview for a framework instance"""
        if not self.framework_docs:
            raise ValueError("FrameworkDocs required for instance overview")

        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": f"{self.framework_docs.framework_name} Instance Overview",
            "description": f"Lightweight overview of {self.framework_docs.framework_name} components",
            "version": self.schema_version,
            "instance_number": instance_number,
            "schema_type": "instance_overview",
            "content_type": "component_overview",
            "ai_usage": {
                "purpose": "Quick understanding of available components without full details",
                "token_efficiency": "Summary-level information for rapid comprehension",
                "next_steps": "Load specific component detail files when needed",
            },
            "framework_instance": {
                "name": self.framework_docs.framework_name,
                "generated_at": self.framework_docs.generated_at,
                "component_summary": {
                    "dtos": {
                        "count": len(self.framework_docs.dtos),
                        "names": [dto.name for dto in self.framework_docs.dtos],
                    },
                    "features": {
                        "count": len(self.framework_docs.features),
                        "names": [feature.name for feature in self.framework_docs.features],
                    },
                    "application_services": {
                        "count": len(self.framework_docs.application_services),
                        "names": [
                            service.name
                            for service in self.framework_docs.application_services
                        ],
                    },
                    "dependencies": {
                        "count": len(self.framework_docs.dependencies),
                        "names": [
                            getattr(dep, "name", str(dep))
                            for dep in self.framework_docs.dependencies
                        ],
                    },
                },
                "available_detail_files": [
                    f"{instance_number:02d}_{self.framework_docs.framework_name}_dtos.json",
                    f"{instance_number:02d}_{self.framework_docs.framework_name}_features.json",
                    f"{instance_number:02d}_{self.framework_docs.framework_name}_services.json",
                ],
            },
        }

    def generate_dto_chunk(
        self, instance_number: int, detailed: bool = False
    ) -> Dict[str, Any]:
        """Generate DTO chunk with optional detailed information"""
        if not self.framework_docs:
            raise ValueError("FrameworkDocs required for DTO chunk")

        suffix = "_details" if detailed else ""
        title = f"{self.framework_docs.framework_name} DTOs"
        if detailed:
            title += " (Detailed)"

        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": title,
            "description": f"Data Transfer Objects for {self.framework_docs.framework_name}",
            "version": self.schema_version,
            "instance_number": instance_number,
            "schema_type": f"dto_chunk{suffix}",
            "content_type": "detailed_information" if detailed else "summary_information",
            "ai_usage": {
                "purpose": f"{'Complete' if detailed else 'Summary'} DTO information for code generation",
                "token_efficiency": f"{'Full details' if detailed else 'Compact summaries'} for optimal consumption",
            },
            "dtos": [],
        }

        for dto in self.framework_docs.dtos:
            if detailed:
                # Include full details
                dto_info = {
                    "name": dto.name,
                    "docstring": dto.docstring,
                    "fields": [
                        {
                            "name": field_name,
                            "type": field_info.get("type", "any"),
                            "default": field_info.get("default"),
                            "required": field_info.get("required", False),
                            "description": field_info.get("description", ""),
                        }
                        for field_name, field_info in dto.fields.items()
                    ],
                    "ai_hints": {
                        "business_domain": self._infer_business_domain(dto.name),
                        "complexity": self._assess_dto_complexity(dto.fields),
                        "usage_pattern": (
                            "command"
                            if "Command" in dto.name
                            else "response" if "Response" in dto.name else "data"
                        ),
                    },
                }
            else:
                # Include only summary
                dto_info = {
                    "name": dto.name,
                    "docstring": dto.docstring,
                    "field_count": len(dto.fields),
                    "field_names": list(dto.fields.keys()),
                    "business_domain": self._infer_business_domain(dto.name),
                }

            schema["dtos"].append(dto_info)

        return schema

    def generate_feature_chunk(
        self, instance_number: int, detailed: bool = False
    ) -> Dict[str, Any]:
        """Generate Feature chunk with optional detailed information"""
        if not self.framework_docs:
            raise ValueError("FrameworkDocs required for Feature chunk")

        suffix = "_details" if detailed else ""
        title = f"{self.framework_docs.framework_name} Features"
        if detailed:
            title += " (Detailed)"

        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": title,
            "description": f"Features (use cases) for {self.framework_docs.framework_name}",
            "version": self.schema_version,
            "instance_number": instance_number,
            "schema_type": f"feature_chunk{suffix}",
            "content_type": "detailed_information" if detailed else "summary_information",
            "ai_usage": {
                "purpose": f"{'Complete' if detailed else 'Summary'} Feature information for understanding business logic",
                "token_efficiency": f"{'Full implementation details' if detailed else 'Overview and patterns'} for optimal consumption",
            },
            "features": [],
        }

        for feature in self.framework_docs.features:
            if detailed:
                # Include full details
                feature_info = {
                    "name": feature.name,
                    "docstring": feature.docstring,
                    "input_dto": getattr(feature, "input_dto_name", None),
                    "methods": [
                        {
                            "name": method.name,
                            "docstring": method.docstring,
                            "parameters": method.parameters,
                            "return_type": method.return_type,
                        }
                        for method in feature.methods.values()
                    ],
                    "ai_hints": {
                        "business_domain": self._infer_business_domain(feature.name),
                        "complexity": self._assess_feature_complexity(feature),
                        "execution_pattern": "synchronous",  # Could be enhanced with analysis
                    },
                }
            else:
                # Include only summary
                feature_info = {
                    "name": feature.name,
                    "docstring": feature.docstring,
                    "input_dto": getattr(feature, "input_dto_name", None),
                    "method_count": len(feature.methods),
                    "business_domain": self._infer_business_domain(feature.name),
                }

            schema["features"].append(feature_info)

        return schema

    def generate_service_chunk(
        self, instance_number: int, detailed: bool = False
    ) -> Dict[str, Any]:
        """Generate ApplicationService chunk with optional detailed information"""
        if not self.framework_docs:
            raise ValueError("FrameworkDocs required for Service chunk")

        suffix = "_details" if detailed else ""
        title = f"{self.framework_docs.framework_name} Application Services"
        if detailed:
            title += " (Detailed)"

        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": title,
            "description": f"Application Services (orchestrators) for {self.framework_docs.framework_name}",
            "version": self.schema_version,
            "instance_number": instance_number,
            "schema_type": f"service_chunk{suffix}",
            "content_type": "detailed_information" if detailed else "summary_information",
            "ai_usage": {
                "purpose": f"{'Complete' if detailed else 'Summary'} Service information for understanding orchestration",
                "token_efficiency": f"{'Full orchestration details' if detailed else 'Overview and patterns'} for optimal consumption",
            },
            "application_services": [],
        }

        for service in self.framework_docs.application_services:
            if detailed:
                # Include full details
                service_info = {
                    "name": service.name,
                    "docstring": service.docstring,
                    "input_dto": getattr(service, "input_dto_name", None),
                    "methods": [
                        {
                            "name": method.name,
                            "docstring": method.docstring,
                            "parameters": method.parameters,
                            "return_type": method.return_type,
                        }
                        for method in service.methods.values()
                    ],
                    "ai_hints": {
                        "business_domain": self._infer_business_domain(service.name),
                        "complexity": self._assess_service_complexity(service),
                        "orchestration_pattern": "feature_bus",  # Based on framework pattern
                    },
                }
            else:
                # Include only summary
                service_info = {
                    "name": service.name,
                    "docstring": service.docstring,
                    "input_dto": getattr(service, "input_dto_name", None),
                    "method_count": len(service.methods),
                    "business_domain": self._infer_business_domain(service.name),
                }

            schema["application_services"].append(service_info)

        return schema

    def generate_all_chunks(self, output_dir: str, instance_number: int) -> List[str]:
        """Generate all chunks for a framework instance"""
        if not self.framework_docs:
            raise ValueError("FrameworkDocs required for generating chunks")

        generated_files = []
        framework_name = self.framework_docs.framework_name

        # Instance overview
        overview_path = os.path.join(
            output_dir, f"{instance_number:02d}_{framework_name}_context.json"
        )
        overview = self.generate_instance_overview(instance_number)
        self._save_chunk_to_file(overview, overview_path)
        generated_files.append(overview_path)

        # DTO chunks (summary and detailed)
        dto_summary_path = os.path.join(
            output_dir, f"{instance_number:02d}_{framework_name}_dtos.json"
        )
        dto_summary = self.generate_dto_chunk(instance_number, detailed=False)
        self._save_chunk_to_file(dto_summary, dto_summary_path)
        generated_files.append(dto_summary_path)

        dto_detail_path = os.path.join(
            output_dir, f"{instance_number:02d}_{framework_name}_dtos_details.json"
        )
        dto_detail = self.generate_dto_chunk(instance_number, detailed=True)
        self._save_chunk_to_file(dto_detail, dto_detail_path)
        generated_files.append(dto_detail_path)

        # Feature chunks (summary and detailed)
        feature_summary_path = os.path.join(
            output_dir, f"{instance_number:02d}_{framework_name}_features.json"
        )
        feature_summary = self.generate_feature_chunk(instance_number, detailed=False)
        self._save_chunk_to_file(feature_summary, feature_summary_path)
        generated_files.append(feature_summary_path)

        feature_detail_path = os.path.join(
            output_dir, f"{instance_number:02d}_{framework_name}_features_details.json"
        )
        feature_detail = self.generate_feature_chunk(instance_number, detailed=True)
        self._save_chunk_to_file(feature_detail, feature_detail_path)
        generated_files.append(feature_detail_path)

        # Service chunks (summary and detailed) - only if services exist
        if self.framework_docs.application_services:
            service_summary_path = os.path.join(
                output_dir, f"{instance_number:02d}_{framework_name}_services.json"
            )
            service_summary = self.generate_service_chunk(instance_number, detailed=False)
            self._save_chunk_to_file(service_summary, service_summary_path)
            generated_files.append(service_summary_path)

            service_detail_path = os.path.join(
                output_dir, f"{instance_number:02d}_{framework_name}_services_details.json"
            )
            service_detail = self.generate_service_chunk(instance_number, detailed=True)
            self._save_chunk_to_file(service_detail, service_detail_path)
            generated_files.append(service_detail_path)

        return generated_files

    def save_framework_context_to_file(self, output_path: str):
        """Save the shared framework context to a file"""
        context = self.generate_framework_context()
        self._save_chunk_to_file(context, output_path)
        return output_path

    def _save_chunk_to_file(self, chunk: Dict[str, Any], output_path: str):
        """Save a chunk to a JSON file"""
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(chunk, f, indent=2, ensure_ascii=False, default=str)

    # Helper methods for business domain inference and complexity assessment
    def _infer_business_domain(self, component_name: str) -> str:
        """Infer business domain from component name"""
        name_lower = component_name.lower()

        if any(term in name_lower for term in ["payment", "pay", "transaction", "billing"]):
            return "payments"
        elif any(term in name_lower for term in ["user", "customer", "profile", "account"]):
            return "user_management"
        elif any(term in name_lower for term in ["order", "cart", "checkout", "purchase"]):
            return "orders"
        elif any(term in name_lower for term in ["auth", "login", "token", "security"]):
            return "authentication"
        elif any(term in name_lower for term in ["notification", "email", "sms", "alert"]):
            return "notifications"
        else:
            return "general"

    def _assess_dto_complexity(self, fields: Dict[str, Any]) -> str:
        """Assess DTO complexity based on field count and types"""
        field_count = len(fields) if isinstance(fields, dict) else len(fields)
        if field_count <= 3:
            return "simple"
        elif field_count <= 7:
            return "medium"
        else:
            return "complex"

    def _assess_feature_complexity(self, feature: ClassMetadata) -> str:
        """Assess Feature complexity based on methods and parameters"""
        total_params = sum(len(method.parameters) for method in feature.methods.values())
        if len(feature.methods) <= 1 and total_params <= 3:
            return "simple"
        elif len(feature.methods) <= 2 and total_params <= 6:
            return "medium"
        else:
            return "complex"

    def _assess_service_complexity(self, service: ClassMetadata) -> str:
        """Assess Service complexity based on methods and parameters"""
        total_params = sum(len(method.parameters) for method in service.methods.values())
        if len(service.methods) <= 1 and total_params <= 3:
            return "simple"
        elif len(service.methods) <= 2 and total_params <= 6:
            return "medium"
        else:
            return "complex"
