import io
import json
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from fpdf import FPDF
from pydantic import BaseModel
from typing import Optional

app = FastAPI(
    title="PDF Filler API con pypdf",
    description="Una API de código abierto construida exclusivamente con pypdf para inspeccionar y rellenar formularios PDF.",
    version="11.2.0",
)

# ----------------- Helpers -----------------
def _resolve_acroform(writer):
    acro_ref = writer._root_object.get("/AcroForm")
    if not acro_ref:
        return None
    return acro_ref.get_object() if hasattr(acro_ref, "get_object") else acro_ref

def _remove_xfa(writer):
    """Quita XFA para que el visor use AcroForm."""
    acro = _resolve_acroform(writer)
    if not acro:
        return
    xfa_key = NameObject("/XFA")
    if xfa_key in acro:
        del acro[xfa_key]

def _button_states(field_dict):
    """
    Devuelve la lista de estados posibles del botón (checkbox/radio) con SLASH:
    p.ej. ['/1', '/Off'].
    Prioriza /_States_; si no, inspecciona las apariencias /AP de cada widget (/Kids).
    """
    states = []

    # 1) Muchos PDFs exponen /_States_
    raw = field_dict.get("/_States_")
    if raw:
        for s in raw:
            s = str(s)
            states.append(s if s.startswith("/") else "/" + s)

    # 2) Fallback: mirar /AP de cada widget
    widgets = field_dict.get("/Kids", []) or [field_dict]
    for w in widgets:
        ap = w.get("/AP")
        if ap and "/N" in ap:
            for k in ap["/N"].keys():
                s = str(k)
                s = s if s.startswith("/") else "/" + s
                if s not in states:
                    states.append(s)

    return states

def _on_value(field_dict) -> str:
    """Primer estado distinto de /Off; si no hay, '/Yes'."""
    for s in _button_states(field_dict):
        if s.lower() != "/off":
            return s
    return "/Yes"

def _normalize_checkbox_value(v, field_dict):
    """
    Acepta True/False, 'true'/'false', '1'/'0', 'yes'/'no', '/1', '1', '/Yes', 'Yes', 'Off', '/Off' ...
    Devuelve SIEMPRE string con slash, p.ej. '/1' o '/Off'.
    """
    # bool directo
    if isinstance(v, bool):
        return _on_value(field_dict) if v else "/Off"

    sval = str(v).strip()
    low = sval.lower()

    # strings booleanas / numéricas comunes
    if low in {"true", "1", "yes", "y", "on"}:
        return _on_value(field_dict)
    if low in {"false", "0", "no", "n", "off"}:
        return "/Off"

    # nombres exactos
    if low == "off":
        return "/Off"

    # asegurar slash
    if not sval.startswith("/"):
        sval = "/" + sval
    return sval

def _build_full_name(annot):
    """Construye el nombre completo del campo recorriendo la cadena /Parent."""
    parts = []
    obj = annot
    while obj:
        t = obj.get("/T")
        if t:
            parts.append(str(t))
        parent = obj.get("/Parent")
        if parent:
            try:
                obj = parent.get_object()
            except Exception:
                obj = parent
        else:
            obj = None
    parts.reverse()
    return ".".join(parts)

