# Copilot Agent Instructions

You are a Copilot coding agent for the **sincpro_framework** project. Your goal is to understand the codebase, follow its conventions, and ensure all changes pass the test suite.

## Before each task
1. Review `/generated_docs/ai_context/consolidated_frameworks_schema.json`  
2. Read the project’s `README.md`  
3. Check existing tests in `/tests`  
4. Follow the project’s established code conventions  
5. Apply typing and annotation best practices—provide clear type hints without being overly rigid  

## Code Style
- Run `make format` to automatically format all code  
- Use **Black** with a maximum line length of **94**  
- Use **isort** to order imports (skip sorting in `__init__.py`)  
- Document all public functions with **Google-style** docstrings  
- Prefer functional programming; when logic becomes complex, encapsulate it in classes  
