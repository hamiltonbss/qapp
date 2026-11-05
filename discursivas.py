"""Módulo de questões discursivas: importação CSV, marcação de treino, resposta e anotações por tema."""
import csv
import io
from datetime import datetime, timezone

import streamlit as st
from bson import ObjectId
from pymongo import ASCENDING

COLUNAS = ["disciplina", "tema", "enunciado"]
MODELO = ("disciplina;tema;enunciado\n"
          "Direito Administrativo;Controle da administração;Discorra sobre o controle interno e externo da "
          "administração pública, abordando: a) conceito; b) órgãos responsáveis; c) exemplos.\n")


def _agora():
    return datetime.now(timezone.utc).isoformat()


def init_indexes(db):
    db.disc_temas.create_index([("usuario_login", ASCENDING), ("disciplina", ASCENDING), ("tema", ASCENDING)])


def interpretar_csv(txt):
    """Retorna (linhas válidas, erros). Aceita ; ou , como separador."""
    txt = txt.lstrip("﻿")
    cab1 = txt.split("\n", 1)[0]
    sep = ";" if cab1.count(";") >= cab1.count(",") else ","
    leitor = csv.DictReader(io.StringIO(txt), delimiter=sep)
    cab = [(c or "").strip().lower() for c in leitor.fieldnames or []]
    faltam = [c for c in ("tema", "enunciado") if c not in cab]
    if faltam:
        return [], [f"Colunas obrigatórias ausentes: {', '.join(faltam)}"]
    linhas, erros = [], []
    for n, row in enumerate(leitor, start=2):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if k}
        if not r.get("tema") or not r.get("enunciado"):
            erros.append(f"Linha {n}: tema e enunciado são obrigatórios.")
            continue
        linhas.append({c: r.get(c, "") for c in COLUNAS})
    return linhas, erros


def importar(db, login, linhas):
    novos = dup = 0
    for l in linhas:
        filtro = {"usuario_login": login, "tema": l["tema"], "enunciado": l["enunciado"]}
        if db.disc_temas.find_one(filtro, {"_id": 1}):
            dup += 1
            continue
        db.disc_temas.insert_one({**filtro, "disciplina": l["disciplina"] or "Geral", "treinado": False,
                                  "resposta": "", "anotacoes": "", "criado_em": _agora()})
        novos += 1
    return novos, dup


def _aba_temas(db, login):
    temas = list(db.disc_temas.find({"usuario_login": login}).sort([("disciplina", 1), ("tema", 1)]))
    if not temas:
        st.info("Nenhum tema cadastrado. Use a aba **Importar**.")
        return
    feitos = sum(1 for t in temas if t.get("treinado"))
    st.progress(feitos / len(temas), text=f"{feitos} de {len(temas)} temas treinados")

    c1, c2 = st.columns(2)
    discs = sorted({t["disciplina"] for t in temas})
    disc = c1.selectbox("Disciplina", ["Todas"] + discs)
    sit = c2.radio("Situação", ["Todos", "Não treinados", "Treinados"], horizontal=True)
    for t in temas:
        if disc != "Todas" and t["disciplina"] != disc:
            continue
        if sit != "Todos" and t.get("treinado") != (sit == "Treinados"):
            continue
        tid = str(t["_id"])
        with st.expander(("✅ " if t.get("treinado") else "⬜ ") + f"{t['disciplina']} · {t['tema']}"):
            st.markdown(t["enunciado"])
            resp = st.text_area("Minha resposta", t.get("resposta", ""), height=250, key=f"dr_{tid}")
            st.caption(f"{len(resp)} caracteres · {len(resp.split())} palavras")
            notas = st.text_area("Anotações", t.get("anotacoes", ""), height=120, key=f"dn_{tid}")
            trein = st.checkbox("Treinado", t.get("treinado", False), key=f"dt_{tid}")
            b1, b2 = st.columns(2)
            if b1.button("💾 Salvar", key=f"ds_{tid}", type="primary"):
                db.disc_temas.update_one({"_id": ObjectId(tid), "usuario_login": login},
                                         {"$set": {"resposta": resp, "anotacoes": notas, "treinado": trein,
                                                   "atualizado_em": _agora()}})
                st.toast("Salvo.")
                st.rerun()
            if b2.button("🗑️ Excluir tema", key=f"dx_{tid}"):
                db.disc_temas.delete_one({"_id": ObjectId(tid), "usuario_login": login})
                st.rerun()


def _aba_importar(db, login):
    st.caption("Colunas: **disciplina;tema;enunciado** (separador ; ou ,). Temas repetidos são ignorados.")
    st.download_button("⬇️ Baixar modelo", MODELO.encode("utf-8-sig"), "modelo_discursivas.csv", "text/csv")
    arq = st.file_uploader("Arquivo CSV", type=["csv"], key="disc_up")
    txt = arq.getvalue().decode("utf-8-sig", errors="replace") if arq else st.text_area("…ou cole o CSV", height=150)
    if not txt.strip():
        return
    linhas, erros = interpretar_csv(txt)
    for e in erros[:10]:
        st.warning(e)
    if linhas:
        st.dataframe(linhas, width="stretch", hide_index=True)
        if st.button(f"📥 Importar {len(linhas)} tema(s)", type="primary"):
            novos, dup = importar(db, login, linhas)
            st.success(f"{novos} importado(s), {dup} já existiam.")


def render_page(db, login):
    st.title("✍️ Discursivas")
    aba1, aba2 = st.tabs(["Temas", "Importar"])
    with aba1:
        _aba_temas(db, login)
    with aba2:
        _aba_importar(db, login)
