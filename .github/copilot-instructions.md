# Instrucciones para Copilot Agent

## Prioridades antes de cualquier tarea:
1. Revisar la documentación existente en `/sincpro_framework/generate_documentation/sincpro_framework_ai_guide.md`
2. Revisar documentacions `/doc/architecture/` 
3. Revisar el archivo `README.md` del proyecto
2. Verificar tests existentes en `/tests`
3. Seguir las convenciones de código del proyecto
4. Ejecutar tests antes de cualquier commit

## Estilo de código:
- Usar black con lineas de 94 caracteres 
- Usar isort para ordenar imports, no ordenar en __init__.py
- Documentar funciones públicas con docstrings estilo Google
- Preferir functional programming, en caso de ser funciones complejas utilizar classes

