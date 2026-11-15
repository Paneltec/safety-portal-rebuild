"""PDF reports. Built from the saved record and its schema snapshot, never from a live form.

Branding (name, logo, address, contact, portal address) comes from organisation
settings. Nothing about the hosting platform is ever printed.
"""
from datetime import datetime
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import HRFlowable, Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

INK = colors.HexColor("#16222E")
MUTED = colors.HexColor("#4A5967")
LINE = colors.HexColor("#D5DCE3")
BAD = colors.HexColor("#9F1D12")
OK = colors.HexColor("#0B5D4A")

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=13.5, textColor=INK)
LABEL = ParagraphStyle("label", parent=BODY, fontName="Helvetica-Bold")
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8.5, leading=11, textColor=MUTED)
TITLE = ParagraphStyle("title", parent=BODY, fontName="Helvetica-Bold", fontSize=18, leading=22)
SECTION = ParagraphStyle("section", parent=BODY, fontName="Helvetica-Bold", fontSize=12.5, leading=16, spaceBefore=8)


def _p(text: Any, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)


def _when(value: datetime | None, tz: str) -> str:
    if value is None:
        return "—"
    local = value.astimezone(ZoneInfo(tz))
    return local.strftime("%d/%m/%Y ") + local.strftime("%I:%M %p").lstrip("0").lower()


def _date(value: str) -> str:
    parts = value.split("-")
    return "/".join(reversed(parts)) if len(parts) == 3 else value


def _image(data: bytes, max_w: float, max_h: float) -> Image | None:
    """Scale to fit while keeping the picture's own proportions."""
    try:
        img = Image(BytesIO(data))
        w, h = img.imageWidth, img.imageHeight
        if not w or not h:
            return None
        scale = min(max_w / w, max_h / h, 1.0)
        img.drawWidth, img.drawHeight = w * scale, h * scale
        img.hAlign = "LEFT"
        return img
    except Exception:  # noqa: BLE001 - an unreadable picture is reported in the PDF, not fatal
        return None


