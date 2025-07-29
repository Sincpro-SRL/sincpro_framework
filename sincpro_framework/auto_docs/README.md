# 📚 Auto-Documentation System

Sistema de generación automática de documentación para Sincpro Framework basado en introspección de componentes registrados.

## 🏗️ Arquitectura

El sistema sigue el patrón Domain/Infrastructure:

```
sincpro_framework/auto_docs/
├── domain/                    # Interfaces y protocolos
│   ├── __init__.py           # Protocols y tipos base
├── infrastructure/           # Implementaciones concretas  
│   ├── __init__.py
│   ├── service.py           # Servicio principal
│   ├── sincpro_introspector.py  # Introspector del framework
│   └── markdown_generator.py    # Generador Markdown
└── __init__.py              # API pública
```

## 🚀 Uso Básico

### Generar Documentación

```python
from sincpro_framework.auto_docs import generate_framework_documentation

# Genera documentación completa
generate_framework_documentation(
    framework_instance=my_framework,
    output_path="docs/api_reference.md"
)
```

### Obtener Resumen

```python
from sincpro_framework.auto_docs import print_framework_summary

# Muestra resumen en consola
print_framework_summary(my_framework)
```

### Contenido como String

```python
from sincpro_framework.auto_docs import get_framework_documentation_content

# Obtiene documentación como string
content = get_framework_documentation_content(my_framework)
```

## 📋 API Completa

### `generate_framework_documentation(framework_instance, output_path, **config)`

Genera documentación completa y la guarda en un archivo.

**Parámetros:**
- `framework_instance`: Instancia del framework construida
- `output_path`: Ruta donde guardar la documentación  
- `**config`: Opciones de configuración

**Opciones de configuración:**
- `include_examples: bool = True` - Incluir ejemplos de uso
- `include_dependencies: bool = True` - Incluir información de dependencias
- `include_type_details: bool = True` - Incluir detalles de tipos
- `include_source_links: bool = False` - Incluir enlaces a código fuente

**Retorna:** `str` - Ruta donde se guardó la documentación

### `print_framework_summary(framework_instance)`

Imprime un resumen del framework en la consola.

### `get_framework_documentation_content(framework_instance, format_type="markdown", **config)`

Obtiene el contenido de la documentación como string sin guardar archivo.

## 🎯 Casos de Uso

### 1. Script de Documentación del Proyecto

```python
# generate_docs.py
from my_app import framework
from sincpro_framework.auto_docs import generate_framework_documentation

generate_framework_documentation(
    framework, 
    "docs/api_reference.md",
    include_examples=True
)
```

### 2. Múltiples Servicios

```python
# generate_all_docs.py
services = {
    'payment': payment_framework,
    'user': user_framework,
    'notification': notification_framework
}

for name, fw in services.items():
    generate_framework_documentation(
        fw, 
        f"docs/{name}_api.md"
    )
```

### 3. Integración en CI/CD

```bash
# En tu pipeline
python generate_docs.py
git add docs/
git commit -m "Update API documentation"
```

## 📖 Documentación Generada

La documentación incluye:

- **Overview**: Estadísticas y resumen del framework
- **Features**: Todas las Features registradas con DTOs
- **Application Services**: Servicios de aplicación complejos
- **DTOs**: Especificaciones completas de Data Transfer Objects
- **Dependencies**: Dependencias globales e inyección
- **Examples**: Ejemplos de uso para cada componente

## 🔧 Extensibilidad

### Agregar Nuevo Generador

1. Implementar el protocol `DocumentationGenerator`:

```python
from sincpro_framework.auto_docs.domain import DocumentationGenerator

class JsonDocumentationGenerator:
    def generate(self, result: IntrospectionResult) -> str:
        # Implementar generación JSON
        pass
    
    def save_to_file(self, content: str, output_path: str) -> str:
        # Implementar guardado
        pass
```

2. Extender el servicio para soportar el nuevo formato.

### Personalizar Introspección

1. Implementar el protocol `FrameworkIntrospector`:

```python
from sincpro_framework.auto_docs.domain import FrameworkIntrospector

class CustomIntrospector:
    def introspect(self, framework_instance) -> IntrospectionResult:
        # Implementar introspección personalizada
        pass
```

## ⚡ Mejores Prácticas

1. **Separación**: Mantén la generación de docs separada de la lógica de negocio
2. **Automatización**: Integra en tu proceso de build/CI
3. **Versionado**: Incluye la documentación generada en control de versiones
4. **Configuración**: Usa las opciones de configuración según tu audiencia
5. **Nomenclatura**: Usa nombres descriptivos para los archivos de salida
