"""
Tests for Auto-Documentation System

Essential tests to validate the functionality of the auto-documentation system,
including the new JSON schema generation for AI consumption.
"""

import json
import os
import tempfile

import pytest

from sincpro_framework import Feature, UseFramework, ApplicationService
from sincpro_framework.sincpro_abstractions import DataTransferObject


class ExampleDTO(DataTransferObject):
    """DTO de prueba para documentación"""

    name: str
    age: int = 25


class ExampleResponseDTO(DataTransferObject):
    """DTO de respuesta para prueba"""

    message: str
    success: bool = True


class ProcessCommandDTO(DataTransferObject):
    """DTO for processing complex operations"""
    
    operation_id: str
    data: str
    priority: int = 1


class ServiceProcessCommandDTO(DataTransferObject):
    """DTO for service-level processing operations"""
    
    operation_id: str
    data: str
    priority: int = 1


@pytest.fixture
def test_framework():
    """Fixture que crea un framework de prueba con componentes básicos"""
    framework = UseFramework("test_framework")

    # Add a test dependency
    class TestDatabase:
        """Test database for documentation testing"""
        def save(self, data):
            return f"saved_{id(data)}"

    framework.add_dependency("test_db", TestDatabase())

    @framework.feature(ExampleDTO)
    class ExampleFeature(Feature):
        """Feature de prueba que procesa ExampleDTO"""
        
        test_db: TestDatabase

        def execute(self, dto: ExampleDTO) -> ExampleResponseDTO:
            return ExampleResponseDTO(
                message=f"Hello {dto.name}, age {dto.age}", success=True
            )

    @framework.feature(ProcessCommandDTO)
    class ProcessFeature(Feature):
        """Feature that processes complex operations"""
        
        test_db: TestDatabase
        
        def execute(self, dto: ProcessCommandDTO) -> ExampleResponseDTO:
            result = self.test_db.save(dto)
            return ExampleResponseDTO(
                message=f"Processed {dto.operation_id}", success=True
            )

    # Add an application service with different DTO
    @framework.app_service(ServiceProcessCommandDTO)
    class ComplexProcessor(ApplicationService):
        """Application service that orchestrates complex processing"""
        
        test_db: TestDatabase
        
        def execute(self, dto: ServiceProcessCommandDTO) -> ExampleResponseDTO:
            # Orchestrate features - convert to feature DTO
            process_dto = ProcessCommandDTO(
                operation_id=dto.operation_id,
                data=dto.data,
                priority=dto.priority
            )
            return self.feature_bus.execute(process_dto)

    framework.build_root_bus()
    return framework


def test_framework_basic_functionality(test_framework):
    """Test básico para verificar que el framework funciona"""
    dto = ExampleDTO(name="Alice", age=30)
    result = test_framework(dto, ExampleResponseDTO)

    assert result.message == "Hello Alice, age 30"
    assert result.success is True


def test_generate_markdown_documentation(test_framework):
    """Test para generar documentación markdown usando el método del framework"""

    with tempfile.TemporaryDirectory() as temp_dir:
        # Generate markdown documentation
        from sincpro_framework.generate_documentation import build_documentation
        
        result_path = build_documentation(
            test_framework, 
            output_dir=temp_dir,
            format="markdown"
        )

        # Verify that the site was created
        assert os.path.exists(result_path)
        
        # Check for key files
        assert os.path.exists(os.path.join(temp_dir, "mkdocs.yml"))
        assert os.path.exists(os.path.join(temp_dir, "docs", "index.md"))
        assert os.path.exists(os.path.join(temp_dir, "docs", "features.md"))
        assert os.path.exists(os.path.join(temp_dir, "docs", "dtos.md"))

        # Verify content basic
        with open(os.path.join(temp_dir, "docs", "index.md"), "r") as f:
            content = f.read()
            assert "test_framework" in content
            assert "2" in content  # Number of features


