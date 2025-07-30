"""
Framework Documentation Extractor

Extracts structured documentation metadata from framework introspection results.
This class is responsible for converting IntrospectionResult into FrameworkDocs model.
"""

from datetime import datetime

from sincpro_framework.generate_documentation.domain.extractor import (
    extract_class_metadata,
    extract_function_metadata,
    extract_pydantic_model_metadata,
    is_pydantic_model_class,
)
from sincpro_framework.generate_documentation.domain.models import (
    FrameworkDocs,
    IntrospectionResult,
)


class FrameworkDocumentationExtractor:
    """Extracts structured documentation metadata from framework introspection"""

    def extract_framework_docs(
        self, introspection_result: IntrospectionResult
    ) -> FrameworkDocs:
        """
        Extract complete FrameworkDocs model from introspection result.
        This is the main entry point that creates structured documentation metadata.
        """
        self.result = introspection_result

        # Extract DTOs (Pydantic models from classes)
        dtos = []
        for dto_class in self.result.dtos.classes:
            if is_pydantic_model_class(dto_class):
                dtos.append(extract_pydantic_model_metadata(dto_class))

        # Extract Features (class metadata from instances)
        features = []
        for feature_obj in self.result.features.objects:
            # Extract class metadata from the instance
            feature_class = feature_obj.__class__
            features.append(extract_class_metadata(feature_class))

        # Extract Application Services (class metadata from instances)
        application_services = []
        for app_service_obj in self.result.app_services.objects:
            # Extract class metadata from the instance
            app_service_class = app_service_obj.__class__
            application_services.append(extract_class_metadata(app_service_class))

        # Extract Dependencies (functions + class metadata from instances)
        dependencies = []
        for dep_func in self.result.dependencies.functions:
            dependencies.append(extract_function_metadata(dep_func))
        for dep_obj in self.result.dependencies.objects:
            # Extract class metadata from the instance
            dep_class = dep_obj.__class__
            dependencies.append(extract_class_metadata(dep_class))

        # Extract Middlewares (functions + class metadata from instances)
        middlewares = []
        for middleware_func in self.result.middlewares.functions:
            middlewares.append(extract_function_metadata(middleware_func))
        for middleware_obj in self.result.middlewares.objects:
            # Extract class metadata from the instance
            middleware_class = middleware_obj.__class__
            middlewares.append(extract_class_metadata(middleware_class))

        # Create FrameworkDocs instance
        framework_docs = FrameworkDocs(
            framework_name=self.result.framework_name,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            generated_by="sincpro_framework",
            dtos=dtos,
            features=features,
            application_services=application_services,
            dependencies=dependencies,
            middlewares=middlewares,
        )

        # Generate and attach summary
        framework_docs.generate_summary()

        return framework_docs


doc_extractor = FrameworkDocumentationExtractor()