def _apply_checkbox_appearances(writer, btn_values_by_name):
    """
    Fija /AS y /V en cada widget de checkbox Y estampa un overlay visual
    con checkmarks, porque pdf.js no renderiza correctamente los cambios de /AS.
    """
    # Paso 1: Setear /AS y /V en las anotaciones + recopilar posiciones
    checked_by_page = {}  # page_idx -> list of (x1, y1, x2, y2)

    for page_idx, page in enumerate(writer.pages):
        annots = page.get("/Annots", [])
        for annot_ref in annots:
            try:
                annot = annot_ref.get_object()
            except Exception:
                annot = annot_ref
            ft = annot.get("/FT")
            if not ft:
                parent = annot.get("/Parent")
                if parent:
                    try:
                        parent_obj = parent.get_object()
                    except Exception:
                        parent_obj = parent
                    ft = parent_obj.get("/FT")
            if ft != "/Btn":
                continue
            full_name = _build_full_name(annot)
            sval = btn_values_by_name.get(full_name)
            if not sval:
                continue
            val = NameObject(sval)
            annot.update({NameObject("/AS"): val, NameObject("/V"): val})

            # Si está marcado (no /Off), guardar posición para overlay
            if sval.lower() != "/off":
                rect = annot.get("/Rect")
                if rect:
                    coords = [float(v) for v in rect]
                    checked_by_page.setdefault(page_idx, []).append(coords)

    # Paso 2: Crear overlay con checkmarks para cada página que tenga checks
    for page_idx, rects in checked_by_page.items():
        page = writer.pages[page_idx]
        box = page.mediabox
        w_pt = float(box.width)
        h_pt = float(box.height)

        overlay = FPDF(orientation="P", unit="pt", format=(w_pt, h_pt))
        overlay.set_margin(0)
        overlay.add_page()

        for x1, y1, x2, y2 in rects:
            cx = (x1 + x2) / 2
            cy_fpdf = h_pt - (y1 + y2) / 2
            size = min(x2 - x1, y2 - y1) * 0.7
            overlay.set_font("ZapfDingbats", "", size)
            overlay.set_text_color(0, 0, 0)
            overlay.set_xy(cx - size / 2, cy_fpdf - size / 2)
            overlay.cell(size, size, chr(0x34), align="C")

        overlay_reader = PdfReader(io.BytesIO(overlay.output()))
        page.merge_page(overlay_reader.pages[0])

def _pages_of_field(reader: PdfReader, field_dict) -> list[int]:
    """
    Devuelve una lista de índices de página (1-based) donde aparecen los widgets del campo.
    """
    pages = []
    widgets = field_dict.get("/Kids", []) or [field_dict]
    for w in widgets:
        pref = w.get("/P")
        if not pref:
            continue
        try:
            pobj = pref.get_object()
        except Exception:
            pobj = pref
        for i, page in enumerate(reader.pages):
            try:
                pg_obj = page.get_object()
            except Exception:
                pg_obj = page
            if pg_obj == pobj:
                pnum = i + 1  # 1-based para humanos
                if pnum not in pages:
                    pages.append(pnum)
                break
    return pages

def _apply_text_overlays(writer, overlays):
    """
    Aplica textos libres sobre páginas del PDF.
    Cada overlay: { "page": 2, "x": 400, "y": 150, "text": "Statement 5", "fontSize": 8, "bold": false }
    - page: número de página (1-based)
    - x, y: coordenadas en puntos PDF (origen abajo-izquierda)
    - text: texto a dibujar
    - fontSize: tamaño de fuente (default 8)
    - bold: si usar negrita (default false)
    """
    # Agrupar overlays por página
    by_page = {}
    for ov in overlays:
        pg = ov.get("page", 1) - 1  # convertir a 0-based
        by_page.setdefault(pg, []).append(ov)

    for page_idx, items in by_page.items():
        if page_idx < 0 or page_idx >= len(writer.pages):
            continue
        page = writer.pages[page_idx]
        box = page.mediabox
        w_pt = float(box.width)
        h_pt = float(box.height)

        overlay = FPDF(orientation="P", unit="pt", format=(w_pt, h_pt))
        overlay.set_margin(0)
        overlay.add_page()

        for item in items:
            x = float(item.get("x", 0))
            y_pdf = float(item.get("y", 0))
            text = str(item.get("text", ""))
            font_size = float(item.get("fontSize", 8))
            bold = item.get("bold", False)

            # Convertir coordenadas PDF (origen abajo-izq) a FPDF (origen arriba-izq)
            y_fpdf = h_pt - y_pdf

            style = "B" if bold else ""
            overlay.set_font("Helvetica", style, font_size)
            overlay.set_text_color(0, 0, 0)
            overlay.set_xy(x, y_fpdf - font_size * 0.4)
            overlay.cell(0, font_size, text)

        overlay_reader = PdfReader(io.BytesIO(overlay.output()))
        page.merge_page(overlay_reader.pages[0])