def test_generate_json_schema(test_framework):
    """Test para generar JSON schema para consumo de IA"""

    with tempfile.TemporaryDirectory() as temp_dir:
        # Generate JSON schema
        from sincpro_framework.generate_documentation import build_documentation
        
        result_path = build_documentation(
            test_framework,
            output_dir=temp_dir,
            format="json"
        )

        # Verify that the JSON schema was created
        assert os.path.exists(result_path)
        assert result_path.endswith("_schema.json")

        # Load and verify JSON schema content
        with open(result_path, "r") as f:
            schema = json.load(f)

        # Verify schema structure
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["title"] == "test_framework Framework Schema"
        assert schema["version"] == "1.0.0"
        assert "metadata" in schema
        assert "components" in schema
        assert "ai_metadata" in schema

        # Verify metadata
        metadata = schema["metadata"]
        assert metadata["name"] == "test_framework"
        assert metadata["type"] == "sincpro_framework"
        assert "architecture_patterns" in metadata
        assert "component_summary" in metadata

        # Verify components
        components = schema["components"]
        assert "dtos" in components
        assert "features" in components
        assert "application_services" in components
        assert "dependencies" in components

        # Verify DTOs
        dtos = components["dtos"]
        assert len(dtos) >= 2  # ExampleDTO and ProcessCommandDTO
        
        # Find ExampleDTO
        example_dto = next((dto for dto in dtos if dto["name"] == "ExampleDTO"), None)
        assert example_dto is not None
        assert example_dto["type"] == "data_transfer_object"
        assert example_dto["purpose"] == "data_validation_serialization"
        assert "fields" in example_dto
        assert "ai_hints" in example_dto

        # Verify Features
        features = components["features"]
        assert len(features) >= 2  # ExampleFeature and ProcessFeature
        
        # Find ExampleFeature
        example_feature = next((feat for feat in features if feat["name"] == "ExampleFeature"), None)
        assert example_feature is not None
        assert example_feature["type"] == "feature"
        assert example_feature["pattern"] == "command_pattern"
        assert "execute_method" in example_feature
        assert "ai_hints" in example_feature

        # Verify Application Services
        app_services = components["application_services"]
        assert len(app_services) >= 1
        
        # Find ComplexProcessor
        processor = next((svc for svc in app_services if svc["name"] == "ComplexProcessor"), None)
        assert processor is not None
        assert processor["type"] == "application_service"
        assert processor["purpose"] == "orchestration_coordination"

        # Verify AI metadata
        ai_metadata = schema["ai_metadata"]
        assert "embedding_suggestions" in ai_metadata
        assert "code_generation_hints" in ai_metadata
        assert "complexity_analysis" in ai_metadata

        # Verify embedding suggestions
        embedding = ai_metadata["embedding_suggestions"]
        assert "primary_entities" in embedding
        assert "business_capabilities" in embedding
        assert len(embedding["primary_entities"]) >= 2

        # Verify code generation hints
        code_hints = ai_metadata["code_generation_hints"]
        assert "framework_patterns" in code_hints
        assert "common_imports" in code_hints
        assert "naming_conventions" in code_hints


def test_generate_both_formats(test_framework):
    """Test para generar ambos formatos: markdown y JSON"""

    with tempfile.TemporaryDirectory() as temp_dir:
        # Generate both formats
        from sincpro_framework.generate_documentation import build_documentation
        
        result_path = build_documentation(
            test_framework,
            output_dir=temp_dir,
            format="both"
        )

        # Verify that both formats were created
        assert os.path.exists(result_path)
        
        # Check for markdown files
        assert os.path.exists(os.path.join(temp_dir, "mkdocs.yml"))
        assert os.path.exists(os.path.join(temp_dir, "docs", "index.md"))
        assert os.path.exists(os.path.join(temp_dir, "site"))  # Built site
        
        # Check for JSON schema
        json_files = [f for f in os.listdir(temp_dir) if f.endswith("_schema.json")]
        assert len(json_files) >= 1
        
        # Verify JSON schema content
        json_path = os.path.join(temp_dir, json_files[0])
        with open(json_path, "r") as f:
            schema = json.load(f)
        
        assert schema["title"] == "test_framework Framework Schema"


