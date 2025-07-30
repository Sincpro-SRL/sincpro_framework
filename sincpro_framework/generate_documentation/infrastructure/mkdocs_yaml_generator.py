"""
MkDocs YAML Configuration Generator

Dedicated component for generating clean and scalable MkDocs YAML configurations.
Handles both single and multi-framework scenarios with proper site structure.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from sincpro_framework.generate_documentation.domain.models import MkDocsCompleteDocumentation


@dataclass
class SiteConfig:
    """Configuration for the complete MkDocs site"""

    site_name: str
    site_description: Optional[str] = None
    theme: str = "material"
    repo_url: Optional[str] = None
    edit_uri: Optional[str] = None


@dataclass
class NavigationItem:
    """Represents a navigation item with proper structure"""

    title: str
    path: Optional[str] = None
    children: Optional[List["NavigationItem"]] = None

    def to_dict(self) -> Dict:
        """Convert to dictionary for YAML generation"""
        if self.children:
            return {self.title: [child.to_dict() for child in self.children]}
        else:
            return {self.title: self.path}


class MkDocsYamlGenerator:
    """
    Generates clean and structured MkDocs YAML configurations.
    Separates concerns and provides a clean API for YAML generation.
    """

    def __init__(self, site_config: Optional[SiteConfig] = None):
        """
        Initialize the YAML generator with optional site configuration.

        Args:
            site_config: Optional site configuration. If None, uses defaults.
        """
        self.site_config = site_config or SiteConfig(
            site_name="Framework Documentation",
            site_description="Auto-generated documentation for framework components",
            theme="material",
        )

    def generate_mkdocs_yaml(self, documentation: MkDocsCompleteDocumentation) -> str:
        """
        Generate complete mkdocs.yml content.

        Args:
            documentation: Complete documentation structure

        Returns:
            str: Complete mkdocs.yml content
        """
        # Update site name based on documentation
        if documentation.main_title:
            self.site_config.site_name = documentation.main_title

        yaml_content = []

        # Site configuration
        yaml_content.extend(self._generate_site_config())
        yaml_content.append("")  # Empty line

        # Navigation
        yaml_content.extend(self._generate_navigation(documentation))
        yaml_content.append("")  # Empty line

        # Theme configuration
        yaml_content.extend(self._generate_theme_config())
        yaml_content.append("")  # Empty line

        # Plugins and extensions
        yaml_content.extend(self._generate_plugins_config())

        return "\n".join(yaml_content)

    def generate_navigation_yaml(self, documentation: MkDocsCompleteDocumentation) -> str:
        """
        Generate only the navigation portion of the YAML.
        Useful for cases where you only need the nav config.

        Args:
            documentation: Complete documentation structure

        Returns:
            str: Navigation YAML content
        """
        nav_lines = self._generate_navigation(documentation)
        return "\n".join(nav_lines)

    def _generate_site_config(self) -> List[str]:
        """Generate site configuration section"""
        lines = [f"site_name: '{self.site_config.site_name}'"]

        if self.site_config.site_description:
            lines.append(f"site_description: '{self.site_config.site_description}'")

        if self.site_config.repo_url:
            lines.append(f"repo_url: '{self.site_config.repo_url}'")

        if self.site_config.edit_uri:
            lines.append(f"edit_uri: '{self.site_config.edit_uri}'")

        return lines

    def _generate_navigation(self, documentation: MkDocsCompleteDocumentation) -> List[str]:
        """Generate navigation configuration"""
        lines = ["nav:"]

        if documentation.is_multi_framework:
            lines.extend(self._generate_multi_framework_nav(documentation))
        else:
            lines.extend(self._generate_single_framework_nav(documentation))

        return lines

    def _generate_multi_framework_nav(
        self, documentation: MkDocsCompleteDocumentation
    ) -> List[str]:
        """Generate navigation for multiple frameworks"""
        lines = ["  - Home: index.md"]

        for framework in documentation.frameworks:
            lines.append(f"  - {framework.framework_name}:")

            # Add framework pages with proper indentation
            for nav_item in framework.nav_items:
                file_path = f"{framework.framework_dir}/{nav_item.file_path}"
                lines.append(f"    - {nav_item.title}: {file_path}")

        return lines

    def _generate_single_framework_nav(
        self, documentation: MkDocsCompleteDocumentation
    ) -> List[str]:
        """Generate navigation for a single framework"""
        lines = []

        if documentation.frameworks:
            framework = documentation.frameworks[0]
            for nav_item in framework.nav_items:
                lines.append(f"  - {nav_item.title}: {nav_item.file_path}")

        return lines

    def _generate_theme_config(self) -> List[str]:
        """Generate theme configuration with Sincpro brand colors"""
        lines = [
            f"theme:",
            f"  name: '{self.site_config.theme}'",
        ]

        if self.site_config.theme == "material":
            lines.extend(
                [
                    "  features:",
                    "    - navigation.tabs",
                    "    - navigation.sections",
                    "    - navigation.expand",
                    "    - navigation.top",
                    "    - navigation.instant",
                    "    - navigation.tracking",
                    "    - search.highlight",
                    "    - search.share",
                    "    - search.suggest",
                    "    - content.code.copy",
                    "    - content.code.annotate",
                    "    - content.tabs.link",
                    "  palette:",
                    "    # Sincpro Light Theme - Corporate violet",
                    "    - media: '(prefers-color-scheme: light)'",
                    "      scheme: default",
                    "      primary: deep purple",
                    "      accent: purple",
                    "      toggle:",
                    "        icon: material/brightness-7",
                    "        name: Switch to dark mode",
                    "    # Sincpro Dark Theme - Soft violet for developers",
                    "    - media: '(prefers-color-scheme: dark)'",
                    "      scheme: slate",
                    "      primary: deep purple",
                    "      accent: purple",
                    "      toggle:",
                    "        icon: material/brightness-4",
                    "        name: Switch to light mode",
                    "  font:",
                    "    text: 'Inter'",
                    "    code: 'JetBrains Mono'",
                ]
            )

        return lines

    def _generate_plugins_config(self) -> List[str]:
        """Generate plugins and extensions configuration optimized for developers"""
        lines = [
            "plugins:",
            "  - search:",
            "      lang: ['es', 'en']",
            "  - mkdocstrings:",
            "      handlers:",
            "        python:",
            "          options:",
            "            docstring_style: google",
            "            show_source: true",
            "            show_root_heading: true",
            "            show_root_toc_entry: false",
            "            heading_level: 2",
            "",
            "markdown_extensions:",
            "  # Sincpro Developer-focused extensions",
            "  - admonition",
            "  - attr_list",
            "  - md_in_html",
            "  - pymdownx.details",
            "  - pymdownx.superfences:",
            "      custom_fences:",
            "        - name: mermaid",
            "          class: mermaid",
            "          format: !!python/name:pymdownx.superfences.fence_code_format",
            "  - pymdownx.highlight:",
            "      anchor_linenums: true",
            "      line_spans: __span",
            "      pygments_lang_class: true",
            "      use_pygments: true",
            "  - pymdownx.inlinehilite",
            "  - pymdownx.snippets:",
            "      check_paths: true",
            "  - pymdownx.tabbed:",
            "      alternate_style: true",
            "      slugify: !!python/object/apply:pymdownx.slugs.slugify",
            "        kwds:",
            "          case: lower",
            "  - pymdownx.emoji:",
            "      emoji_index: !!python/name:material.extensions.emoji.twemoji",
            "      emoji_generator: !!python/name:material.extensions.emoji.to_svg",
            "  - pymdownx.tasklist:",
            "      custom_checkbox: true",
            "  - toc:",
            "      permalink: '🔗'",
            "      title: 'Contents'",
            "",
            "# Additional configuration for Sincpro",
            "extra:",
            "  generator: false  # Hide 'Made with Material for MkDocs'",
            "  social:",
            "    - icon: fontawesome/brands/github",
            "      link: https://github.com/Sincpro-SRL",
            "      name: Sincpro on GitHub",
            "    - icon: fontawesome/solid/globe",
            "      link: https://sincpro.com.bo",
            "      name: Sincpro website",
            "    - icon: fontawesome/solid/code",
            "      link: https://sincpro.com.bo",
            "      name: Developer portal",
            "",
            "# Custom CSS for Sincpro colors",
            "extra_css:",
            "  - assets/css/sincpro-theme.css",
            "",
            "# Custom JavaScript",
            "extra_javascript:",
            "  - assets/js/sincpro-analytics.js",
        ]

        return lines

    def create_custom_navigation(self, items: List[NavigationItem]) -> str:
        """
        Create custom navigation from NavigationItem objects.
        Useful for advanced navigation structures.

        Args:
            items: List of NavigationItem objects

        Returns:
            str: YAML navigation content
        """
        lines = ["nav:"]

        for item in items:
            nav_dict = item.to_dict()
            lines.extend(self._dict_to_yaml_lines(nav_dict, indent=1))

        return "\n".join(lines)

    def _dict_to_yaml_lines(self, data: Dict, indent: int = 0) -> List[str]:
        """Convert dictionary to YAML lines with proper indentation"""
        lines = []
        indent_str = "  " * indent

        for key, value in data.items():
            if isinstance(value, dict):
                lines.append(f"{indent_str}- {key}:")
                lines.extend(self._dict_to_yaml_lines(value, indent + 1))
            elif isinstance(value, list):
                lines.append(f"{indent_str}- {key}:")
                for item in value:
                    if isinstance(item, dict):
                        lines.extend(self._dict_to_yaml_lines(item, indent + 1))
                    else:
                        lines.append(f"{indent_str}  - {item}")
            else:
                lines.append(f"{indent_str}- {key}: {value}")

        return lines


# Factory function for easy instantiation
def create_yaml_generator(
    site_name: Optional[str] = None,
    site_description: Optional[str] = None,
    theme: str = "material",
    repo_url: Optional[str] = None,
) -> MkDocsYamlGenerator:
    """
    Factory function to create a configured YAML generator.

    Args:
        site_name: Name of the site
        site_description: Description of the site
        theme: MkDocs theme to use
        repo_url: Repository URL for the project

    Returns:
        MkDocsYamlGenerator: Configured generator instance
    """
    config = SiteConfig(
        site_name=site_name or "Framework Documentation",
        site_description=site_description,
        theme=theme,
        repo_url=repo_url,
    )

    return MkDocsYamlGenerator(config)


# Singleton instance for common usage
yaml_generator = MkDocsYamlGenerator()
