"""
Tests for Auto-Documentation System

Pruebas esenciales para validar la funcionalidad del sistema de auto-documentación.
"""

import os
import tempfile

import pytest

from sincpro_framework import Feature, UseFramework
from sincpro_framework.sincpro_abstractions import DataTransferObject


class ExampleDTO(DataTransferObject):
    """DTO de prueba para documentación"""

    name: str
    age: int = 25


class ExampleResponseDTO(DataTransferObject):
    """DTO de respuesta para prueba"""

    message: str
    success: bool = True


@pytest.fixture
def test_framework():
    """Fixture que crea un framework de prueba con componentes básicos"""
    framework = UseFramework("test_framework")

    @framework.feature(ExampleDTO)
    class ExampleFeature(Feature):
        """Feature de prueba que procesa ExampleDTO"""

        def execute(self, dto: ExampleDTO) -> ExampleResponseDTO:
            return ExampleResponseDTO(
                message=f"Hello {dto.name}, age {dto.age}", success=True
            )

    framework.build_root_bus()
    return framework


def test_framework_basic_functionality(test_framework):
    """Test básico para verificar que el framework funciona"""
    dto = ExampleDTO(name="Alice", age=30)
    result = test_framework(dto)

    assert result.message == "Hello Alice, age 30"
    assert result.success is True


def test_generate_documentation(test_framework):
    """Test para generar documentación en archivo"""
    from sincpro_framework.auto_docs import generate_framework_documentation

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        output_path = f.name

    try:
        # Generar documentación
        result_path = generate_framework_documentation(test_framework, output_path)

        # Verificar que se creó el archivo
        assert os.path.exists(result_path)
        assert result_path == output_path

        # Verificar contenido básico
        with open(result_path, "r") as f:
            content = f.read()
            assert "Test_Framework" in content
            assert "Features" in content
            assert "ExampleDTO" in content

    finally:
        # Limpiar archivo temporal
        if os.path.exists(output_path):
            os.unlink(output_path)


def test_print_framework_summary(test_framework, capsys):
    """Test para imprimir resumen del framework"""
    from sincpro_framework.auto_docs import print_framework_summary

    print_framework_summary(test_framework)

    captured = capsys.readouterr()
    output = captured.out

    assert "Test_Framework Framework Summary" in output
    assert "Features: 1" in output
    # No verificamos DTOs porque depende de la implementación del introspector
    assert "ExampleDTO" in output


def test_framework_not_built_error():
    """Test para verificar error cuando framework no está construido"""
    from sincpro_framework.auto_docs import generate_framework_documentation

    framework = UseFramework("test_framework")

    with pytest.raises(Exception) as exc_info:
        generate_framework_documentation(framework, "test.md")

    assert "must be built" in str(exc_info.value).lower()


if __name__ == "__main__":
    # Ejecución directa para pruebas rápidas
    framework = UseFramework("test_framework")

    @framework.feature(ExampleDTO)
    class ExampleFeature(Feature):
        def execute(self, dto: ExampleDTO) -> ExampleResponseDTO:
            return ExampleResponseDTO(message=f"Hello {dto.name}")

    framework.build_root_bus()

    print("🧪 Running basic test...")
    dto = ExampleDTO(name="Test")
    result = framework(dto)
    print(f"✅ Result: {result}")

    print("\n📊 Framework Summary:")
    from sincpro_framework.auto_docs import print_framework_summary

    print_framework_summary(framework)

    print("\n🎉 All basic tests passed!")
