"""PowerPoint presentation generator from scenario text."""

import io
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

# Цвета бренда
CLR_BG      = RGBColor(0x0D, 0x0D, 0x1A)   # тёмно-синий фон
CLR_ACCENT  = RGBColor(0xE8, 0xD5, 0xB7)   # тёплый кремовый акцент
CLR_WHITE   = RGBColor(0xFF, 0xFF, 0xFF)
CLR_GRAY    = RGBColor(0xAA, 0xAA, 0xBB)


def _set_bg(slide, color: RGBColor) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def _add_textbox(slide, text: str, left, top, width, height,
                 font_size: int, color: RGBColor,
                 bold: bool = False, align=PP_ALIGN.LEFT) -> None:
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(font_size)
    run.font.color.rgb = color
    run.font.bold = bold
    run.font.name = "Arial"


def _parse_scenario(text: str) -> list[dict]:
    """
    Формат:
        Заголовок презентации
        ---
        Заголовок слайда
        - пункт 1
        - пункт 2
        ---
        Следующий слайд
        текст без тире тоже работает как пункт
    """
    blocks = [b.strip() for b in text.strip().split("---")]
    slides = []

    for i, block in enumerate(blocks):
        if not block:
            continue
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue

        title = lines[0]
        bullets = []
        for line in lines[1:]:
            # убрать ведущие - • *
            clean = line.lstrip("-•* ").strip()
            if clean:
                bullets.append(clean)

        slides.append({"title": title, "bullets": bullets, "is_title_slide": i == 0})

    return slides


def generate_pptx(scenario_text: str, author: str = "@belevtsow") -> bytes:
    """Генерирует PPTX из текста сценария, возвращает bytes."""
    prs = Presentation()
    prs.slide_width  = Inches(13.33)
    prs.slide_height = Inches(7.5)

    W = prs.slide_width
    H = prs.slide_height
    PAD = Inches(0.8)

    slides_data = _parse_scenario(scenario_text)
    blank_layout = prs.slide_layouts[6]  # пустой

    for i, data in enumerate(slides_data):
        slide = prs.slides.add_slide(blank_layout)
        _set_bg(slide, CLR_BG)

        if data["is_title_slide"]:
            # ── Титульный слайд ──────────────────────────────────
            # Акцентная линия сверху
            line = slide.shapes.add_shape(
                1,  # MSO_SHAPE_TYPE.RECTANGLE
                PAD, Inches(0.35),
                W - PAD * 2, Inches(0.06)
            )
            line.fill.solid()
            line.fill.fore_color.rgb = CLR_ACCENT
            line.line.fill.background()

            # Заголовок
            _add_textbox(slide, data["title"],
                         PAD, Inches(2.5), W - PAD * 2, Inches(2.5),
                         font_size=52, color=CLR_WHITE, bold=True,
                         align=PP_ALIGN.CENTER)

            # Подзаголовок из первого пункта (если есть)
            if data["bullets"]:
                _add_textbox(slide, data["bullets"][0],
                             PAD, Inches(5.0), W - PAD * 2, Inches(1.0),
                             font_size=22, color=CLR_ACCENT,
                             align=PP_ALIGN.CENTER)

            # Автор внизу
            _add_textbox(slide, author,
                         PAD, Inches(6.6), W - PAD * 2, Inches(0.5),
                         font_size=14, color=CLR_GRAY,
                         align=PP_ALIGN.CENTER)

        else:
            # ── Контентный слайд ─────────────────────────────────
            # Номер слайда (маленький, правый угол)
            _add_textbox(slide, str(i),
                         W - Inches(1.2), Inches(0.2), Inches(0.8), Inches(0.4),
                         font_size=12, color=CLR_GRAY, align=PP_ALIGN.RIGHT)

            # Акцентная линия слева
            bar = slide.shapes.add_shape(
                1,
                PAD, Inches(0.9),
                Inches(0.06), Inches(0.75)
            )
            bar.fill.solid()
            bar.fill.fore_color.rgb = CLR_ACCENT
            bar.line.fill.background()

            # Заголовок слайда
            _add_textbox(slide, data["title"],
                         PAD + Inches(0.25), Inches(0.85),
                         W - PAD * 2, Inches(0.9),
                         font_size=32, color=CLR_WHITE, bold=True)

            # Разделитель
            sep = slide.shapes.add_shape(
                1,
                PAD, Inches(1.75),
                W - PAD * 2, Inches(0.02)
            )
            sep.fill.solid()
            sep.fill.fore_color.rgb = RGBColor(0x33, 0x33, 0x55)
            sep.line.fill.background()

            # Буллеты
            if data["bullets"]:
                txBox = slide.shapes.add_textbox(
                    PAD, Inches(1.9),
                    W - PAD * 2, Inches(5.0)
                )
                tf = txBox.text_frame
                tf.word_wrap = True

                for j, bullet in enumerate(data["bullets"]):
                    if j == 0:
                        p = tf.paragraphs[0]
                    else:
                        p = tf.add_paragraph()

                    p.space_before = Pt(6)
                    run = p.add_run()
                    run.text = f"  ·  {bullet}"
                    run.font.size = Pt(22)
                    run.font.color.rgb = CLR_WHITE
                    run.font.name = "Arial"

    # Сохранить в bytes
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
