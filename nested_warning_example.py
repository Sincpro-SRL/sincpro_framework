#!/usr/bin/env python
"""
Script de ejemplo para mostrar warnings de variables de entorno en configuraciones anidadas.
Este script simula un escenario más complejo con múltiples variables de entorno faltantes
en diferentes niveles de la configuración.
"""

import os
import warnings
from typing import Dict, Optional

from sincpro_framework.sincpro_conf import SincproConfig, build_config_obj


# Clases de configuración anidadas
class DatabaseConfig(SincproConfig):
    """Configuración de la base de datos."""
    host: str = "localhost"
    port: int = 5432
    username: str = "admin"
    password: str = "secure_password"  # Solo para desarrollo, en producción usar variable de entorno
    
    
class ApiConfig(SincproConfig):
    """Configuración de la API."""
    url: str = "https://api.example.com"
    timeout: int = 30
    key: str = "default-api-key-for-dev"


class LoggingConfig(SincproConfig):
    """Configuración del sistema de logging."""
    level: str = "DEBUG"
    file_path: str = "/tmp/app.log"


class ComplexConfig(SincproConfig):
    """Configuración completa de la aplicación con componentes anidados."""
    database: DatabaseConfig = DatabaseConfig()
    api: ApiConfig = ApiConfig()
    logging: LoggingConfig = LoggingConfig()


def main():
    """Función principal que carga la configuración compleja y muestra los resultados."""
    # Configurar para mostrar todos los warnings
    warnings.filterwarnings('always')
    
    print("Cargando configuración compleja con múltiples variables de entorno faltantes...")
    print("=" * 80)
    
    # Establecemos una variable de entorno para demostrar valores mixtos
    os.environ["DB_HOST"] = "db.production.example.com"
    
    # Cargamos la configuración desde el archivo
    config_path = os.path.join(os.path.dirname(__file__), "nested_warning_example.yml")
    config = build_config_obj(ComplexConfig, config_path)
    
    # Mostramos los resultados
    print("\nResultados de la configuración:")
    print("=" * 80)
    
    print("\n[Configuración de Base de Datos]")
    print(f"Host: {config.database.host}")              # Debería usar el valor de la variable de entorno
    print(f"Port: {config.database.port}")              # Valor del archivo YAML
    print(f"Username: {config.database.username}")      # Debería usar el valor por defecto
    print(f"Password: {config.database.password}")      # Debería usar el valor por defecto
    
    print("\n[Configuración de API]")
    print(f"URL: {config.api.url}")                     # Valor del archivo YAML
    print(f"Timeout: {config.api.timeout}")             # Valor del archivo YAML
    print(f"API Key: {config.api.key}")                 # Debería usar el valor por defecto
    
    print("\n[Configuración de Logging]")
    print(f"Level: {config.logging.level}")             # Valor del archivo YAML
    print(f"File Path: {config.logging.file_path}")     # Debería usar el valor por defecto
    
    print("\nComo puedes ver, las variables de entorno faltantes han emitido warnings")
    print("y se han usado los valores por defecto definidos en las clases de configuración.")
    print("La aplicación puede continuar ejecutándose sin interrupciones.")


if __name__ == "__main__":
    main() 