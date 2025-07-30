"""
MkDocs Documentation Generator

Generador de documentación que utiliza MkDocs + mkdocstrings para auto-documentar
todos los tipos de objetos encontrados en la introspección.
"""

import inspect
from typing import Any, Dict, List

from sincpro_framework.generate_documentation.domain.models import IntrospectionResult

"""
Auto Documentation Generator for Framework Introspection
Genera documentación automática basada en IntrospectionResult
"""

import inspect
from datetime import datetime
from typing import Any, Dict, List


class FrameworkDocumentationGenerator:
    """Generador de documentación automática para frameworks introspectados"""

    def __init__(self, introspection_result: IntrospectionResult):
        self.result = introspection_result
        self.docs = {
            "metadata": {
                "framework_name": introspection_result.framework_name,
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "generated_by": "andru1236",  # Tu usuario
                "total_items": self._count_total_items(),
            },
            "sections": {},
        }

    def _count_total_items(self) -> Dict[str, int]:
        """Cuenta elementos en cada sección"""
        return {
            "dtos": len(self.result.dtos.classes),
            "dependency_functions": len(self.result.dependencies.functions),
            "dependency_objects": len(self.result.dependencies.objects),
            "middleware_functions": len(self.result.middlewares.functions),
            "middleware_objects": len(self.result.middlewares.objects),
            "features": len(self.result.features.objects),
            "app_services": len(self.result.app_services.objects),
        }

    def generate_complete_documentation(self) -> Dict[str, Any]:
        """Genera documentación completa de todos los componentes"""

        # Documentar DTOs (Pydantic Models) - Automático
        self.docs["sections"]["dtos"] = self._document_pydantic_models()

        # Documentar Dependencies
        self.docs["sections"]["dependencies"] = self._document_dependencies()

        # Documentar Middlewares
        self.docs["sections"]["middlewares"] = self._document_middlewares()

        # Documentar Features
        self.docs["sections"]["features"] = self._document_features()

        # Documentar Application Services
        self.docs["sections"]["app_services"] = self._document_app_services()

        return self.docs

    def _document_pydantic_models(self) -> Dict[str, Any]:
        """Documenta DTOs Pydantic (automático)"""
        section = {
            "title": "📋 Data Transfer Objects (DTOs)",
            "description": "Modelos Pydantic con validación automática y schemas JSON",
            "count": len(self.result.dtos.classes),
            "models": {},
        }

        for dto_class in self.result.dtos.classes:
            model_info = {
                "name": dto_class.__name__,
                "module": dto_class.__module__,
                "docstring": inspect.getdoc(dto_class) or "Sin documentación",
                "schema": None,
                "fields": {},
                "inheritance": [
                    base.__name__
                    for base in dto_class.__bases__
                    if base.__name__ != "BaseModel"
                ],
            }

            # Schema automático de Pydantic
            try:
                if hasattr(dto_class, "model_json_schema"):  # Pydantic V2
                    model_info["schema"] = dto_class.model_json_schema()
                elif hasattr(dto_class, "schema"):  # Pydantic V1
                    model_info["schema"] = dto_class.schema()
            except Exception as e:
                model_info["schema_error"] = str(e)

            # Campos del modelo
            if hasattr(dto_class, "model_fields"):  # Pydantic V2
                for field_name, field_info in dto_class.model_fields.items():
                    model_info["fields"][field_name] = {
                        "type": str(field_info.annotation),
                        "required": field_info.is_required(),
                        "default": (
                            field_info.default if field_info.default is not None else None
                        ),
                        "description": field_info.description,
                    }
            elif hasattr(dto_class, "__fields__"):  # Pydantic V1
                for field_name, field_info in dto_class.__fields__.items():
                    model_info["fields"][field_name] = {
                        "type": str(field_info.type_),
                        "required": field_info.required,
                        "default": field_info.default,
                        "description": field_info.field_info.description,
                    }

            section["models"][dto_class.__name__] = model_info

        return section

    def _document_dependencies(self) -> Dict[str, Any]:
        """Documenta sistema de dependencias"""
        section = {
            "title": "🔌 Dependency Injection System",
            "description": "Funciones y objetos del sistema de inyección de dependencias",
            "functions": {},
            "objects": {},
        }

        # Documentar funciones de dependencias
        for func in self.result.dependencies.functions:
            section["functions"][func.__name__] = self._extract_function_info(func)

        # Documentar objetos de dependencias
        for i, obj in enumerate(self.result.dependencies.objects):
            key = f"{obj.__class__.__name__}_{i}"
            section["objects"][key] = self._extract_object_info(obj)

        return section

    def _document_middlewares(self) -> Dict[str, Any]:
        """Documenta middlewares"""
        section = {
            "title": "🔄 Middleware System",
            "description": "Funciones y objetos middleware del framework",
            "functions": {},
            "objects": {},
        }

        # Documentar funciones middleware
        for func in self.result.middlewares.functions:
            section["functions"][func.__name__] = self._extract_function_info(func)

        # Documentar objetos middleware
        for i, obj in enumerate(self.result.middlewares.objects):
            key = f"{obj.__class__.__name__}_{i}"
            section["objects"][key] = self._extract_object_info(obj)

        return section

    def _document_features(self) -> Dict[str, Any]:
        """Documenta features del framework"""
        section = {
            "title": "⚡ Framework Features",
            "description": "Características y funcionalidades principales del framework",
            "objects": {},
        }

        for i, obj in enumerate(self.result.features.objects):
            key = f"{obj.__class__.__name__}_{i}"
            section["objects"][key] = self._extract_object_info(obj)

        return section

    def _document_app_services(self) -> Dict[str, Any]:
        """Documenta servicios de aplicación"""
        section = {
            "title": "🏢 Application Services",
            "description": "Servicios de la capa de aplicación del framework",
            "objects": {},
        }

        for i, obj in enumerate(self.result.app_services.objects):
            key = f"{obj.__class__.__name__}_{i}"
            section["objects"][key] = self._extract_object_info(obj)

        return section

    def _extract_function_info(self, func) -> Dict[str, Any]:
        """Extrae información detallada de una función"""
        sig = inspect.signature(func)

        return {
            "name": func.__name__,
            "module": func.__module__,
            "docstring": inspect.getdoc(func) or "Sin documentación",
            "signature": str(sig),
            "parameters": {
                name: {
                    "type": (
                        str(param.annotation)
                        if param.annotation != inspect.Parameter.empty
                        else "Any"
                    ),
                    "default": (
                        str(param.default)
                        if param.default != inspect.Parameter.empty
                        else None
                    ),
                    "required": param.default == inspect.Parameter.empty,
                }
                for name, param in sig.parameters.items()
            },
            "return_type": (
                str(sig.return_annotation)
                if sig.return_annotation != inspect.Signature.empty
                else "Any"
            ),
            "is_async": inspect.iscoroutinefunction(func),
            "is_generator": inspect.isgeneratorfunction(func),
            "source_file": self._get_source_file(func),
            "source_line": self._get_source_line(func),
        }

    def _extract_object_info(self, obj) -> Dict[str, Any]:
        """Extrae información de un objeto/instancia"""
        cls = obj.__class__

        return {
            "class_name": cls.__name__,
            "module": cls.__module__,
            "class_docstring": inspect.getdoc(cls) or "Sin documentación",
            "object_repr": repr(obj),
            "object_id": id(obj),
            "is_pydantic": hasattr(obj, "model_fields") or hasattr(obj, "__fields__"),
            "public_attributes": self._get_public_attributes(obj),
            "public_methods": self._get_public_methods(obj),
            "inheritance": [
                base.__name__ for base in cls.__mro__[1:] if base.__name__ != "object"
            ],
        }

    def _get_public_attributes(self, obj) -> Dict[str, Any]:
        """Obtiene atributos públicos de un objeto"""
        attributes = {}
        for attr_name in dir(obj):
            if not attr_name.startswith("_") and not callable(getattr(obj, attr_name, None)):
                try:
                    attr_value = getattr(obj, attr_name)
                    attributes[attr_name] = {
                        "value": (
                            str(attr_value)[:100] + "..."
                            if len(str(attr_value)) > 100
                            else str(attr_value)
                        ),
                        "type": type(attr_value).__name__,
                    }
                except:
                    attributes[attr_name] = {"value": "<Error al acceder>", "type": "Unknown"}
        return attributes

    def _get_public_methods(self, obj) -> Dict[str, Any]:
        """Obtiene métodos públicos de un objeto"""
        methods = {}
        for method_name in dir(obj):
            if not method_name.startswith("_") and callable(getattr(obj, method_name, None)):
                try:
                    method = getattr(obj, method_name)
                    if inspect.ismethod(method) or inspect.isfunction(method):
                        methods[method_name] = {
                            "signature": str(inspect.signature(method)),
                            "docstring": inspect.getdoc(method) or "Sin documentación",
                        }
                except:
                    methods[method_name] = {
                        "signature": "<Error>",
                        "docstring": "Error al acceder",
                    }
        return methods

    def _get_source_file(self, obj) -> str:
        """Obtiene archivo fuente"""
        try:
            return inspect.getfile(obj)
        except:
            return "Unknown"

    def _get_source_line(self, obj) -> int:
        """Obtiene línea de código fuente"""
        try:
            return inspect.getsourcelines(obj)[1]
        except:
            return 0

    def generate_markdown(self) -> str:
        """Genera documentación en formato Markdown"""
        docs = self.generate_complete_documentation()

        markdown_lines = [
            f"# {docs['metadata']['framework_name']} - Documentación Automática",
            "",
            f"> 🤖 Generado automáticamente el {docs['metadata']['generated_at']} por {docs['metadata']['generated_by']}",
            "",
            "## 📊 Resumen del Framework",
            "",
            f"- **DTOs (Pydantic):** {docs['metadata']['total_items']['dtos']} modelos",
            f"- **Funciones de Dependencias:** {docs['metadata']['total_items']['dependency_functions']}",
            f"- **Objetos de Dependencias:** {docs['metadata']['total_items']['dependency_objects']}",
            f"- **Funciones Middleware:** {docs['metadata']['total_items']['middleware_functions']}",
            f"- **Objetos Middleware:** {docs['metadata']['total_items']['middleware_objects']}",
            f"- **Features:** {docs['metadata']['total_items']['features']}",
            f"- **Application Services:** {docs['metadata']['total_items']['app_services']}",
            "",
            "---",
            "",
        ]

        # Generar sección DTOs
        if docs["sections"]["dtos"]["models"]:
            markdown_lines.extend(self._generate_dtos_markdown(docs["sections"]["dtos"]))

        # Generar sección Dependencies
        if (
            docs["sections"]["dependencies"]["functions"]
            or docs["sections"]["dependencies"]["objects"]
        ):
            markdown_lines.extend(
                self._generate_dependencies_markdown(docs["sections"]["dependencies"])
            )

        # Generar sección Middlewares
        if (
            docs["sections"]["middlewares"]["functions"]
            or docs["sections"]["middlewares"]["objects"]
        ):
            markdown_lines.extend(
                self._generate_middlewares_markdown(docs["sections"]["middlewares"])
            )

        # Generar sección Features
        if docs["sections"]["features"]["objects"]:
            markdown_lines.extend(
                self._generate_features_markdown(docs["sections"]["features"])
            )

        # Generar sección App Services
        if docs["sections"]["app_services"]["objects"]:
            markdown_lines.extend(
                self._generate_app_services_markdown(docs["sections"]["app_services"])
            )

        return "\n".join(markdown_lines)

    def _generate_dtos_markdown(self, section: Dict[str, Any]) -> List[str]:
        """Genera Markdown para DTOs Pydantic"""
        lines = [
            f"# {section['title']}",
            "",
            section["description"],
            "",
            f"**Total de modelos:** {section['count']}",
            "",
        ]

        for model_name, model_info in section["models"].items():
            lines.extend(
                [
                    f"## {model_name}",
                    "",
                    f"**Módulo:** `{model_info['module']}`",
                    "",
                    model_info["docstring"],
                    "",
                ]
            )

            if model_info["inheritance"]:
                lines.extend([f"**Herencia:** {' → '.join(model_info['inheritance'])}", ""])

            if model_info["fields"]:
                lines.extend(["### Campos:", ""])
                for field_name, field_info in model_info["fields"].items():
                    required = "✅ Requerido" if field_info["required"] else "⚪ Opcional"
                    lines.append(f"- **{field_name}** (`{field_info['type']}`) - {required}")
                    if field_info["description"]:
                        lines.append(f"  - {field_info['description']}")
                    if field_info["default"] is not None:
                        lines.append(f"  - Default: `{field_info['default']}`")
                lines.append("")

            if model_info["schema"]:
                lines.extend(
                    ["### Schema JSON:", "```json", str(model_info["schema"]), "```", ""]
                )

        return lines

    def _generate_dependencies_markdown(self, section: Dict[str, Any]) -> List[str]:
        """Genera Markdown para Dependencies"""
        lines = [f"# {section['title']}", "", section["description"], ""]

        if section["functions"]:
            lines.extend(["## Funciones de Dependencias", ""])
            for func_name, func_info in section["functions"].items():
                lines.extend(self._generate_function_markdown(func_name, func_info))

        if section["objects"]:
            lines.extend(["## Objetos de Dependencias", ""])
            for obj_name, obj_info in section["objects"].items():
                lines.extend(self._generate_object_markdown(obj_name, obj_info))

        return lines

    def _generate_middlewares_markdown(self, section: Dict[str, Any]) -> List[str]:
        """Genera Markdown para Middlewares"""
        lines = [f"# {section['title']}", "", section["description"], ""]

        if section["functions"]:
            lines.extend(["## Funciones Middleware", ""])
            for func_name, func_info in section["functions"].items():
                lines.extend(self._generate_function_markdown(func_name, func_info))

        if section["objects"]:
            lines.extend(["## Objetos Middleware", ""])
            for obj_name, obj_info in section["objects"].items():
                lines.extend(self._generate_object_markdown(obj_name, obj_info))

        return lines

    def _generate_features_markdown(self, section: Dict[str, Any]) -> List[str]:
        """Genera Markdown para Features"""
        lines = [f"# {section['title']}", "", section["description"], ""]

        for obj_name, obj_info in section["objects"].items():
            lines.extend(self._generate_object_markdown(obj_name, obj_info))

        return lines

    def _generate_app_services_markdown(self, section: Dict[str, Any]) -> List[str]:
        """Genera Markdown para App Services"""
        lines = [f"# {section['title']}", "", section["description"], ""]

        for obj_name, obj_info in section["objects"].items():
            lines.extend(self._generate_object_markdown(obj_name, obj_info))

        return lines

    def _generate_function_markdown(
        self, func_name: str, func_info: Dict[str, Any]
    ) -> List[str]:
        """Genera Markdown para una función"""
        lines = [
            f"### {func_name}",
            "",
            f"**Módulo:** `{func_info['module']}`",
            f"**Signature:** `{func_info['signature']}`",
            "",
        ]

        if func_info["is_async"]:
            lines.append("🔄 **Función Asíncrona**")
        if func_info["is_generator"]:
            lines.append("🔁 **Función Generadora**")

        lines.extend(["", func_info["docstring"], ""])

        if func_info["parameters"]:
            lines.extend(["**Parámetros:**", ""])
            for param_name, param_info in func_info["parameters"].items():
                required = "✅ Requerido" if param_info["required"] else "⚪ Opcional"
                lines.append(f"- **{param_name}** (`{param_info['type']}`) - {required}")
                if param_info["default"]:
                    lines.append(f"  - Default: `{param_info['default']}`")
            lines.append("")

        if func_info["return_type"] != "Any":
            lines.extend([f"**Retorna:** `{func_info['return_type']}`", ""])

        return lines

    def _generate_object_markdown(self, obj_name: str, obj_info: Dict[str, Any]) -> List[str]:
        """Genera Markdown para un objeto"""
        lines = [
            f"### {obj_name}",
            "",
            f"**Clase:** `{obj_info['class_name']}`",
            f"**Módulo:** `{obj_info['module']}`",
            "",
        ]

        if obj_info["is_pydantic"]:
            lines.append("📋 **Instancia Pydantic**")

        lines.extend(
            [
                "",
                obj_info["class_docstring"],
                "",
                f"**Representación:** `{obj_info['object_repr']}`",
                "",
            ]
        )

        if obj_info["inheritance"]:
            lines.extend([f"**Herencia:** {' → '.join(obj_info['inheritance'])}", ""])

        if obj_info["public_attributes"]:
            lines.extend(["**Atributos públicos:**", ""])
            for attr_name, attr_info in obj_info["public_attributes"].items():
                lines.append(
                    f"- **{attr_name}** (`{attr_info['type']}`): `{attr_info['value']}`"
                )
            lines.append("")

        if obj_info["public_methods"]:
            lines.extend(["**Métodos públicos:**", ""])
            for method_name, method_info in obj_info["public_methods"].items():
                lines.append(f"- **{method_name}**`{method_info['signature']}`")
                if method_info["docstring"] != "Sin documentación":
                    lines.append(f"  - {method_info['docstring']}")
            lines.append("")

        return lines

    def save_documentation(self, output_file: str = "framework_documentation.md"):
        """Guarda la documentación en un archivo"""
        markdown_content = self.generate_markdown()

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        print(f"✅ Documentación guardada en: {output_file}")
        print(f"📊 Tamaño: {len(markdown_content)} caracteres")
        return output_file
