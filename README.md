<div align="center">
  <img src="docs/assets/AMAI_LOGO-RECUADRO_2026-10-11.svg" alt="AMAI Solutions" width="120" height="120">

  <h1>AMAI PDF Filler API</h1>

  <p><strong>Microservicio FastAPI para inspección y rellenado de formularios PDF AcroForm.</strong></p>

  <p>
    <img src="https://img.shields.io/badge/Python-Backend-3776AB?style=for-the-badge" alt="Python: Backend">
    <img src="https://img.shields.io/badge/FastAPI-API-009688?style=for-the-badge" alt="FastAPI: API">
    <img src="https://img.shields.io/badge/PDF-pypdf-0f9d6a?style=for-the-badge" alt="PDF: pypdf">
  </p>
</div>

| Campo | Valor |
|---|---|
| **Repositorio** | [`amai-solutions/PyPDFForm`](https://github.com/amai-solutions/PyPDFForm) |
| **Identidad** | AMAI Solutions |
| **Estado documentado** | Documentación de uso e instalación disponible; no se ha certificado despliegue activo ni se ha cotejado el número de versión anunciado. |

API minimalista en **FastAPI** para **inspeccionar y rellenar formularios PDF** usando solo **pypdf**.

* **Título**: PDF Filler API con pypdf
* **Descripción**: API de código abierto para inspeccionar y rellenar formularios PDF (AcroForm).
* **Versión**: 11.1.0
* **Stack**: Python 3.10, FastAPI, Uvicorn, pypdf

---

## 1) Requisitos

* Python 3.10+
* `pip`
* (Opcional) Docker 24+

---

## 2) Estructura del proyecto

```
.
├── app.py
├── requirements.txt
└── Dockerfile
```

---

## 3) Instalación y ejecución local (sin Docker)

1. **Crear entorno** (recomendado):

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   ```
2. **Instalar dependencias**:

   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
3. **Levantar el servidor** (hot-reload para desarrollo):

   ```bash
   uvicorn app:app --reload --host 0.0.0.0 --port 8000
   ```
4. **Probar**:

   * Salud: `GET http://localhost:8000/`
   * Docs Swagger: `http://localhost:8000/docs`
   * ReDoc: `http://localhost:8000/redoc`

---

## 4) Ejecución con Docker

1. **Construir la imagen**:

   ```bash
   docker build -t pdf-filler:latest .
   ```
2. **Ejecutar el contenedor**:

   ```bash
   docker run --rm -p 8000:8000 --name pdf-filler pdf-filler:latest
   ```
3. **Probar** (desde tu máquina):

   * `GET http://localhost:8000/`
   * `http://localhost:8000/docs`

> El `Dockerfile` usa `python:3.10-slim`, instala `requirements.txt`, copia `app.py` y expone el puerto **8000**. El comando de entrada es:
>
> ```
> uvicorn app:app --host 0.0.0.0 --port 8000
> ```

---

## 5) Endpoints

### 5.1 `GET /`

**Descripción:** Salud del servicio.

**Respuesta 200**

```json
{"message": "✅ PDF Form Filler API (pypdf edition) está funcionando."}
```

---

### 5.2 `POST /dump-fields`

**Descripción:** Inspecciona un PDF y devuelve **lista detallada de campos**: nombre, tipo, valor, posibles estados (para botones), y **páginas 1‑based** donde aparece cada campo.

**Headers**: `Content-Type: multipart/form-data`

**Campos del form**:

* `file`: PDF a inspeccionar (`application/pdf`).

**Ejemplo (cURL)**

```bash
curl -X POST \
  -F "file=@./form.pdf;type=application/pdf" \
  http://localhost:8000/dump-fields
```

**Respuesta 200 (ejemplo)**

```json
{
  "fields": [
    {
      "FieldName": "nombre",
      "FieldType": "Text",
      "FieldValue": null,
      "Pages": [1]
    },
    {
      "FieldName": "acepto",
      "FieldType": "Button",
      "FieldValue": "/Off",
      "PossibleValues": ["/1", "/Off"],
      "TrueValue": "/1",
      "Pages": [2]
    }
  ]
}
```

> **Notas**
>
> * Tipos mapeados: `/Tx`→`Text`, `/Btn`→`Button` (checkbox/radio), `/Ch`→`Choice`, `/Sig`→`Signature`.
> * Para botones, **`TrueValue`** es el primer estado distinto de `/Off`.

---

### 5.3 `POST /fill-form`

**Descripción:** Rellena un formulario PDF. Soporta **checkbox/radio** aceptando valores muy flexibles.

**Headers**: `Content-Type: multipart/form-data`

**Campos del form**:

* `file`: PDF base (`application/pdf`)
* `data`: JSON con los valores (`application/json`)

**Valores admitidos en checkboxes/radios** (se normalizan automáticamente):

* `true`, `false`, `1`, `0`, `yes`, `no`, `on`, `off`, `"/1"`, `"/Off"`, `"Yes"`, etc.

**Ejemplo de `data.json`**

```json
{
  "nombre": "Ada Lovelace",
  "email": "ada@example.com",
  "acepto": true,
  "genero": "/F"
}
```

**cURL**

```bash
curl -X POST http://localhost:8000/fill-form \
  -F "file=@./form.pdf;type=application/pdf" \
  -F "data=@./data.json;type=application/json" \
  -o filled_form.pdf
```

**Respuesta 200**: PDF rellenado (stream) con cabecera `Content-Disposition: attachment; filename=filled_...`.

> **Detalles internos importantes**
>
> * Se **elimina XFA** para forzar uso de **AcroForm** por el visor.
> * Se pide **regeneración de apariencias** y, para botones, se fijan `/V` y `/AS` para que la marca sea **visible**.
> * Los valores de texto se escriben tal cual; los botones se normalizan al estado correcto (`/Off` o el estado *On* real, p. ej. `"/1"`).

---

### 5.4 `POST /visual-mapper`

**Descripción:** Devuelve un PDF donde **cada checkbox/radio se marca en su estado “On” real**, y los campos de texto muestran su **nombre de campo** (útil para mapear).

**Headers**: `Content-Type: multipart/form-data`

**Campos del form**:

* `file`: PDF base (`application/pdf`)

**cURL**

```bash
curl -X POST http://localhost:8000/visual-mapper \
  -F "file=@./form.pdf;type=application/pdf" \
  -o visual_map.pdf
```

**Respuesta 200**: PDF con nombres/estados aplicados.

---

## 6) Errores y validaciones

* `400 Bad Request` si no envías el tipo de archivo correcto (PDF/JSON) o si falta un campo.
* `500 Internal Server Error` con detalle del error Python si el PDF está corrupto o hay un fallo inesperado.

---

## 7) Seguridad y notas

* **Sin persistencia**: los PDFs se procesan **en memoria** y se devuelven; la API **no guarda** archivos.
* Diseñado para **AcroForm** (los XFA se eliminan).
* Accede a la documentación interactiva: `/docs` o `/redoc`.

---

## 8) Despliegue (paso a paso)

### Opción A — Docker (recomendada)

1. Construir la imagen:

   ```bash
   docker build -t pdf-filler:latest .
   ```
2. Ejecutar:

   ```bash
   docker run -d --restart=always -p 8000:8000 --name pdf-filler pdf-filler:latest
   ```
3. (Opcional) Poner detrás de un **reverse proxy** (Nginx, Caddy, Traefik) y servir por HTTPS.

### Opción B — Servidor Linux (bare metal / VM)

1. Instalar Python 3.10 y `pip`.
2. Clonar/copiar el proyecto y crear **venv**.
3. Instalar `-r requirements.txt`.
4. Ejecutar Uvicorn como servicio (systemd):

   * Crear archivo `/etc/systemd/system/pdf-filler.service`:

     ```ini
     [Unit]
     Description=PDF Filler API (Uvicorn)
     After=network.target

     [Service]
     User=www-data
     WorkingDirectory=/ruta/al/proyecto
     ExecStart=/ruta/al/proyecto/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
     Restart=always

     [Install]
     WantedBy=multi-user.target
     ```
   * Recargar y arrancar:

     ```bash
     sudo systemctl daemon-reload
     sudo systemctl enable --now pdf-filler
     ```
5. (Opcional) Reverse proxy + HTTPS.

### Opción C — Plataformas de contenedores

* Funciona en **AWS ECS/Fargate**, **Azure Web Apps for Containers**, **GCP Cloud Run**, **Railway**, **Render**, **Fly.io**, etc.
* Usa la imagen construida desde tu `Dockerfile` y expón el **puerto 8000**.

---

## 9) Pruebas rápidas

* Arranca el servicio y sube un PDF de prueba en `/docs` para validar cada endpoint.
* Usa `curl` (arriba) o Postman/Insomnia.

---

## 10) FAQ

**¿Soporta formularios XFA?**
No. Eliminamos XFA para que el visor use AcroForm; el flujo está optimizado para **AcroForm**.

**¿Cómo sé qué valor activar en un checkbox/radio?**
Con `POST /dump-fields` mira `PossibleValues` y usa el distinto de `/Off` (también lo auto‑mapeamos desde `true/yes/1`).

**¿Dónde veo los nombres de campos?**

* `POST /dump-fields` devuelve `FieldName`.
* `POST /visual-mapper` imprime el nombre sobre el PDF.

---

## 11) Licencia

Libre uso interno. Ajusta la licencia según tus necesidades.

---

## 12) Créditos

* [FastAPI](https://fastapi.tiangolo.com/)
* [pypdf](https://pypdf.readthedocs.io/)
* [Uvicorn](https://www.uvicorn.org/)