# ----------------- Endpoints -----------------
@app.post("/dump-fields")
async def dump_fields(file: UploadFile = File(...)):
    """
    Inspecciona un PDF y devuelve lista de campos:
    - FieldName, FieldType, FieldValue
    - PossibleValues (para /Btn, p.ej. ['/1','/Off'])
    - TrueValue (valor que se usará si pasas true)
    - Pages (páginas 1-based donde aparece el campo)
    """
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    try:
        reader = PdfReader(io.BytesIO(await file.read()))
        fields = reader.get_fields() or {}
        field_type_map = {"/Tx": "Text", "/Btn": "Button", "/Ch": "Choice", "/Sig": "Signature"}

        detailed = []
        for name, fobj in fields.items():
            ftype = fobj.get("/FT")

            if ftype is None:
                continue

            item = {
                "FieldName": name,
                "FieldType": field_type_map.get(ftype, str(ftype)),
                "FieldValue": fobj.get("/V"),
                "Pages": _pages_of_field(reader, fobj),
            }
            if ftype == "/Btn":
                opts = _button_states(fobj)
                item["PossibleValues"] = opts  # con slash siempre
                item["TrueValue"] = _on_value(fobj)  # a esto se mapea 'true'
            detailed.append(item)

        return {"fields": detailed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al procesar el PDF: {type(e).__name__} - {e}")

@app.post("/fill-form")
async def fill_form(file: UploadFile = File(...), data: UploadFile = File(...)):
    """
    Rellena un formulario PDF.
    Para checkboxes acepta: true/false, '/1', '1', '/Yes', 'Yes', 'Off', '/Off', etc.
    """
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="El archivo 'file' debe ser un PDF.")
    if data.content_type != "application/json":
        raise HTTPException(status_code=400, detail="El archivo 'data' debe ser un JSON.")

    try:
        reader = PdfReader(io.BytesIO(await file.read()))
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        _remove_xfa(writer)

        # pedir al visor que regenere apariencias por si acaso
        try:
            writer.set_need_appearances_writer(True)
        except Exception:
            pass

        form_data = json.loads((await data.read()).decode("utf-8"))
        if not isinstance(form_data, dict):
            raise HTTPException(status_code=400, detail="El JSON debe ser un objeto.")

        # Extraer overlays si existen (texto libre en posiciones arbitrarias)
        overlays = form_data.pop("__overlays", None)

        fields = reader.get_fields() or {}
        mapping = {}
        btn_map = {}

        # Construye el mapping normal y uno aparte para botones
        for name, fobj in fields.items():
            if name not in form_data:
                continue
            if fobj.get("/FT") == "/Btn":
                sval = _normalize_checkbox_value(form_data[name], fobj)  # '/1' o '/Off'
                mapping[name] = sval
                btn_map[name] = sval
            else:
                mapping[name] = form_data[name]

        # Rellenamos sin regeneración automática; luego forzamos apariencias
        for p in writer.pages:
            writer.update_page_form_field_values(p, mapping, auto_regenerate=False)

        _apply_checkbox_appearances(writer, btn_map)

        # Aplicar overlays de texto libre si se proporcionaron
        if overlays and isinstance(overlays, list):
            _apply_text_overlays(writer, overlays)

        out = io.BytesIO()
        writer.write(out)
        out.seek(0)
        return StreamingResponse(
            out,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=filled_{getattr(file, 'filename', 'form')}"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al rellenar el PDF: {type(e).__name__} - {e}")

@app.post("/visual-mapper")
async def visual_mapper(file: UploadFile = File(...)):
    """
    Pinta textos con el nombre del campo y marca TODOS los checkboxes en su
    estado 'On' real (p. ej. '/1').
    """
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    try:
        reader = PdfReader(io.BytesIO(await file.read()))
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        _remove_xfa(writer)
        try:
            writer.set_need_appearances_writer(True)
        except Exception:
            pass

        fields = reader.get_fields()
        if not fields:
            raise HTTPException(status_code=400, detail="El PDF no contiene campos de formulario.")

        mapping = {}
        btn_map = {}

        for name, fobj in fields.items():
            if fobj.get("/FT") == "/Btn":
                onv = _on_value(fobj)  # p.ej. '/1'
                mapping[name] = onv
                btn_map[name] = onv
            else:
                mapping[name] = name[-35:]

        for p in writer.pages:
            writer.update_page_form_field_values(p, mapping, auto_regenerate=False)

        _apply_checkbox_appearances(writer, btn_map)

        out = io.BytesIO()
        writer.write(out)
        out.seek(0)
        return StreamingResponse(
            out,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=visual_map.pdf"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al mapear el PDF: {type(e).__name__} - {e}")

