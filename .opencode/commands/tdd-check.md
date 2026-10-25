---
description: Verifica que la suite de tests esté en verde antes de commitear
agent: build
---
Ejecuta `pytest -q`. Si hay fallos, muestra los errores y NO permitas commitear. Si está todo verde, indica que se puede proceder con `/commit`.
