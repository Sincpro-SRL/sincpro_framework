"""
Static Site Generator

Complete static site generator for MkDocs with Sincpro theme.
Single API that generates everything needed for `mkdocs build` directly.
"""

import shutil
import subprocess
import sys
from pathlib import Path

from sincpro_framework.generate_documentation.domain.models import MkDocsCompleteDocumentation
from sincpro_framework.generate_documentation.infrastructure.mkdocs_yaml_generator import (
    yaml_generator,
)


class StaticSiteGenerator:
    """
    Complete MkDocs static site generator.
    Simple and unified API for generating ready-to-use documentation.
    """

    def generate_site(
        self,
        documentation: MkDocsCompleteDocumentation,
        output_dir: str = "generated_docs",
        build_static: bool = False,
    ) -> str:
        """
        Generate a complete static site ready for MkDocs.
        Optionally builds static HTML automatically.

        Args:
            documentation: Complete framework documentation
            output_dir: Output directory
            build_static: If True, runs mkdocs build automatically

        Returns:
            str: Path to the generated directory (or site/ if build_static=True)
        """
        # Prepare directory
        output_path = self._prepare_output_directory(output_dir)

        # Generate content files (markdown)
        self._write_content_files(documentation, output_path)

        # Generate complete mkdocs.yml
        self._write_mkdocs_config(documentation, output_path)

        # Generate additional files
        self._write_requirements_file(output_path)
        self._write_readme_file(documentation, output_path)
        self._write_sincpro_assets(output_path)

        print(f"✅ Complete static site generated at: {output_path}")

        if not build_static:
            print(f"📝 Run: cd {output_path} && mkdocs serve")
            return output_path

        # Build static HTML automatically
        site_path = self._build_static_site(output_path)
        print(f"🚀 Static HTML site built at: {site_path}")
        print(f"📂 Ready to serve or deploy the 'site' directory")

        return site_path

    def _build_static_site(self, source_path: str, site_dir: str = "site") -> str:
        """
        Execute mkdocs build using subprocess to generate static HTML.

        Args:
            source_path: Path to the directory containing mkdocs.yml
            site_dir: Directory name for the built site

        Returns:
            str: Path to the built site directory
        """
        source_path = Path(source_path).resolve()
        mkdocs_yml = source_path / "mkdocs.yml"
        site_path = source_path / site_dir

        # Verify mkdocs.yml exists
        if not mkdocs_yml.exists():
            raise FileNotFoundError(f"mkdocs.yml not found at {mkdocs_yml}")

        print(f"🔨 Building static site with mkdocs...")

        try:
            # Get the Python executable from current environment
            python_executable = sys.executable

            # Build the mkdocs command
            cmd = [
                python_executable,
                "-m",
                "mkdocs",
                "build",
                "-f",
                str(mkdocs_yml),
                "-d",
                str(site_path),
                "--clean",
            ]

            # Execute mkdocs build
            result = subprocess.run(
                cmd, cwd=source_path, capture_output=True, text=True, check=True
            )

            print(f"✅ MkDocs build completed successfully")
            if result.stdout:
                print(f"📋 Build output: {result.stdout.strip()}")

        except subprocess.CalledProcessError as e:
            print(f"❌ MkDocs build failed with exit code {e.returncode}")
            if e.stderr:
                print(f"🔥 Error: {e.stderr.strip()}")
            if e.stdout:
                print(f"📋 Output: {e.stdout.strip()}")
            raise RuntimeError(f"MkDocs build failed: {e.stderr}") from e
        except FileNotFoundError as e:
            print(f"❌ MkDocs not found. Make sure it's installed in your environment.")
            raise RuntimeError(
                "MkDocs executable not found. Install with: pip install mkdocs mkdocs-material"
            ) from e

        return str(site_path)

    def _prepare_output_directory(self, output_dir: str) -> str:
        """Prepare clean output directory"""
        output_path = Path(output_dir).resolve()

        if output_path.exists():
            print(f"🗑️ Cleaning existing directory: {output_path}")
            shutil.rmtree(output_path)

        output_path.mkdir(parents=True, exist_ok=True)
        print(f"📁 Directory prepared: {output_path}")

        return str(output_path)

    def _write_content_files(
        self, documentation: MkDocsCompleteDocumentation, output_path: str
    ) -> None:
        """Write all markdown content files in correct MkDocs structure"""
        # Create docs directory for MkDocs standard structure
        docs_dir = Path(output_path) / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)

        # Write main index if exists
        if documentation.main_index_content:
            index_path = docs_dir / "index.md"
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(documentation.main_index_content)
            print(f"✅ Content: docs/index.md")

        # Write framework pages
        for framework in documentation.frameworks:
            # Create framework directory if multi-framework
            if documentation.is_multi_framework and framework.framework_dir:
                framework_dir = docs_dir / framework.framework_dir
                framework_dir.mkdir(parents=True, exist_ok=True)
                base_path = framework_dir
                path_prefix = f"docs/{framework.framework_dir}"
            else:
                base_path = docs_dir
                path_prefix = "docs"

            # Write all pages for this framework
            for page in framework.pages:
                page_path = base_path / page.filename

                # Create subdirectories if needed
                page_path.parent.mkdir(parents=True, exist_ok=True)

                with open(page_path, "w", encoding="utf-8") as f:
                    f.write(page.content)

                if documentation.is_multi_framework and framework.framework_dir:
                    print(f"✅ Content: {path_prefix}/{page.filename}")
                else:
                    print(f"✅ Content: docs/{page.filename}")

    def _write_mkdocs_config(
        self, documentation: MkDocsCompleteDocumentation, output_path: str
    ) -> None:
        """Generate complete mkdocs.yml with Sincpro configuration"""
        config_content = yaml_generator.generate_mkdocs_yaml(documentation)
        config_path = Path(output_path) / "mkdocs.yml"

        with open(config_path, "w", encoding="utf-8") as f:
            f.write(config_content)
        print(f"✅ Configuration: mkdocs.yml")

    def _write_requirements_file(self, output_path: str) -> None:
        """Generate requirements.txt for MkDocs"""
        requirements = [
            "mkdocs>=1.6.1",
            "mkdocs-material>=9.6.16",
            "mkdocstrings[python]>=0.30.0",
            "pymdown-extensions>=10.16.1",
        ]

        requirements_path = Path(output_path) / "requirements.txt"
        with open(requirements_path, "w", encoding="utf-8") as f:
            f.write("\n".join(requirements))
        print(f"✅ Dependencies: requirements.txt")

    def _write_readme_file(
        self, documentation: MkDocsCompleteDocumentation, output_path: str
    ) -> None:
        """Generate README.md with instructions"""
        frameworks_list = "\n".join(
            [
                f"- **{fw.framework_name}**: `{fw.framework_dir}/`"
                for fw in documentation.frameworks
            ]
        )

        readme_content = f"""# {documentation.main_title}

Auto-generated documentation with Sincpro Framework.

## 🚀 Quick Start

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Serve locally:**
   ```bash
   mkdocs serve
   ```

3. **Build static site:**
   ```bash
   mkdocs build
   ```

## 📁 Structure

{"This site documents multiple frameworks:" if documentation.is_multi_framework else "This site documents a single framework:"}

{frameworks_list}

## 🎨 Theme

Uses Material theme for MkDocs with Sincpro corporate colors (violet).

---
*Auto-generated by Sincpro Framework*
"""

        readme_path = Path(output_path) / "README.md"
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(readme_content)
        print(f"✅ Instructions: README.md")

    def _write_sincpro_assets(self, output_path: str) -> None:
        """Generate Sincpro CSS and JS assets in correct MkDocs structure"""
        # Assets go inside docs/assets/ for MkDocs standard structure
        docs_dir = Path(output_path) / "docs"
        assets_dir = docs_dir / "assets"
        css_dir = assets_dir / "css"
        js_dir = assets_dir / "js"

        # Create directories
        css_dir.mkdir(parents=True, exist_ok=True)
        js_dir.mkdir(parents=True, exist_ok=True)

        # Sincpro custom CSS
        sincpro_css = """
/* Sincpro Framework Documentation - Professional Corporate Theme */
:root {
  /* Primary Sincpro Brand Colors */
  --sincpro-primary: #6B46C1;
  --sincpro-primary-light: #8B5CF6;
  --sincpro-primary-dark: #553C9A;
  --sincpro-accent: #A78BFA;
  --sincpro-white: #FFFFFF;
  --sincpro-gray-50: #F9FAFB;
  --sincpro-gray-100: #F3F4F6;
  --sincpro-gray-200: #E5E7EB;
  --sincpro-gray-300: #D1D5DB;
  --sincpro-gray-800: #1F2937;
  --sincpro-gray-900: #111827;
  
  /* Status Colors */
  --sincpro-success: #10B981;
  --sincpro-warning: #F59E0B;
  --sincpro-error: #EF4444;
  --sincpro-info: #3B82F6;
}

/* Enhanced Header */
.md-header {
  background: linear-gradient(135deg, var(--sincpro-primary) 0%, var(--sincpro-primary-light) 100%);
  box-shadow: 0 4px 20px rgba(107, 70, 193, 0.15);
  backdrop-filter: blur(10px);
}

.md-header__title {
  color: var(--sincpro-white);
  font-weight: 600;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
}

/* Modern Navigation Tabs */
.md-tabs {
  background: var(--sincpro-primary-dark);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.md-tabs__item {
  transition: all 0.3s ease;
}

.md-tabs__item:hover {
  background: rgba(255, 255, 255, 0.1);
}

/* Enhanced Navigation */
.md-nav__item .md-nav__link--active {
  color: var(--sincpro-primary);
  font-weight: 600;
  border-left: 3px solid var(--sincpro-primary);
  padding-left: 0.7rem;
}

.md-nav__item .md-nav__link:hover {
  color: var(--sincpro-primary-light);
  transition: color 0.2s ease;
}

/* Enhanced Links */
.md-typeset a {
  color: var(--sincpro-primary);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: all 0.2s ease;
}

.md-typeset a:hover {
  color: var(--sincpro-primary-dark);
  border-bottom-color: var(--sincpro-primary-light);
}

/* Professional Tables */
.md-typeset table:not([class]) {
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 4px 6px rgba(0, 0, 0, 0.07);
  border: 1px solid var(--sincpro-gray-200);
}

.md-typeset table:not([class]) th {
  background: linear-gradient(135deg, var(--sincpro-primary) 0%, var(--sincpro-primary-light) 100%);
  color: var(--sincpro-white);
  font-weight: 600;
  text-transform: uppercase;
  font-size: 0.85em;
  letter-spacing: 0.5px;
  padding: 1rem 0.8rem;
  border: none;
}

.md-typeset table:not([class]) td {
  padding: 0.8rem;
  border-bottom: 1px solid var(--sincpro-gray-100);
  vertical-align: top;
}

.md-typeset table:not([class]) tr:hover {
  background-color: var(--sincpro-gray-50);
}

/* Enhanced Code Blocks */
.md-typeset .highlight {
  border-radius: 8px;
  border: 1px solid var(--sincpro-gray-200);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
}

.md-typeset code {
  background-color: var(--sincpro-gray-100);
  color: var(--sincpro-primary-dark);
  padding: 0.2em 0.4em;
  border-radius: 4px;
  font-weight: 500;
  border: 1px solid var(--sincpro-gray-200);
}

/* Professional Badges */
.sincpro-badge {
  display: inline-block;
  padding: 6px 12px;
  background: linear-gradient(135deg, var(--sincpro-primary) 0%, var(--sincpro-primary-light) 100%);
  color: var(--sincpro-white);
  border-radius: 20px;
  font-size: 0.8em;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  box-shadow: 0 2px 4px rgba(107, 70, 193, 0.2);
  transition: all 0.2s ease;
}

.sincpro-badge:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 8px rgba(107, 70, 193, 0.3);
}

/* Status Badges */
.badge-required {
  background: linear-gradient(135deg, var(--sincpro-success) 0%, #059669 100%);
  color: white;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 0.75em;
  font-weight: 600;
}

.badge-optional {
  background: linear-gradient(135deg, var(--sincpro-gray-300) 0%, var(--sincpro-gray-400) 100%);
  color: var(--sincpro-gray-800);
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 0.75em;
  font-weight: 600;
}

/* Enhanced Admonitions */
.md-typeset .admonition {
  border-radius: 8px;
  border-left: 4px solid var(--sincpro-primary);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  margin: 1.5rem 0;
}

.md-typeset .admonition.info {
  border-left-color: var(--sincpro-info);
}

.md-typeset .admonition.success {
  border-left-color: var(--sincpro-success);
}

.md-typeset .admonition.warning {
  border-left-color: var(--sincpro-warning);
}

.md-typeset .admonition.danger {
  border-left-color: var(--sincpro-error);
}

/* Enhanced Content Cards */
.content-card {
  background: var(--sincpro-white);
  border-radius: 12px;
  padding: 1.5rem;
  margin: 1rem 0;
  box-shadow: 0 4px 6px rgba(0, 0, 0, 0.07);
  border: 1px solid var(--sincpro-gray-200);
  transition: all 0.3s ease;
}

.content-card:hover {
  box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
  transform: translateY(-2px);
}

/* Enhanced Section Headers */
.md-typeset h1 {
  color: var(--sincpro-primary);
  border-bottom: 3px solid var(--sincpro-primary-light);
  padding-bottom: 0.5rem;
  margin-bottom: 2rem;
}

.md-typeset h2 {
  color: var(--sincpro-primary-dark);
  border-bottom: 2px solid var(--sincpro-gray-200);
  padding-bottom: 0.3rem;
}

.md-typeset h3 {
  color: var(--sincpro-primary);
}

/* Enhanced Tabs */
.md-typeset .tabbed-set {
  border-radius: 8px;
  border: 1px solid var(--sincpro-gray-200);
  overflow: hidden;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
}

.md-typeset .tabbed-set > input:checked + label {
  background: var(--sincpro-primary);
  color: var(--sincpro-white);
}

.md-typeset .tabbed-set > input:not(:checked) + label:hover {
  background: var(--sincpro-gray-50);
}

/* Footer Enhancement */
.md-footer {
  background: var(--sincpro-gray-900);
  border-top: 3px solid var(--sincpro-primary);
}

/* Responsive Design */
@media screen and (max-width: 768px) {
  .md-typeset table:not([class]) {
    font-size: 0.9em;
  }
  
  .content-card {
    padding: 1rem;
    margin: 0.5rem 0;
  }
  
  .sincpro-badge {
    padding: 4px 8px;
    font-size: 0.7em;
  }
}

/* Print Styles */
@media print {
  .md-header,
  .md-tabs,
  .md-sidebar,
  .md-footer {
    display: none !important;
  }
  
  .md-main__inner {
    margin: 0 !important;
  }
  
  .md-typeset a {
    color: var(--sincpro-primary) !important;
    text-decoration: underline !important;
  }
}

/* Dark Mode Enhancements */
[data-md-color-scheme="slate"] {
  --sincpro-gray-50: #1e293b;
  --sincpro-gray-100: #334155;
  --sincpro-gray-200: #475569;
}

[data-md-color-scheme="slate"] .content-card {
  background: var(--md-default-bg-color);
  border-color: var(--md-default-fg-color--lighter);
}

[data-md-color-scheme="slate"] .md-typeset table:not([class]) td {
  border-bottom-color: var(--md-default-fg-color--lighter);
}
"""

        css_path = css_dir / "sincpro-theme.css"
        with open(css_path, "w", encoding="utf-8") as f:
            f.write(sincpro_css)
        print(f"✅ Sincpro CSS: docs/assets/css/sincpro-theme.css")

        # Basic JavaScript
        sincpro_js = """
console.log('🚀 Sincpro Framework Documentation');

document.addEventListener('DOMContentLoaded', function() {
    // Add Sincpro badge to footer
    const footer = document.querySelector('.md-footer-meta__inner');
    if (footer && !footer.querySelector('.sincpro-version')) {
        const badge = document.createElement('span');
        badge.className = 'sincpro-version';
        badge.innerHTML = ' | <span class="sincpro-badge">Sincpro Framework</span>';
        footer.appendChild(badge);
    }
});
"""

        js_path = js_dir / "sincpro-analytics.js"
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(sincpro_js)
        print(f"✅ Sincpro JS: docs/assets/js/sincpro-analytics.js")


# Singleton instance for simple usage
site_generator = StaticSiteGenerator()