# ----------------- Stamp Header -----------------
def _create_header_overlay(width_pt: float, height_pt: float, text: str) -> bytes:
    """Crea un PDF de 1 página con solo el texto gris centrado arriba."""
    w_mm = width_pt * 25.4 / 72
    h_mm = height_pt * 25.4 / 72
    overlay = FPDF(orientation="P", unit="mm", format=(w_mm, h_mm))
    overlay.set_margin(0)
    overlay.add_page()
    overlay.set_font("Helvetica", "", 8)
    overlay.set_text_color(160, 160, 160)
    overlay.set_y(3)
    overlay.cell(w_mm, 5, text, align="C")
    return overlay.output()

@app.post("/stamp-header")
async def stamp_header(
    file: UploadFile = File(...),
    text: str = "Foreign-Owned U.S. DE",
):
    """
    Recibe un PDF y estampa un texto gris centrado en la parte superior
    de cada página. Devuelve el PDF modificado.
    """
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    try:
        reader = PdfReader(io.BytesIO(await file.read()))
        writer = PdfWriter()

        for page in reader.pages:
            box = page.mediabox
            w = float(box.width)
            h = float(box.height)
            overlay_bytes = _create_header_overlay(w, h, text)
            overlay_reader = PdfReader(io.BytesIO(overlay_bytes))
            page.merge_page(overlay_reader.pages[0])
            writer.add_page(page)

        out = io.BytesIO()
        writer.write(out)
        out.seek(0)
        return StreamingResponse(
            out,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=stamped_{getattr(file, 'filename', 'doc.pdf')}"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al estampar header: {type(e).__name__} - {e}")

# ----------------- Supporting Statement -----------------
class StatementRequest(BaseModel):
    business_name: str
    ein: str
    tax_year: str = "2024"
    capital_contributions_usd: Optional[float] = None
    capital_distributions_usd: Optional[float] = None
    llc_cost_creation_usd: Optional[float] = None
    owner_name: str = ""

@app.post("/generate-statement")
async def generate_statement(req: StatementRequest):
    """
    Genera el PDF 'Federal Supporting Statements' para LLC Single Member (DE).
    Incluye Part V (Statement 5) y Part VI del Form 5472.
    """
    try:
        pdf = FPDF(orientation="P", unit="mm", format="Letter")
        pdf.set_auto_page_break(auto=True, margin=25)
        pdf.add_page()

        # --- Header (gray text) ---
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(160, 160, 160)
        pdf.cell(0, 6, "Foreign-Owned U.S. DE", ln=True, align="C")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(6)

        # --- Title ---
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 8, "Federal Supporting Statements", ln=True, align="C")
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 8, req.tax_year, ln=True, align="C")
        pdf.ln(10)

        # --- Name & EIN ---
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 7, f"Name(s) as shown on return:  {req.business_name}", ln=True)
        pdf.ln(2)

        ein_formatted = req.ein
        if len(req.ein) == 9 and "-" not in req.ein:
            ein_formatted = f"{req.ein[:2]}-{req.ein[2:]}"
        pdf.cell(0, 7, f"Tax ID Number:  {ein_formatted}", ln=True)
        pdf.ln(10)

        # --- PART V - Statement 5 ---
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, "FORM 5472 - PAGE 2 - PART V - Statement 5", ln=True)
        pdf.ln(15)

        pdf.set_font("Helvetica", "", 11)

        if req.capital_contributions_usd is not None:
            pdf.cell(0, 7, f"CAPITAL CONTRIBUTIONS: {req.capital_contributions_usd:,.2f}$", ln=True)
        if req.capital_distributions_usd is not None:
            pdf.cell(0, 7, f"CAPITAL DISTRIBUTIONS: {req.capital_distributions_usd:,.2f}$", ln=True)
        if req.llc_cost_creation_usd is not None:
            pdf.cell(0, 7, f"LLC COST CREATION: {req.llc_cost_creation_usd:,.2f}$", ln=True)
        pdf.ln(10)

        # --- PART VI ---
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, "FORM 5472 - PAGE 2 - PART VI", ln=True)
        pdf.ln(4)
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 7, "THE FOREIGN RELATED PARTY IS THE MEMBER OF THE REPORTING CORPORATION")

        # --- Output ---
        out = io.BytesIO(pdf.output())
        out.seek(0)

        filename = f"Supporting_Statement_{req.tax_year}_{req.ein}.pdf"
        return StreamingResponse(
            out,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al generar statement: {type(e).__name__} - {e}")

@app.get("/")
def read_root():
    return {"message": "✅ PDF Form Filler API (pypdf edition) está funcionando."}
