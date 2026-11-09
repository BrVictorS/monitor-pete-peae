# -*- coding: utf-8 -*-
"""
Gera as imagens de demonstração do README a partir dos DADOS FICTÍCIOS
(exemplos/seed_exemplo.csv) — nunca dos dados reais.

Saídas em docs/:
  • dashboard_pete.png  — 1ª página do PDF da grade (renderizado via PyMuPDF)
  • whatsapp.png        — prévia da mensagem de WhatsApp (Pillow)

Uso:  python exemplos/gerar_imagens_demo.py
      (requer DATABASE_URL; usa um esquema temporário `demo_imagens`, apagado no fim)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # permite importar store/core

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont

from store.db import Razao, data_br
from store.seed import importar_seed
from core.razao import grade_consolidada
from core.pdf import gerar_pdf
from core.whatsapp import gerar_texto

BASE = Path(__file__).resolve().parent.parent
DOCS = BASE / "docs"
DOCS.mkdir(exist_ok=True)
SEED = BASE / "exemplos" / "seed_exemplo.csv"


def _fonte(sz, bold=False):
    nomes = (["arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold
             else ["arial.ttf", "DejaVuSans.ttf"])
    for n in nomes:
        try:
            return ImageFont.truetype(n, sz)
        except Exception:
            pass
    return ImageFont.load_default()


def dashboard_png(razao):
    grade = grade_consolidada(razao.listar("PETE"))
    pdf = gerar_pdf(grade, "PETE")
    doc = fitz.open(stream=pdf, filetype="pdf")
    pix = doc[0].get_pixmap(dpi=130)
    pix.save(str(DOCS / "dashboard_pete.png"))
    doc.close()
    print("ok docs/dashboard_pete.png")


def dashboard_app_png(razao):
    """Painel estilo dashboard: KPIs + gráfico 'total por parcela' (PETE)."""
    from core.pdf import brl
    grade = grade_consolidada(razao.listar("PETE"))
    cols = grade["colunas"]
    parcs = [k[1] for (k, _r) in cols if isinstance(k, tuple) and k[0] == "P"]
    totais = [(rot, sum(l["valores"].get(key, 0.0) for l in grade["linhas"]))
              for (key, rot) in cols]

    AZUL, ESCURO, BORDA, TXT = (0, 113, 206), (0, 75, 140), (221, 226, 233), (22, 28, 36)
    W, H = 980, 660
    img = Image.new("RGB", (W, H), (244, 246, 248))
    d = ImageDraw.Draw(img)

    d.text((34, 26), "Dashboard — PETE (transporte escolar)",
           font=_fonte(24, True), fill=AZUL)

    # KPIs
    kpis = [("TOTAL PAGO", brl(grade["total_geral"])),
            ("MUNICÍPIOS ATENDIDOS", str(len(grade["linhas"]))),
            ("PARCELA MAIS RECENTE", f"{max(parcs)}ª" if parcs else "—")]
    cw, gap, x0, y0 = 296, 18, 34, 78
    for i, (lab, val) in enumerate(kpis):
        x = x0 + i * (cw + gap)
        d.rounded_rectangle([x, y0, x + cw, y0 + 96], radius=12, fill="white", outline=BORDA)
        d.text((x + 18, y0 + 16), lab, font=_fonte(13, True), fill=(107, 118, 134))
        d.text((x + 18, y0 + 42), val, font=_fonte(28, True), fill=TXT)

    # gráfico de barras
    gx0, gy0, gx1, gy1 = 34, 224, W - 34, H - 40
    d.rounded_rectangle([gx0, gy0, gx1, gy1], radius=12, fill="white", outline=BORDA)
    d.text((gx0 + 20, gy0 + 16), "Total por parcela", font=_fonte(15, True), fill=(91, 100, 112))
    base_y = gy1 - 56
    top_y = gy0 + 60
    maxv = max((v for _, v in totais), default=1) or 1
    n = len(totais)
    area_w = (gx1 - gx0) - 60
    bw = min(90, area_w / max(n, 1) * 0.6)
    step = area_w / max(n, 1)
    for i, (rot, v) in enumerate(totais):
        cx = gx0 + 30 + step * i + step / 2
        h = (v / maxv) * (base_y - top_y)
        d.rectangle([cx - bw / 2, base_y - h, cx + bw / 2, base_y], fill=AZUL)
        d.text((cx, base_y + 10), rot, font=_fonte(12, True), fill=TXT, anchor="ma")
        d.text((cx, base_y - h - 18), brl(v).replace("R$ ", ""),
               font=_fonte(11), fill=ESCURO, anchor="ma")
    img.save(DOCS / "dashboard_app.png")
    print("ok docs/dashboard_app.png")


def whatsapp_png(razao):
    # escolhe uma data com lançamentos e monta o texto
    datas = razao.datas_disponiveis()
    d = datas[len(datas) // 2] if datas else datas[0]
    texto = gerar_texto(razao.do_dia(d), data_br(d))
    linhas = texto.splitlines()
    if len(linhas) > 16:
        linhas = linhas[:16] + ["…"]

    W = 720
    pad, lh = 26, 30
    H = 150 + lh * len(linhas) + 40
    img = Image.new("RGB", (W, H), (229, 221, 213))  # bege do WhatsApp
    d_ = ImageDraw.Draw(img)
    # barra superior verde
    d_.rectangle([0, 0, W, 64], fill=(7, 94, 84))
    d_.ellipse([16, 12, 56, 52], fill=(37, 211, 102))
    d_.text((70, 22), "Secretária  ·  Relatório do dia", font=_fonte(18, True), fill="white")
    # balão da mensagem
    bx0, by0, bx1, by1 = 24, 88, W - 24, H - 24
    d_.rounded_rectangle([bx0, by0, bx1, by1], radius=16, fill=(220, 248, 198))
    f = _fonte(16)
    fb = _fonte(16, True)
    y = by0 + 16
    for ln in linhas:
        bold = ln.startswith("📌") or "*" in ln
        # remove emojis (a fonte não os renderiza) só para a imagem de preview
        txt = ln
        for e in ("📌", "🚌", "🍎"):
            txt = txt.replace(e, "")
        txt = txt.replace("*", "").strip()
        d_.text((bx0 + 18, y), txt, font=(fb if bold else f), fill=(20, 30, 24))
        y += lh
    img.save(DOCS / "whatsapp.png")
    print("ok docs/whatsapp.png")


def main():
    # esquema temporário no PostgreSQL: não encosta no razão real (projetos.lancamentos)
    razao = Razao(schema="demo_imagens")
    try:
        importar_seed(str(SEED), razao, substituir=True)
        dashboard_png(razao)
        dashboard_app_png(razao)
        whatsapp_png(razao)
    finally:
        razao.descartar()


if __name__ == "__main__":
    main()