class _NumberedCanvas(canvas.Canvas):
    """Adds "Page X of Y" and the footer line once the page count is known."""

    def __init__(self, *args: Any, footer: str = "", **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._saved: list[dict[str, Any]] = []
        self._footer = footer

    def showPage(self) -> None:  # noqa: N802 - reportlab API
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self.setFont("Helvetica", 8)
            self.setFillColor(MUTED)
            self.setStrokeColor(LINE)
            self.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
            self.drawString(18 * mm, 9.5 * mm, self._footer[:140])
            self.drawRightString(A4[0] - 18 * mm, 9.5 * mm, f"Page {self._pageNumber} of {total}")
            super().showPage()
        super().save()


def _answer(field: dict[str, Any], value: Any, files: dict[str, dict[str, Any]], width: float) -> list[Any]:
    """Flowables showing one answer. `files` maps file id -> {name, data (bytes or None)}."""
    ftype = field["type"]
    if value in (None, "", [], {}):
        return [_p("Not answered", SMALL)]
    if ftype == "compliance":
        result = {"pass": ("PASS", OK), "fail": ("FAIL", BAD), "na": ("N/A", MUTED)}[value["result"]]
        style = ParagraphStyle("res", parent=LABEL, textColor=result[1])
        out: list[Any] = [Paragraph(result[0], style)]
        if value.get("note"):
            out.append(_p(value["note"]))
        return out
    if ftype in ("photo", "attachment"):
        out = []
        for file_id in value:
            info = files.get(file_id)
            if info is None or info["data"] is None:
                out.append(_p(f"{info['name'] if info else 'File'}: content missing from storage", SMALL))
                continue
            picture = _image(info["data"], width, 85 * mm) if info["mime"].startswith("image/") else None
            if picture is not None:
                out += [picture, _p(info["name"], SMALL)]
            else:
                out.append(_p(f"Attached file: {info['name']}"))
        return out
    if ftype == "signature":
        info = files.get(value["file_id"])
        out = []
        picture = _image(info["data"], 70 * mm, 28 * mm) if info and info["data"] else None
        out.append(picture if picture is not None else _p("Signature image missing from storage", SMALL))
        out.append(_p(f"Signed by {value['signed_name']}"))
        return out
    if ftype == "date":
        return [_p(_date(value))]
    if ftype == "select" and isinstance(value, list):
        return [_p(", ".join(value))]
    if ftype == "reference_matrix":
        rows = field.get("config", {}).get("rows", [])
        return [_p("\n".join(f"{r['label']}: {value[r['id']]}" for r in rows if r["id"] in value) or "—")]
    if ftype == "gps":
        if "lat" in value:
            accuracy = f" (within {round(value['accuracy_m'])} m)" if "accuracy_m" in value else ""
            return [_p(f"{value['lat']:.5f}, {value['lng']:.5f}{accuracy}")]
        return [_p(f"Location not available: {value['unavailable_reason']}")]
    if ftype == "navixy_vehicle":
        return [_p(f"{value['label']} ({'from Navixy' if value.get('source') == 'navixy' else 'entered by hand'})")]
    if ftype == "worker_picker":
        return [_p(", ".join(v["label"] for v in (value if isinstance(value, list) else [value])))]
    if ftype in ("job_picker", "site_picker", "asset_qr"):
        return [_p(value["label"])]
    if ftype == "corrective_actions":
        return [
            _p(f"{i}. {a['description']} — {a['responsible']}"
               f"{' — due ' + _date(a['due_date']) if a.get('due_date') else ''} — {'Done' if a['status'] == 'done' else 'Open'}")
            for i, a in enumerate(value, start=1)
        ]
    unit = field.get("config", {}).get("unit")
    return [_p(f"{value} {unit}" if unit and ftype == "number" else value)]


def submission_pdf(
    *,
    org: dict[str, Any],
    logo: bytes | None,
    title: str,
    meta: list[tuple[str, str]],
    schema: list[dict[str, Any]],
    values: dict[str, Any],
    files: dict[str, dict[str, Any]],
    timezone: str,
) -> bytes:
    """Render one saved submission. `meta` is a list of (label, text) shown under the title."""
    buffer = BytesIO()
    width = A4[0] - 36 * mm
    printed = _when(datetime.now(ZoneInfo(timezone)), timezone)
    footer = " · ".join(x for x in [org.get("name"), org.get("public_portal_url"), f"Printed {printed}"] if x)
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=20 * mm, title=title, author=org.get("name") or "")
    story: list[Any] = []

    # Header: logo at its own proportions beside the organisation's details.
    address = ", ".join(x for x in [org.get("address_line1"), org.get("address_line2"), org.get("suburb"),
                                    org.get("state"), org.get("postcode")] if x)
    contact = " · ".join(x for x in [org.get("contact_phone"), org.get("contact_email"), org.get("website_url")] if x)
    details = [_p(org.get("trading_name") or org.get("name") or "", LABEL)]
    if org.get("abn"):
        details.append(_p(f"ABN {org['abn']}", SMALL))
    if address:
        details.append(_p(address, SMALL))
    if contact:
        details.append(_p(contact, SMALL))
    picture = _image(logo, 42 * mm, 20 * mm) if logo else None
    header = Table([[picture or "", details]], colWidths=[46 * mm if picture else 0.1 * mm, width - (46 * mm if picture else 0.1 * mm)])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("LINEBELOW", (0, 0), (-1, -1), 1, INK), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story += [header, Spacer(1, 10), _p(title, TITLE), Spacer(1, 4)]
    meta_table = Table([[_p(k, SMALL), _p(v)] for k, v in meta], colWidths=[38 * mm, width - 38 * mm])
    meta_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                    ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]))
    story += [meta_table, Spacer(1, 8)]

    for field in schema:
        if field["type"] == "section_header":
            story.append(_p(field["label"], SECTION))
            continue
        block = [_p(field["label"], LABEL), *_answer(field, values.get(field["id"]), files, width - 4 * mm)]
        rule = HRFlowable(width="100%", thickness=0.5, color=LINE, spaceBefore=5, spaceAfter=5)
        if field["type"] in ("photo", "attachment", "textarea", "corrective_actions"):
            # Long answers flow on to the next page; the label stays with the first part.
            story += [KeepTogether(block[:2]), *block[2:], rule]
        else:
            story.append(KeepTogether([*block, rule]))

    doc.build(story, canvasmaker=lambda *a, **k: _NumberedCanvas(*a, footer=footer, **k))
    return buffer.getvalue()
