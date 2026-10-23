---
description: Commitea y pushea los cambios actuales con un mensaje automático
agent: build
---
Revisa `git status --short`.
Si hay cambios, haz un `git add .`, luego un `git commit -m "feat: <resumen de los cambios>"` y finalmente un `git push origin main`.
Si no hay cambios, indica que el árbol está limpio.
