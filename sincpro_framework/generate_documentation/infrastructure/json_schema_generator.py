"""
JSON Schema Generator for AI Consumption

Generates JSON Schema optimized for AI consumption and embedding processes.
This generator creates structured data that can be easily consumed by AI models
for understanding framework components and generating code.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from sincpro_framework.generate_documentation.domain.models import (
    ClassMetadata,
    FrameworkDocs,
    FunctionMetadata,
    PydanticModelMetadata,
)


class AIOptimizedJSONSchemaGenerator:
    """
    Generates JSON Schema optimized for AI consumption and embedding processes.
    
    The output is structured to be easily parsed by AI models and contains
    rich metadata for understanding framework components.
    """
    
    def __init__(self, framework_docs: FrameworkDocs):
        self.framework_docs = framework_docs
        self.schema_version = "1.0.0"
        
    def generate_complete_schema(self) -> Dict[str, Any]:
        """
        Generate complete JSON schema optimized for AI consumption.
        
        Returns:
            Dict[str, Any]: Complete JSON schema with framework metadata
        """
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": f"{self.framework_docs.framework_name} Framework Schema",
            "description": f"AI-optimized schema for {self.framework_docs.framework_name} framework components",
            "version": self.schema_version,
            "generated_at": self.framework_docs.generated_at,
            "generated_by": self.framework_docs.generated_by,
            "metadata": self._generate_framework_metadata(),
            "components": {
                "dtos": self._generate_dto_schemas(),
                "features": self._generate_feature_schemas(),
                "application_services": self._generate_application_service_schemas(),
                "dependencies": self._generate_dependency_schemas(),
                "middlewares": self._generate_middleware_schemas()
            },
            "relationships": self._generate_component_relationships(),
            "ai_metadata": self._generate_ai_metadata()
        }
        
        return schema
    
    def _generate_framework_metadata(self) -> Dict[str, Any]:
        """Generate framework-level metadata for AI understanding"""
        summary = self.framework_docs.summary
        
        return {
            "name": self.framework_docs.framework_name,
            "type": "sincpro_framework",
            "architecture_patterns": [
                "Domain-Driven Design",
                "Clean Architecture", 
                "Hexagonal Architecture",
                "Command Query Separation"
            ],
            "component_summary": {
                "total_components": summary.total_components if summary else 0,
                "dtos_count": summary.dtos_count if summary else 0,
                "features_count": summary.features_count if summary else 0,
                "application_services_count": summary.application_services_count if summary else 0,
                "middlewares_count": summary.middlewares_count if summary else 0,
                "dependencies_count": summary.dependencies_count if summary else 0
            },
            "capabilities": self._extract_framework_capabilities()
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
                    "event_payload"
                ],
                "ai_hints": {
                    "is_input_type": self._is_input_dto(dto.name),
                    "is_output_type": self._is_output_dto(dto.name),
                    "complexity_level": self._assess_dto_complexity(dto.fields),
                    "validation_rules": self._extract_validation_rules(dto.fields)
                }
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
                    "business_domain": self._infer_business_domain(feature.name)
                }
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
                    "business_domain": self._infer_business_domain(service.name)
                }
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
                        "complexity_level": "low"
                    }
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
                        "complexity_level": self._assess_dependency_complexity(dep)
                    }
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
                    "description": middleware.docstring or f"Middleware Function: {middleware.name}",
                    "purpose": "cross_cutting_concerns",
                    "pattern": "middleware_pattern",
                    "execution_order": "pre_post_processing",
                    "ai_hints": {
                        "modifies_request": True,
                        "modifies_response": True,
                        "has_side_effects": True,
                        "complexity_level": "medium"
                    }
                }
            elif isinstance(middleware, ClassMetadata):
                schema = {
                    "type": "middleware_class",
                    "name": middleware.name,
                    "module": middleware.module,
                    "description": middleware.docstring or f"Middleware Class: {middleware.name}",
                    "purpose": "cross_cutting_concerns",
                    "pattern": "middleware_pattern",
                    "methods": self._convert_methods_to_ai_schema(middleware.methods),
                    "ai_hints": {
                        "is_stateful": len(middleware.attributes) > 0,
                        "complexity_level": self._assess_middleware_complexity(middleware)
                    }
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
            "dependency_injection": self._map_dependency_injection()
        }
    
    def _generate_ai_metadata(self) -> Dict[str, Any]:
        """Generate metadata specifically for AI consumption"""
        return {
            "embedding_suggestions": {
                "primary_entities": [dto.name for dto in self.framework_docs.dtos],
                "business_capabilities": [feature.name for feature in self.framework_docs.features],
                "integration_points": self._identify_integration_points(),
                "data_flow_patterns": self._identify_data_flow_patterns()
            },
            "code_generation_hints": {
                "framework_patterns": [
                    "decorator_based_registration",
                    "dependency_injection",
                    "command_pattern",
                    "middleware_pipeline"
                ],
                "common_imports": self._extract_common_imports(),
                "naming_conventions": self._extract_naming_conventions()
            },
            "complexity_analysis": {
                "overall_complexity": self._assess_overall_complexity(),
                "most_complex_components": self._identify_complex_components(),
                "simplest_components": self._identify_simple_components()
            }
        }
    
    # Helper methods for AI-specific analysis
    
    def _convert_pydantic_fields_to_ai_schema(self, fields: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
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
                    "is_amount": "amount" in field_name.lower() or "price" in field_name.lower(),
                    "is_text": "name" in field_name.lower() or "description" in field_name.lower(),
                    "is_status": "status" in field_name.lower() or "state" in field_name.lower()
                }
            }
        return ai_fields
    
    def _convert_methods_to_ai_schema(self, methods: Dict[str, FunctionMetadata]) -> Dict[str, Any]:
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
                    "is_property": method_name.startswith("get_") or method_name.startswith("is_"),
                    "is_action": method_name.startswith("create_") or method_name.startswith("update_") or method_name.startswith("delete_")
                }
            }
        return ai_methods
    
    def _extract_execute_method_details(self, feature: ClassMetadata) -> Optional[Dict[str, Any]]:
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
                "processes_dto": True
            }
        }
    
    def _extract_framework_capabilities(self) -> List[str]:
        """Extract framework capabilities for AI understanding"""
        capabilities = ["dependency_injection", "command_execution"]
        
        if self.framework_docs.middlewares:
            capabilities.append("middleware_pipeline")
        if self.framework_docs.application_services:
            capabilities.append("service_orchestration")
        if any("async" in str(method) for feature in self.framework_docs.features for method in feature.methods.values()):
            capabilities.append("async_processing")
            
        return capabilities
    
    def _is_input_dto(self, dto_name: str) -> bool:
        """Determine if DTO is typically used for input"""
        return "command" in dto_name.lower() or "query" in dto_name.lower() or "request" in dto_name.lower()
    
    def _is_output_dto(self, dto_name: str) -> bool:
        """Determine if DTO is typically used for output"""
        return "response" in dto_name.lower() or "result" in dto_name.lower() or "event" in dto_name.lower()
    
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
            "analytics": "analytics"
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
            return [param.get("type", "any") for param in execute_method.parameters.values() if param.get("name") != "self"]
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
        return not any(keyword in func.name.lower() for keyword in ["save", "create", "update", "delete", "send", "post"])
    
    def _has_side_effects(self, func: FunctionMetadata) -> bool:
        """Determine if function has side effects"""
        return not self._is_pure_function(func)
    
    def _is_external_integration(self, dep: ClassMetadata) -> bool:
        """Determine if dependency provides external integration"""
        integration_keywords = ["adapter", "client", "api", "service", "gateway", "repository"]
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
            "from typing import Any"
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
            "methods": "snake_case"
        }
    
    def _assess_overall_complexity(self) -> str:
        """Assess overall framework complexity"""
        total_components = len(self.framework_docs.dtos) + len(self.framework_docs.features) + len(self.framework_docs.application_services)
        
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
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(schema, f, indent=2, ensure_ascii=False, default=str)
            
        return output_path