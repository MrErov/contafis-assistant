# Corpus normativo (RAG) — qué se espera y cómo cargarlo

Esta carpeta es donde deben vivir los **PDF de las leyes**. El repositorio los
entrega vacíos a propósito: son documentos oficiales, pesados y sujetos a
actualización, por lo que no se versionan en git (ver «Estado actual»).

El `.gitignore` cubre `data/chroma/` (los índices generados), pero **no** cubre
`data/corpus/` ni `data/manifest.json`. Si decides versionar los PDF, revisa el
peso antes de commitear.

## Estado actual del repo

Faltan estos ficheros, por eso `python scripts/ingest_corpus.py` termina con
`IngestError: Manifiesto inválido para ingesta: missing`:

| Fichero | Estado | Para qué sirve |
|---|---|---|
| `data/manifest.json` | ausente | declara documentos, hashes, páginas y versión/hash del índice BM25 |
| `data/corpus/*.pdf` | ausente | texto fuente que se trocea, embebe e indexa |
| `data/bm25_index.json` | ausente | índice BM25 que produce la ingesta |
| `data/chroma/` | presente (vacío) | almacén vectorial; `chroma.sqlite3` sin colección usable |

Sin ellos, `GET /health` devuelve `status: degraded` y `corpus_status: missing`
(nunca `ready`: `CorpusStatus` solo admite `ok`, `missing`, `empty`, `mismatch`).

## Ubicación esperada

```
data/
├── manifest.json          <- se genera a mano (ver formato abajo)
├── bm25_index.json        <- lo escribe la ingesta
├── chroma/                <- lo escribe la ingesta
└── corpus/                <- aquí van los PDF
    ├── ley-islr.pdf
    └── providencia-seniat.pdf
```

Los ficheros declarados en `manifest.json` se resuelven **relativos al directorio
del manifiesto** (`data/`), de ahí que el `file` de cada documento incluya el
prefijo `corpus/`.

## Formato de `manifest.json`

Campos validados a nivel de manifiesto: `bm25_index_version` (entero ≥ 1),
`bm25_index_sha256` (64 hex), `documents` (lista).

Campos validados por documento (todos obligatorios; `extra="forbid"`):

| Campo | Tipo | Notas |
|---|---|---|
| `id` | string | identificador estable de cita |
| `title` | string | título para la citación |
| `version` | string | versión del documento |
| `file` | string | ruta relativa a `data/`, p. ej. `corpus/ley-islr.pdf` |
| `sha256` | string | SHA-256 del PDF (64 hex, minúsculas) |
| `pages` | int (≥ 1) | nº de páginas; debe cuadrar con el PDF real |
| `published_at` | date (ISO) | p. ej. `2014-08-05` |
| `language` | string | `es` |
| `origin` | string | procedencia, p. ej. `Gaceta Oficial` |

Ejemplo mínimo con dos documentos:

```json
{
  "bm25_index_version": 1,
  "bm25_index_sha256": "cambiar_por_el_sha256_del_indice_bm25",
  "documents": [
    {
      "id": "ley-islr",
      "title": "Ley de Impuesto sobre la Renta",
      "version": "2014",
      "file": "corpus/ley-islr.pdf",
      "sha256": "0000...0000",
      "pages": 120,
      "published_at": "2014-08-05",
      "language": "es",
      "origin": "Gaceta Oficial Nº 6.152 Extraordinario"
    },
    {
      "id": "providencia-seniat",
      "title": "Providencia SNAT/2019/000009",
      "version": "2019",
      "file": "corpus/providencia-seniat.pdf",
      "sha256": "1111...1111",
      "pages": 40,
      "published_at": "2019-03-11",
      "language": "es",
      "origin": "SENIAT"
    }
  ]
}
```

No existe `manifest.example.json` versionado; el bloque anterior sirve de
plantilla. Puedes copiar `data/corpus/manifest.example.json` a `data/manifest.json`
y rellenar los `sha256` reales.

## Cómo calcular los hashes y las páginas

```powershell
# SHA-256 de cada PDF (copiar el valor en minúsculas al manifest)
Get-FileHash -Algorithm SHA256 .\data\corpus\ley-islr.pdf | Select-Object Hash
```

```python
# Número de páginas y texto extraíble
from pypdf import PdfReader
r = PdfReader("data/corpus/ley-islr.pdf")
print(len(r.pages))
```

Si un PDF es escaneado (sin capa de texto), `extract_pdf_pages` devuelve páginas
vacías y el documento no indexa nada: hará falta OCR antes de ingerir.

## Flujo de ingesta

1. Coloca los PDF en `data/corpus/`.
2. Crea `data/manifest.json` con los `sha256` reales. En `bm25_index_sha256`
   puedes poner cualquier valor de 64 hex (p. ej. `0` * 64): la ingesta no lo
   contrasta.
3. Ejecuta la ingesta desde la raíz del repo:

   ```powershell
   python scripts\ingest_corpus.py
   ```

   Lee el `.env` de la raíz (ya no depende del CWD) y usa `EMBEDDING_MODEL`,
   `OPENAI_API_KEY` y `OPENAI_BASE_URL` desde `Settings`. También acepta
   `--manifest <ruta>` si el manifiesto no está en `data/`.

4. La salida es:

   ```
   documentos=2 chunks=314 bm25_version=1 sha256=<hash real>
   ```

5. **Copia ese `sha256` en `manifest.json`** (`bm25_index_sha256`). Sin ese paso
   el servicio arranca en `corpus_status: mismatch` porque `load_manifest` sí
   compara el hash del índice contra el manifiesto.

6. Verifica:

   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/health
   ```

   Con Odoo accesible y el corpus cargado debería dar `status: ok` y
   `corpus_status: ok`. `scope_state` quedará en `scope_unsafe` mientras la API
   key de Odoo sea de administrador (ver `docs/memory.md`).

## Qué leyes incluir

La spec 001 confirma tres documentos obligatorios y uno opcional:

- Ley de Impuesto sobre la Renta (ISLR)
- Ley de Impuesto al Valor Agregado (IVA)
- Providencia SENIAT
- Opcional: Código Orgánico Tributario o Providencia SNAT/2019/000009

## De dónde descargarlas

- **Gaceta Oficial de la República Bolivariana de Venezuela** — fuente primaria
  de las leyes y sus reformas. Busca por número de gaceta y año.
- **Asamblea Nacional de Venezuela** (`asambleanacionaldevenezuela.org`) —
  textos vigentes y proyecto de ley.
- **SENIAT** (`seniat.gob.ve`, «Normativa Legal») — providencias y criterios de
  aplicación del ISLR/IVA.

Descarga siempre la **gaceta extraordinaria específica** de la reforma que
necesitas y anota el número en `origin` + la fecha en `published_at`: el corpus
debe ser trazable y la cita se apoya en esos metadatos.