def test_direct_json_schema_generation():
    """Test directo de generación de JSON schema usando generate_json_schema"""
    
    from sincpro_framework.generate_documentation import generate_json_schema
    from sincpro_framework.generate_documentation.infrastructure.sincpro_introspector import component_finder
    from sincpro_framework.generate_documentation.infrastructure.framework_docs_extractor import doc_extractor
    
    # Create simple framework
    framework = UseFramework("direct_test")
    
    @framework.feature(ExampleDTO)
    class SimpleFeature(Feature):
        def execute(self, dto: ExampleDTO) -> ExampleResponseDTO:
            return ExampleResponseDTO(message="simple", success=True)
    
    framework.build_root_bus()
    
    # Extract documentation
    introspector_instance = component_finder.introspect(framework)
    doc = doc_extractor.extract_framework_docs(introspector_instance)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # Generate JSON schema directly
        result_path = generate_json_schema([doc], temp_dir)
        
        # Verify result
        assert os.path.exists(result_path)
        assert "direct_test_schema.json" in result_path
        
        # Verify content
        with open(result_path, "r") as f:
            schema = json.load(f)
        
        assert schema["metadata"]["name"] == "direct_test"
        assert len(schema["components"]["features"]) == 1


def test_json_schema_ai_optimization(test_framework):
    """Test específico para verificar optimizaciones para IA"""
    
    with tempfile.TemporaryDirectory() as temp_dir:
        from sincpro_framework.generate_documentation import build_documentation
        
        result_path = build_documentation(
            test_framework,
            output_dir=temp_dir,
            format="json"
        )
        
        with open(result_path, "r") as f:
            schema = json.load(f)
        
        # Verify AI-specific optimizations
        ai_metadata = schema["ai_metadata"]
        
        # Check embedding suggestions
        embedding = ai_metadata["embedding_suggestions"]
        assert isinstance(embedding["primary_entities"], list)
        assert isinstance(embedding["business_capabilities"], list)
        assert isinstance(embedding["data_flow_patterns"], list)
        
        # Check code generation hints
        code_hints = ai_metadata["code_generation_hints"]
        assert "decorator_based_registration" in code_hints["framework_patterns"]
        assert "dependency_injection" in code_hints["framework_patterns"]
        assert any("sincpro_framework" in imp for imp in code_hints["common_imports"])
        
        # Check complexity analysis
        complexity = ai_metadata["complexity_analysis"]
        assert complexity["overall_complexity"] in ["simple", "medium", "complex"]
        assert isinstance(complexity["most_complex_components"], list)
        assert isinstance(complexity["simplest_components"], list)
        
        # Verify DTO AI hints
        dtos = schema["components"]["dtos"]
        for dto in dtos:
            ai_hints = dto["ai_hints"]
            assert "is_input_type" in ai_hints
            assert "is_output_type" in ai_hints
            assert "complexity_level" in ai_hints
            assert "validation_rules" in ai_hints
        
        # Verify Feature AI hints
        features = schema["components"]["features"]
        for feature in features:
            ai_hints = feature["ai_hints"]
            assert "is_synchronous" in ai_hints
            assert "has_side_effects" in ai_hints
            assert "complexity_level" in ai_hints
            assert "business_domain" in ai_hints


if __name__ == "__main__":
    # Ejecución directa para pruebas rápidas
    framework = UseFramework("test_framework")

    @framework.feature(ExampleDTO)
    class ExampleFeature(Feature):
        def execute(self, dto) -> ExampleResponseDTO:
            return ExampleResponseDTO(message=f"Hello {dto.name}")

    framework.build_root_bus()

    print("🧪 Running basic test...")
    dto = ExampleDTO(name="Test")
    result = framework(dto, ExampleResponseDTO)
    print(f"✅ Result: {result}")

    print("\n📊 Testing JSON Schema generation...")
    from sincpro_framework.generate_documentation import build_documentation
    
    with tempfile.TemporaryDirectory() as temp_dir:
        json_path = build_documentation(framework, output_dir=temp_dir, format="json")
        print(f"✅ JSON Schema generated: {json_path}")
        
        with open(json_path, "r") as f:
            schema = json.load(f)
        print(f"✅ Schema contains {len(schema['components']['dtos'])} DTOs and {len(schema['components']['features'])} Features")

    print("\n🎉 All basic tests passed!")
