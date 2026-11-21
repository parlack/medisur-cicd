"""Render the existing school draft and its editable diagram without network access.

Run with C:\\Python314\\python.exe scripts/render-report.py. Dependencies:
python-docx, markdown-it-py, Pillow. Only architecture.png and reporte.docx
are written. Use --diagram-only to write only architecture.png, without reading
the report or creating a DOCX. Draw.io XML owns diagram labels and geometry.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from markdown_it import MarkdownIt
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
PARSER = MarkdownIt("commonmark", {"html": False}).enable("table")
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
SECRET_PATTERNS = (
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"\bgh[pousr]_[A-Za-z0-9]{20,}\b",
    r"\bgithub_pat_[A-Za-z0-9_]{20,}\b",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b",
    r"(?im)^\s*(?:client-key-data|password|passwd|token|api_key|secret)\s*[:=]\s*\S{8,}",
)


def check_secrets(text):
    if any(re.search(pattern, text) for pattern in SECRET_PATTERNS):
        raise ValueError("Possible credential detected; output blocked (value not logged).")


def discover_evidence():
    descriptions = {
        "grafana-medisur-infrastructure.png": (
            "Grafana, tablero Medisur - Infrastructure: CPU, memoria y disco de las dos "
            "máquinas virtuales."),
        "grafana-medisur-application.png": (
            "Grafana, tablero Medisur - Application: réplicas listas contra deseadas, "
            "versión por pod, peticiones por segundo, respuestas 5xx, latencia p95 y reinicios."),
        "jenkins-main-stages.png": (
            "Jenkins, job medisur-main: vista de etapas del pipeline, todas en verde."),
        "jenkins-main-build-3.png": (
            "Jenkins, medisur-main build 3: resultado SUCCESS con los artefactos de "
            "cobertura y JUnit y las 35 pruebas aprobadas."),
        "jenkins-quality-demo-tests.png": (
            "Jenkins, medisur-quality-demo build 2: 35 pruebas aprobadas, cero fallos "
            "y cero omisiones."),
        "sonar-medisur-quality-demo.png": (
            "SonarQube, proyecto MediSur Quality Gate Demo: puerta en ERROR con 37.7 % "
            "de cobertura frente al mínimo de 60 %."),
        "sonar-medisur-cicd.png": (
            "SonarQube, proyecto Medisur Pilot API, rama main: puerta aprobada con "
            "93.9 % de cobertura frente al mínimo de 60 %."),
    }
    files = {path.name: path for path in (DOCS / "evidence").glob("*.png")}
    if files.keys() - descriptions.keys():
        raise ValueError("Unrecognized evidence PNG; review its caption before embedding.")
    evidence = []
    for name, description in descriptions.items():
        if name not in files:
            continue
        path = files[name]
        modified = path.stat().st_mtime_ns
        data = path.read_bytes()
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG":
                raise ValueError(f"Evidence is not PNG: {name}")
            image.verify()
        date = datetime.fromtimestamp(modified / 1_000_000_000, timezone.utc)
        caption = (
            f"Figura {len(evidence) + 2}. {description} "
            f"Fuente: docs/evidence/{name}."
        )
        evidence.append({"path": path, "data": data, "modified": modified, "caption": caption})
    return evidence


def add_evidence_figures(document, evidence):
    for item in evidence:
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.page_break_before = True
        paragraph.paragraph_format.keep_with_next = True
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        picture = paragraph.add_run().add_picture(BytesIO(item["data"]), width=Cm(17))
        picture._inline.docPr.set("descr", item["caption"])
        caption = document.add_paragraph(item["caption"], style="Caption")
        caption.paragraph_format.keep_together = True
        caption.paragraph_format.keep_with_next = False
    return len(evidence)


def style_map(cell):
    return dict(item.split("=", 1) for item in cell.get("style", "").split(";") if "=" in item)


def diagram_font(size, bold=False):
    for path in (
        Path("C:/Windows/Fonts") / ("calibrib.ttf" if bold else "calibri.ttf"),
        Path("/usr/share/fonts/truetype/dejavu") / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
    ):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def render_diagram(source, destination):
    model = ET.parse(source).find(".//mxGraphModel")
    if model is None:
        raise ValueError("Expected uncompressed draw.io mxGraphModel.")
    width, height = int(model.get("pageWidth")), int(model.get("pageHeight"))
    scale = 2
    image = Image.new("RGB", (width * scale, height * scale), "white")
    draw = ImageDraw.Draw(image)
    cells = model.findall("./root/mxCell")
    vertices = {cell.get("id"): cell for cell in cells if cell.get("vertex") == "1"}

    def box(cell):
        geom = cell.find("mxGeometry")
        return tuple(float(geom.get(key, "0")) for key in ("x", "y", "width", "height"))

    def point(cell, prefix):
        settings = style_map(cell)
        vertex = vertices[cell.get("source" if prefix == "exit" else "target")]
        x, y, size_x, size_y = box(vertex)
        return (x + size_x * float(settings.get(prefix + "X", ".5")),
                y + size_y * float(settings.get(prefix + "Y", ".5")))

    def scaled(point_value):
        return tuple(round(value * scale) for value in point_value)

    def draw_vertex(cell):
        x, y, size_x, size_y = box(cell)
        settings = style_map(cell)
        bounds = (scaled((x, y)), scaled((x + size_x, y + size_y)))
        is_text = cell.get("style", "").startswith("text;")
        fill = settings.get("fillColor", None if is_text else "#ffffff")
        outline = None if is_text else settings.get("strokeColor", "#648aae")
        if not is_text and settings.get("rounded") == "1":
            draw.rounded_rectangle(bounds, radius=12 * scale, fill=fill, outline=outline, width=2 * scale)
        elif fill:
            draw.rectangle(bounds, fill=fill, outline=outline, width=2 * scale)
        text = cell.get("value", "")
        if not text:
            return
        font_size = int(settings.get("fontSize", "26"))
        bold = int(settings.get("fontStyle", "0")) & 1
        while font_size >= 16:
            font = diagram_font(font_size * scale, bold)
            lines = []
            for original_line in text.splitlines():
                line = ""
                for word in original_line.split():
                    candidate = (line + " " + word).strip()
                    if line and draw.textlength(candidate, font=font) > (size_x - 24) * scale:
                        lines.append(line)
                        line = word
                    else:
                        line = candidate
                lines.append(line)
            line_height = font_size * 1.25 * scale
            if len(lines) * line_height <= (size_y - 8) * scale and all(
                draw.textlength(line, font=font) <= (size_x - 20) * scale for line in lines
            ):
                break
            font_size -= 1
        else:
            raise ValueError(f"Diagram text does not fit: {cell.get('id')}")
        text_y = (y + size_y / 2) * scale - len(lines) * line_height / 2
        for line in lines:
            text_x = ((x + 12) * scale if settings.get("align") == "left" else
                      (x + size_x / 2) * scale - draw.textlength(line, font=font) / 2)
            draw.text((text_x, text_y), line, font=font, fill=settings.get("fontColor", "#17324d"), anchor="lt")
            text_y += line_height

    # Container backgrounds precede edges; nodes and labels sit above them.
    for cell in vertices.values():
        if not cell.get("value"):
            draw_vertex(cell)
    for edge in cells:
        if edge.get("edge") != "1":
            continue
        settings = style_map(edge)
        points = [point(edge, "exit")]
        points += [(float(node.get("x")), float(node.get("y"))) for node in edge.findall("./mxGeometry/Array/mxPoint")]
        points.append(point(edge, "entry"))
        color = settings.get("strokeColor", "#586d80")
        for start, end in zip(points, points[1:]):
            distance = math.dist(start, end)
            if not distance:
                continue
            if settings.get("dashed") == "1":
                for offset in range(0, math.ceil(distance), 17):
                    finish = min(offset + 10, distance)
                    segment = [tuple(start[axis] + (end[axis] - start[axis]) * part / distance for axis in (0, 1))
                               for part in (offset, finish)]
                    draw.line([scaled(value) for value in segment], fill=color, width=3 * scale)
            else:
                draw.line([scaled(start), scaled(end)], fill=color, width=3 * scale)
        end, previous = points[-1], points[-2]
        angle = math.atan2(end[1] - previous[1], end[0] - previous[0])
        arrow = [end] + [(end[0] - 16 * math.cos(angle) + side * 8 * math.sin(angle),
                          end[1] - 16 * math.sin(angle) - side * 8 * math.cos(angle)) for side in (-1, 1)]
        draw.polygon([scaled(value) for value in arrow], fill=color)
    for cell in vertices.values():
        if cell.get("value"):
            draw_vertex(cell)
    image.save(destination, dpi=(300, 300))


def add_field(paragraph, instruction, placeholder):
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    code = OxmlElement("w:instrText")
    code.set(qn("xml:space"), "preserve")
    code.text = " " + instruction + " "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    result = OxmlElement("w:t")
    result.text = placeholder
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for node in (begin, code, separate, result, end):
        run._r.append(node)


def inline(paragraph, tokens):
    bold = italic = 0
    link = None
    for token in tokens or []:
        if token.type in ("strong_open", "strong_close"):
            bold += 1 if token.type.endswith("open") else -1
        elif token.type in ("em_open", "em_close"):
            italic += 1 if token.type.endswith("open") else -1
        elif token.type in ("link_open", "link_close"):
            link = None
            if token.type == "link_open":
                target = token.attrGet("href")
                if target and not re.match(r"(?i)^(?:javascript|data|vbscript):", target):
                    link = OxmlElement("w:hyperlink")
                    relation = paragraph.part.relate_to(target, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
                    link.set(qn("r:id"), relation)
                    paragraph._p.append(link)
        elif token.type in ("softbreak", "hardbreak"):
            paragraph.add_run(" " if token.type == "softbreak" else "\n")
        elif token.type in ("text", "code_inline", "image"):
            content = token.content
            if token.type == "text":
                content = re.sub(r"^\[ \]\s*", "☐ ", content)
                content = re.sub(r"^\[[xX]\]\s*", "☑ ", content)
            run = paragraph.add_run(content)
            run.bold, run.italic = bool(bold), bool(italic)
            if token.type == "code_inline":
                run.font.name = "Consolas"
                run.font.size = Pt(9)
            if link is not None:
                run.font.color.rgb = RGBColor.from_string("245B83")
                run.underline = True
                link.append(run._r)
        else:
            raise ValueError(f"Unsupported inline Markdown token: {token.type}")


def create_document(markdown, png, destination, created, evidence=()):
    tokens = PARSER.parse(markdown)
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.1)
    section.left_margin = section.right_margin = Cm(2)
    section.header_distance = section.footer_distance = Cm(0.9)
    normal = document.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(11)
    normal.paragraph_format.space_after = Pt(7)
    normal.paragraph_format.line_spacing = 1.08
    for level in range(1, 7):
        heading = document.styles[f"Heading {level}"]
        heading.font.name = "Calibri"
        heading.font.color.rgb = RGBColor.from_string("17324D")
        heading.paragraph_format.keep_with_next = True
    for name in ("List Bullet", "List Number"):
        document.styles[name].font.name = "Calibri"
        document.styles[name].font.size = Pt(11)
    header = section.header.paragraphs[0]
    header.add_run("MediSur | Piloto de integración y despliegue continuo").font.size = Pt(9)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run(f"Generado: {created:%d/%m/%Y} | Página ").font.size = Pt(9)
    add_field(footer, "PAGE", "1")
    footer.add_run(" de ")
    add_field(footer, "NUMPAGES", "—")
    document.core_properties.created = created.astimezone(timezone.utc).replace(tzinfo=None)
    document.core_properties.modified = document.core_properties.created
    document.core_properties.author = ""
    document.core_properties.title = "MediSur: piloto de integración y despliegue continuo"
    document.core_properties.subject = "Cómputo en la Nube · Caso MediSur, S.A. de C.V."
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "true")
    document.settings.element.append(update)
    lists = []
    item_first = []
    paragraph = None
    table = row = cell = None
    table_count = architecture_count = evidence_count = heading_count = 0
    in_architecture = False
    evidence_pending = False
    evidence_inserted = False
    first_h1 = True

    for index, token in enumerate(tokens):
        kind = token.type
        if kind == "heading_open":
            level = int(token.tag[1])
            heading_text = tokens[index + 1].content
            if level == 2 and re.match(r"9\.\s", heading_text):
                evidence_pending = True
            if level == 2 and re.match(r"10\.\s", heading_text) and evidence_pending:
                if evidence_inserted or architecture_count != 1:
                    raise ValueError("Evidence must follow exactly one architecture diagram and section 9.")
                evidence_count = add_evidence_figures(document, evidence)
                evidence_pending = False
                evidence_inserted = True
            if level <= 2:
                in_architecture = "arquitectura" in heading_text.casefold()
            if level == 1 and first_h1:
                paragraph = document.add_paragraph(style="Title")
            else:
                if level == 2 and re.match(r"2\.\s", heading_text):
                    document.add_page_break()
                paragraph = document.add_heading(level=min(level - 1, 6) if level > 1 else 1)
                if level == 2 and re.match(r"10\.\s", heading_text) and evidence_count:
                    paragraph.paragraph_format.page_break_before = True
                heading_count += 1
        elif kind == "heading_close":
            if first_h1 and token.tag == "h1":
                document.add_paragraph("Cómputo en la Nube", style="Subtitle")
                document.add_paragraph(f"Fecha de generación: {created:%d/%m/%Y %H:%M %z}")
                first_h1 = False
            paragraph = None
        elif kind == "paragraph_open":
            if cell is not None:
                paragraph = cell.paragraphs[0] if not cell.paragraphs[0].text else cell.add_paragraph()
            elif lists and item_first[-1]:
                paragraph = document.add_paragraph(style="List Number" if lists[-1] == "ordered_list_open" else "List Bullet")
                paragraph.paragraph_format.left_indent = Cm(0.65 * len(lists))
                item_first[-1] = False
            else:
                paragraph = document.add_paragraph()
                if lists:
                    paragraph.paragraph_format.left_indent = Cm(0.65 * len(lists))
        elif kind == "inline":
            if paragraph is None:
                raise ValueError("Inline content without paragraph")
            inline(paragraph, token.children)
        elif kind in ("paragraph_close", "th_close", "td_close"):
            paragraph = None
            if kind != "paragraph_close":
                cell = None
        elif kind in ("ordered_list_open", "bullet_list_open"):
            lists.append(kind)
        elif kind in ("ordered_list_close", "bullet_list_close"):
            lists.pop()
        elif kind == "list_item_open":
            item_first.append(True)
        elif kind == "list_item_close":
            item_first.pop()
        elif kind == "table_open":
            end = next(position for position in range(index + 1, len(tokens)) if tokens[position].type == "tr_close")
            columns = sum(child.type == "th_open" for child in tokens[index:end])
            table = document.add_table(rows=0, cols=columns)
            table.style = "Table Grid"
            table.autofit = False
            for column in table.columns:
                column.width = Cm(17 / columns)
            table_count += 1
        elif kind == "tr_open":
            row = table.add_row()
            cell_index = 0
            prevent_split = OxmlElement("w:cantSplit")
            row._tr.get_or_add_trPr().append(prevent_split)
        elif kind in ("th_open", "td_open"):
            cell = row.cells[cell_index]
            cell_index += 1
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(4)
            if kind == "th_open":
                if cell_index == 1:
                    row._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
                shading = OxmlElement("w:shd")
                shading.set(qn("w:fill"), "EAF1F7")
                cell._tc.get_or_add_tcPr().append(shading)
        elif kind == "table_close":
            for number, table_row in enumerate(table.rows):
                for table_cell in table_row.cells:
                    for cell_paragraph in table_cell.paragraphs:
                        for run in cell_paragraph.runs:
                            run.font.size = Pt(9)
                            if number == 0:
                                run.bold = True
            document.add_paragraph().paragraph_format.space_after = Pt(2)
            table = row = cell = None
        elif kind in ("fence", "code_block"):
            if token.info.strip().casefold() == "mermaid" and in_architecture:
                document.add_picture(str(png), width=Cm(17))
                document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                caption = document.add_paragraph(
                    "Figura 1. Arquitectura del piloto. El flujo de entrega se lee de izquierda a derecha "
                    "y el de monitoreo en sentido inverso. Diagrama editable: docs/architecture.drawio.", style="Caption")
                caption.paragraph_format.keep_with_next = False
                architecture_count += 1
            else:
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.left_indent = Cm(0.3)
                run = paragraph.add_run(token.content.rstrip("\n"))
                run.font.name, run.font.size = "Consolas", Pt(9)
        elif kind == "hr":
            document.add_paragraph("—" * 12)
        elif kind in ("thead_open", "thead_close", "tbody_open", "tbody_close", "tr_close", "blockquote_open", "blockquote_close"):
            continue
        else:
            raise ValueError(f"Unsupported block Markdown token: {kind}")
    if architecture_count != 1:
        raise ValueError("Expected exactly one architecture Mermaid block to replace.")
    if evidence and (not evidence_inserted or evidence_pending):
        raise ValueError("Expected sections 9 and 10 to place evidence before section 10.")
    document.save(destination)
    return {"tables": table_count, "images": architecture_count + evidence_count,
            "architecture": architecture_count, "evidence": evidence_count, "headings": heading_count}


def visible_source_text(markdown):
    expected = []
    for token in PARSER.parse(markdown):
        if token.type == "inline":
            for child in token.children or []:
                if child.type in ("text", "code_inline"):
                    text = re.sub(r"^\[[ xX]\]\s*", "", child.content)
                    if text.strip():
                        expected.append(text)
        elif token.type in ("fence", "code_block") and token.info.strip().casefold() != "mermaid":
            expected.append(token.content.strip())
    return expected


def validate_docx(path, markdown, counts, png, evidence=()):
    with ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("Corrupt DOCX ZIP member")
        xml = archive.read("word/document.xml")
        root = ET.fromstring(xml)
        text = "\n".join(
            "".join(node.text or "" if node.tag == qn("w:t") else "\n"
                    for node in paragraph.iter() if node.tag in (qn("w:t"), qn("w:br")))
            for paragraph in root.findall(".//w:p", NS)
        )
        normalized = " ".join(text.split())
        for expected in visible_source_text(markdown):
            if " ".join(expected.split()) not in normalized:
                raise ValueError("Source content missing from DOCX (value not logged).")
        if len(root.findall(".//w:tbl", NS)) != counts["tables"]:
            raise ValueError("Table count mismatch")
        if len(root.findall(".//w:drawing", NS)) != counts["images"]:
            raise ValueError("Image count mismatch")
        headings = [paragraph for paragraph in root.findall("./w:body/w:p", NS)
                    if (style := paragraph.find("w:pPr/w:pStyle", NS)) is not None
                    and style.get(qn("w:val"), "").startswith("Heading")]
        if len(headings) != counts["headings"]:
            raise ValueError("Heading count mismatch")
        if evidence:
            paragraphs = root.findall("./w:body/w:p", NS)
            texts = ["".join(paragraph.itertext()) for paragraph in paragraphs]
            start = next(index for index, paragraph in enumerate(paragraphs)
                         if paragraph in headings and re.match(r"9\.\s", texts[index]))
            end = next(index for index, paragraph in enumerate(paragraphs)
                       if paragraph in headings and re.match(r"10\.\s", texts[index]))
            for item in evidence:
                position = texts.index(item["caption"])
                if not start < position < end or paragraphs[position - 1].find(".//w:drawing", NS) is None:
                    raise ValueError("Evidence caption or placement mismatch")
                start = position
        if "```" in text or "flowchart LR" in text or re.search(r"(?m)^#{1,6}\s", text):
            raise ValueError("Raw Markdown or Mermaid in DOCX")
        for name in archive.namelist():
            if name.endswith((".xml", ".rels")):
                check_secrets(archive.read(name).decode("utf-8"))
        header = ET.fromstring(archive.read("word/header1.xml"))
        if "MediSur" not in "".join(header.itertext()):
            raise ValueError("Missing document header")
        footer = ET.fromstring(archive.read("word/footer1.xml"))
        fields = [node.text.strip() for node in footer.findall(".//w:instrText", NS)]
        if fields != ["PAGE", "NUMPAGES"]:
            raise ValueError("Missing page fields")
        media = [name for name in archive.namelist() if name.startswith("word/media/")]
        expected_images = [png.read_bytes()] + [item["data"] for item in evidence]
        if counts["architecture"] != 1 or counts["images"] != len(expected_images):
            raise ValueError("Architecture or evidence count mismatch")
        expected_hashes = {hashlib.sha256(data).digest() for data in expected_images}
        actual_hashes = {hashlib.sha256(archive.read(name)).digest() for name in media}
        if len(media) != len(expected_hashes) or actual_hashes != expected_hashes:
            raise ValueError("Embedded PNG contents mismatch")
        relations = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
        image_targets = {relation.get("Id"): "word/" + relation.get("Target")
                         for relation in relations
                         if relation.get("Type") == RELATIONSHIP_TYPE.IMAGE
                         and relation.get("TargetMode") != "External"}
        blips = root.findall(".//" + qn("a:blip"))
        embedded_images = [archive.read(image_targets[blip.get(qn("r:embed"))]) for blip in blips]
        if embedded_images != expected_images:
            raise ValueError("Embedded architecture or evidence order/bytes mismatch")
        embedded = embedded_images[0]
    return embedded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagram-only", action="store_true",
                        help="Render and validate architecture.png only; never read or write the report.")
    args = parser.parse_args()
    if args.diagram_only:
        diagram = DOCS / "architecture.drawio"
        diagram_bytes = diagram.read_bytes()
        check_secrets(diagram_bytes.decode("utf-8"))
        with TemporaryDirectory(prefix="medisur-diagram-") as temporary:
            png = Path(temporary) / "architecture.png"
            render_diagram(diagram, png)
            with Image.open(png) as image:
                dimensions = image.size
                image.verify()
            if diagram.read_bytes() != diagram_bytes:
                raise RuntimeError("Diagram changed during rendering; rerun with a stable source.")
            (DOCS / png.name).write_bytes(png.read_bytes())
        print(f"Created {DOCS / 'architecture.png'} ({dimensions[0]} x {dimensions[1]} px)")
        print("Diagram validated; report Markdown and DOCX were not read or written.")
        return
    source = DOCS / "reporte.md"
    diagram = DOCS / "architecture.drawio"
    source_bytes = source.read_bytes()
    diagram_bytes = diagram.read_bytes()
    markdown = source_bytes.decode("utf-8-sig")
    check_secrets(markdown)
    check_secrets(diagram_bytes.decode("utf-8"))
    evidence = discover_evidence()
    created = datetime.now().astimezone()
    # Validate temporary binaries before replacing the two generated deliverables.
    with TemporaryDirectory(prefix="medisur-report-") as temporary:
        png = Path(temporary) / "architecture.png"
        output = Path(temporary) / "reporte.docx"
        render_diagram(diagram, png)
        counts = create_document(markdown, png, output, created, evidence)
        embedded = validate_docx(output, markdown, counts, png, evidence)
        if embedded != png.read_bytes():
            raise ValueError("Embedded image differs from generated PNG")
        with Image.open(png) as image:
            image.verify()
        if source.read_bytes() != source_bytes or diagram.read_bytes() != diagram_bytes:
            raise RuntimeError("Input changed during rendering; rerun with stable sources.")
        if any(item["path"].read_bytes() != item["data"]
               or item["path"].stat().st_mtime_ns != item["modified"] for item in evidence):
            raise RuntimeError("Evidence changed during rendering; rerun with stable sources.")
        for artifact in (png, output):
            (DOCS / artifact.name).write_bytes(artifact.read_bytes())
    print(f"Created {DOCS / 'reporte.docx'}")
    print(f"Created {DOCS / 'architecture.png'}")
    image_label = "image" if counts["images"] == 1 else "images"
    print(f"Validated: {counts['tables']} tables, {counts['images']} embedded {image_label}, {counts['headings']} headings.")
    print(f"Images: {counts['architecture']} architecture diagram; {counts['evidence']} evidence PNGs in section 9.")
    print("Source text preserved; DOCX ZIP/XML, image bytes, draft header and page fields checked.")
    print("Credential-pattern scan passed; it is not a guarantee of exhaustive secret detection.")
    print(f"Source SHA256 unchanged: {hashlib.sha256(source_bytes).hexdigest()}")
    print(f"Actual generation time: {created.isoformat()}")
    print("Pagination requires Word layout; no final page count or completed deployment is asserted.")


if __name__ == "__main__":
    main()