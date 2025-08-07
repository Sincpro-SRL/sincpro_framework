"""
Tests for Auto-Documentation System

Essential tests to validate the functionality of the auto-documentation system,
including the new JSON schema generation for AI consumption.
"""

import json
import os
import tempfile

import pytest

from sincpro_framework import ApplicationService, Feature, UseFramework
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
            self.test_db.save(dto)
            return ExampleResponseDTO(message=f"Processed {dto.operation_id}", success=True)

    # Add an application service with different DTO
    @framework.app_service(ServiceProcessCommandDTO)
    class ComplexProcessor(ApplicationService):
        """Application service that orchestrates complex processing"""

        test_db: TestDatabase

        def execute(self, dto: ServiceProcessCommandDTO) -> ExampleResponseDTO:
            # Orchestrate features - convert to feature DTO
            process_dto = ProcessCommandDTO(
                operation_id=dto.operation_id, data=dto.data, priority=dto.priority
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
            test_framework, output_dir=temp_dir, format="markdown"
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
    """Test para generar JSON schema chunked para consumo de IA (nuevo formato por defecto)"""

    with tempfile.TemporaryDirectory() as temp_dir:
        # Generate JSON schema - now chunked by default
        from sincpro_framework.generate_documentation import build_documentation

        result_path = build_documentation(test_framework, output_dir=temp_dir, format="json")

        # Verify that the AI context directory was created
        ai_context_dir = os.path.join(temp_dir, "ai_context")
        assert os.path.exists(ai_context_dir)
        assert result_path == ai_context_dir

        # Check that framework context file exists
        framework_context_path = os.path.join(ai_context_dir, "01_framework_context.json")
        assert os.path.exists(framework_context_path)

        # Check that instance files exist
        instance_context_path = os.path.join(ai_context_dir, "01_test_framework_context.json")
        assert os.path.exists(instance_context_path)

        # Load and verify framework context
        with open(framework_context_path, "r") as f:
            framework_context = json.load(f)

        assert framework_context["schema_type"] == "framework_context"
        assert "framework_context" in framework_context
        assert framework_context["framework_context"]["framework_name"] == "Sincpro Framework"

        # Load and verify instance context
        with open(instance_context_path, "r") as f:
            instance_data = json.load(f)

        assert instance_data["schema_type"] == "instance_overview"
        assert "framework_instance" in instance_data
        assert instance_data["framework_instance"]["name"] == "test_framework"

        # Verify component summary contains expected data
        component_summary = instance_data["framework_instance"]["component_summary"]
        assert component_summary["dtos"]["count"] >= 2  # ExampleDTO and ProcessCommandDTO
        assert component_summary["features"]["count"] >= 2  # ExampleFeature and ProcessFeature

        # Check DTO files
        dto_summary_path = os.path.join(ai_context_dir, "01_test_framework_dtos.json")
        dto_details_path = os.path.join(ai_context_dir, "01_test_framework_dtos_details.json")
        assert os.path.exists(dto_summary_path)
        assert os.path.exists(dto_details_path)

        # Check Feature files
        feature_summary_path = os.path.join(ai_context_dir, "01_test_framework_features.json")
        feature_details_path = os.path.join(ai_context_dir, "01_test_framework_features_details.json")
        assert os.path.exists(feature_summary_path)
        assert os.path.exists(feature_details_path)

        # Verify DTOs summary content
        with open(dto_summary_path, "r") as f:
            dto_summary = json.load(f)

        assert dto_summary["schema_type"] == "dto_chunk"
        assert dto_summary["content_type"] == "summary_information"
        assert len(dto_summary["dtos"]) >= 2

        # Check DTO summary structure
        sample_dto = dto_summary["dtos"][0]
        assert "name" in sample_dto
        assert "field_count" in sample_dto
        assert "field_names" in sample_dto
        assert "business_domain" in sample_dto

        # Verify Features summary content
        with open(feature_summary_path, "r") as f:
            feature_summary = json.load(f)

        assert feature_summary["schema_type"] == "feature_chunk"
        assert feature_summary["content_type"] == "summary_information"
        assert len(feature_summary["features"]) >= 2

        # Check Feature summary structure
        sample_feature = feature_summary["features"][0]
        assert "name" in sample_feature
        assert "input_dto" in sample_feature
        assert "method_count" in sample_feature
        assert "business_domain" in sample_feature


def test_generate_both_formats(test_framework):
    """Test para generar ambos formatos: markdown y JSON chunked"""

    with tempfile.TemporaryDirectory() as temp_dir:
        # Generate both formats
        from sincpro_framework.generate_documentation import build_documentation

        result_path = build_documentation(test_framework, output_dir=temp_dir, format="both")

        # Verify that both formats were created
        assert os.path.exists(result_path)

        # Check for markdown files
        assert os.path.exists(os.path.join(temp_dir, "mkdocs.yml"))
        assert os.path.exists(os.path.join(temp_dir, "docs", "index.md"))
        assert os.path.exists(os.path.join(temp_dir, "site"))  # Built site

        # Check for chunked JSON schema in ai_context subdirectory
        ai_context_dir = os.path.join(temp_dir, "ai_context")
        assert os.path.exists(ai_context_dir)

        # Check that framework context and instance files exist
        framework_context_path = os.path.join(ai_context_dir, "01_framework_context.json")
        instance_context_path = os.path.join(ai_context_dir, "01_test_framework_context.json")
        assert os.path.exists(framework_context_path)
        assert os.path.exists(instance_context_path)

        # Verify JSON content
        with open(instance_context_path, "r") as f:
            instance_data = json.load(f)

        assert instance_data["schema_type"] == "instance_overview"
        assert "framework_instance" in instance_data
        assert instance_data["framework_instance"]["name"] == "test_framework"


def test_direct_chunked_json_schema_generation():
    """Test directo de generación de JSON schema chunked usando generate_chunked_json_schema"""

    from sincpro_framework.generate_documentation import generate_chunked_json_schema
    from sincpro_framework.generate_documentation.infrastructure.framework_docs_extractor import (
        doc_extractor,
    )
    from sincpro_framework.generate_documentation.infrastructure.sincpro_introspector import (
        component_finder,
    )

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
        # Generate chunked JSON schema directly
        result_dir = generate_chunked_json_schema([doc], temp_dir)

        # Verify result is ai_context directory
        ai_context_dir = os.path.join(temp_dir, "ai_context")
        assert os.path.exists(ai_context_dir)
        assert result_dir == ai_context_dir

        # Check that framework context file exists
        framework_context_path = os.path.join(ai_context_dir, "01_framework_context.json")
        assert os.path.exists(framework_context_path)

        # Check that instance files exist
        instance_context_path = os.path.join(ai_context_dir, "01_direct_test_context.json")
        assert os.path.exists(instance_context_path)

        # Verify content
        with open(instance_context_path, "r") as f:
            instance_data = json.load(f)

        assert instance_data["schema_type"] == "instance_overview"
        assert "framework_instance" in instance_data
        assert instance_data["framework_instance"]["name"] == "direct_test"


def test_json_schema_ai_optimization(test_framework):
    """Test específico para verificar optimizaciones para IA en formato chunked"""

    with tempfile.TemporaryDirectory() as temp_dir:
        from sincpro_framework.generate_documentation import build_documentation

        result_path = build_documentation(test_framework, output_dir=temp_dir, format="json")

        ai_context_dir = os.path.join(temp_dir, "ai_context")

        # Verify framework context contains AI optimizations
        framework_context_path = os.path.join(ai_context_dir, "01_framework_context.json")
        with open(framework_context_path, "r") as f:
            framework_context = json.load(f)

        # Check AI usage information in framework context
        ai_usage = framework_context["ai_usage"]
        assert ai_usage["purpose"] is not None
        assert "token_efficiency" in ai_usage
        assert "next_steps" in ai_usage

        # Verify instance context has AI usage information
        instance_context_path = os.path.join(ai_context_dir, "01_test_framework_context.json")
        with open(instance_context_path, "r") as f:
            instance_data = json.load(f)

        # Check ai_usage in instance context
        ai_usage_instance = instance_data["ai_usage"]
        assert ai_usage_instance["purpose"] is not None
        assert "token_efficiency" in ai_usage_instance
        assert "next_steps" in ai_usage_instance

        # Check DTO details file for AI hints
        dto_details_path = os.path.join(ai_context_dir, "01_test_framework_dtos_details.json")
        with open(dto_details_path, "r") as f:
            dto_details = json.load(f)

        # Verify DTO AI hints
        for dto in dto_details["dtos"]:
            ai_hints = dto["ai_hints"]
            assert "business_domain" in ai_hints
            assert "complexity" in ai_hints
            assert "usage_pattern" in ai_hints

        # Check Feature details file for AI hints
        feature_details_path = os.path.join(ai_context_dir, "01_test_framework_features_details.json")
        with open(feature_details_path, "r") as f:
            feature_details = json.load(f)

        # Verify Feature AI hints
        for feature in feature_details["features"]:
            ai_hints = feature["ai_hints"]
            assert "business_domain" in ai_hints
            assert "complexity" in ai_hints
            assert "execution_pattern" in ai_hints


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
        json_dir = build_documentation(framework, output_dir=temp_dir, format="json")
        print(f"✅ Chunked JSON Schema generated: {json_dir}")

        # Check framework context
        framework_context_path = os.path.join(json_dir, "01_framework_context.json")
        instance_context_path = os.path.join(json_dir, "01_test_framework_context.json")
        
        with open(instance_context_path, "r") as f:
            instance_data = json.load(f)
        
        component_summary = instance_data["framework_instance"]["component_summary"]
        print(
            f"✅ Schema contains {component_summary['dtos']['count']} DTOs and {component_summary['features']['count']} Features"
        )

    print("\n🎉 All basic tests passed!")


def test_chunked_json_generation(test_framework):
    """Test the chunked JSON generation functionality (now the default)"""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Test chunked generation (now the default and only option)
        from sincpro_framework.generate_documentation import build_documentation
        
        result_dir = build_documentation(
            test_framework,
            output_dir=temp_dir,
            format="json"
        )
        
        ai_context_dir = os.path.join(temp_dir, "ai_context")
        assert os.path.exists(ai_context_dir), "AI context directory should be created"
        
        # Check that framework context file exists
        framework_context_path = os.path.join(ai_context_dir, "01_framework_context.json")
        assert os.path.exists(framework_context_path), "Framework context file should exist"
        
        # Check that instance files exist
        instance_context_path = os.path.join(ai_context_dir, "01_test_framework_context.json")
        assert os.path.exists(instance_context_path), "Instance context file should exist"
        
        # Check DTO files
        dto_summary_path = os.path.join(ai_context_dir, "01_test_framework_dtos.json")
        dto_details_path = os.path.join(ai_context_dir, "01_test_framework_dtos_details.json")
        assert os.path.exists(dto_summary_path), "DTO summary file should exist"
        assert os.path.exists(dto_details_path), "DTO details file should exist"
        
        # Check Feature files
        feature_summary_path = os.path.join(ai_context_dir, "01_test_framework_features.json")
        feature_details_path = os.path.join(ai_context_dir, "01_test_framework_features_details.json")
        assert os.path.exists(feature_summary_path), "Feature summary file should exist"
        assert os.path.exists(feature_details_path), "Feature details file should exist"
        
        # Validate file sizes are reasonable (much smaller than consolidated)
        framework_size = os.path.getsize(framework_context_path)
        instance_size = os.path.getsize(instance_context_path)
        dto_summary_size = os.path.getsize(dto_summary_path)
        dto_details_size = os.path.getsize(dto_details_path)
        
        # Framework context should be largest (contains full guide)
        assert framework_size > 50000, f"Framework context should be substantial: {framework_size}"
        
        # Instance files should be much smaller
        assert instance_size < 5000, f"Instance context should be compact: {instance_size}"
        assert dto_summary_size < 2000, f"DTO summary should be compact: {dto_summary_size}"
        assert dto_details_size < 5000, f"DTO details should be reasonable: {dto_details_size}"
        
        # Validate JSON structure
        with open(framework_context_path, 'r') as f:
            framework_data = json.load(f)
        
        assert framework_data["schema_type"] == "framework_context"
        assert "framework_context" in framework_data
        assert framework_data["ai_usage"]["purpose"] is not None
        
        with open(instance_context_path, 'r') as f:
            instance_data = json.load(f)
        
        assert instance_data["schema_type"] == "instance_overview"
        assert "framework_instance" in instance_data
        assert "component_summary" in instance_data["framework_instance"]
        
        # Check that component summary contains expected data
        component_summary = instance_data["framework_instance"]["component_summary"]
        assert component_summary["dtos"]["count"] >= 2  # ExampleDTO and ProcessCommandDTO
        assert component_summary["features"]["count"] >= 2  # ExampleFeature and ProcessFeature
        
        with open(dto_summary_path, 'r') as f:
            dto_summary_data = json.load(f)
        
        assert dto_summary_data["schema_type"] == "dto_chunk"
        assert dto_summary_data["content_type"] == "summary_information"
        assert "dtos" in dto_summary_data
        assert len(dto_summary_data["dtos"]) >= 2
        
        # Check DTO summary structure
        sample_dto = dto_summary_data["dtos"][0]
        assert "name" in sample_dto
        assert "field_count" in sample_dto
        assert "field_names" in sample_dto
        assert "business_domain" in sample_dto
        
        with open(dto_details_path, 'r') as f:
            dto_details_data = json.load(f)
        
        assert dto_details_data["schema_type"] == "dto_chunk_details"
        assert dto_details_data["content_type"] == "detailed_information"
        
        # Check DTO details structure
        detailed_dto = dto_details_data["dtos"][0]
        assert "name" in detailed_dto
        assert "fields" in detailed_dto
        assert isinstance(detailed_dto["fields"], list)
        assert "ai_hints" in detailed_dto
        
        print(f"✅ Chunked generation test passed!")
        print(f"   Framework context: {framework_size} bytes")
        print(f"   Instance context: {instance_size} bytes")
        print(f"   DTO summary: {dto_summary_size} bytes")
        print(f"   DTO details: {dto_details_size} bytes")
        print(f"   Total chunked size: {framework_size + instance_size + dto_summary_size + dto_details_size} bytes")


def test_chunked_json_features(test_framework):
    """Test the chunked JSON generation features and file structure"""
    with tempfile.TemporaryDirectory() as temp_dir:
        from sincpro_framework.generate_documentation import build_documentation
        
        # Generate chunked format (now the default and only option)
        result_dir = build_documentation(
            test_framework,
            output_dir=temp_dir,
            format="json"
        )
        
        # Compare expected files
        ai_context_dir = os.path.join(temp_dir, "ai_context")
        chunked_files = [
            os.path.join(ai_context_dir, f)
            for f in os.listdir(ai_context_dir)
            if f.endswith('.json')
        ]
        
        chunked_total_size = sum(os.path.getsize(f) for f in chunked_files)
        
        print(f"✅ Chunked generation completed:")
        print(f"   Total files: {len(chunked_files)}")
        print(f"   Total size: {chunked_total_size} bytes")
        
        framework_context_path = os.path.join(ai_context_dir, "01_framework_context.json")
        framework_context_size = os.path.getsize(framework_context_path)
        instance_specific_size = chunked_total_size - framework_context_size
        
        print(f"   Framework context (reusable): {framework_context_size} bytes")
        print(f"   Instance-specific size: {instance_specific_size} bytes")
        print(f"   📊 For multiple instances, framework context is reused, saving {framework_context_size} bytes per additional instance")
        
        # Verify key files exist
        expected_files = [
            "01_framework_context.json",
            "01_test_framework_context.json",
            "01_test_framework_dtos.json",
            "01_test_framework_dtos_details.json",
            "01_test_framework_features.json",
            "01_test_framework_features_details.json"
        ]
        
        for expected_file in expected_files:
            file_path = os.path.join(ai_context_dir, expected_file)
            assert os.path.exists(file_path), f"Expected file {expected_file} should exist"
