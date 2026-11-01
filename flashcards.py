"""Módulo de Flashcards — baralhos próprios, revisão espaçada (Leitner) e importação.

Independente do Plano de Estudos: usa as coleções `fc_baralhos` e `fc_cartoes`.
Uso no app principal:
    import flashcards
    flashcards.init_indexes(db)          # uma vez por processo
    flashcards.render_page(db, login)    # na navegação
"""
import csv
import html
import io
import random
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from bson import ObjectId
from pymongo import ASCENDING

try:
    from zoneinfo import ZoneInfo
    FUSO = ZoneInfo("America/Sao_Paulo")
except Exception:
    FUSO = timezone.utc

# Caixas de Leitner: caixa -> dias até a próxima revisão
INTERVALOS = {1: 1, 2: 3, 3: 7, 4: 15, 5: 30}
MAX_CAIXA = max(INTERVALOS)
DELIMITADORES = {"\t": "Tabulação", ";": "Ponto e vírgula (;)", "|": "Barra vertical (|)", ",": "Vírgula (,)"}

MODELO_IMPORTACAO = """frente;verso;baralho
O que é habeas corpus?;Remédio constitucional que protege a liberdade de locomoção.;Constitucional
Prazo da apelação no CPC?;15 dias úteis (art. 1.003, §5º).;Processo Civil
Linha com quebra\\nna frente;Use \\n para pular linha;Constitucional"""


# =============================
# Utilitários
# =============================
def _esc(s):
    return html.escape(str(s or ""), quote=True)


def _oid(x):
    try:
        return x if isinstance(x, ObjectId) else ObjectId(str(x))
    except Exception:
        return None


def _agora():
    return datetime.now(timezone.utc).isoformat()


def _hoje():
    return datetime.now(FUSO).date()


def _hoje_str():
    return _hoje().isoformat()


def _fmt_data(s):
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return "—"


def init_indexes(db):
    for fn in (
        lambda: db.fc_baralhos.create_index([("usuario_login", ASCENDING), ("nome", ASCENDING)],
                                            name="uq_fc_baralho", unique=True),
        lambda: db.fc_cartoes.create_index([("usuario_login", ASCENDING), ("baralho_id", ASCENDING),
                                            ("proxima_revisao", ASCENDING)], name="idx_fc_fila"),
    ):
        try:
            fn()
        except Exception as e:
            print(f"[flashcards] {e}")


# =============================
# Baralhos e cartões (banco)
# =============================
def listar_baralhos(db, login):
    """Baralhos com contagem total e de cartões a revisar hoje (uma agregação)."""
    hoje = _hoje_str()
    cont = {}
    pipe = [
        {"$match": {"usuario_login": login}},
        {"$group": {"_id": "$baralho_id", "total": {"$sum": 1},
                    "hoje": {"$sum": {"$cond": [{"$lte": ["$proxima_revisao", hoje]}, 1, 0]}}}},
    ]
    for d in db.fc_cartoes.aggregate(pipe):
        cont[str(d["_id"])] = (d["total"], d["hoje"])
    out = []
    for b in db.fc_baralhos.find({"usuario_login": login}).sort("nome", ASCENDING):
        total, a_revisar = cont.get(str(b["_id"]), (0, 0))
        out.append({"id": str(b["_id"]), "nome": b.get("nome", ""), "descricao": b.get("descricao", ""),
                    "total": total, "hoje": a_revisar})
    return out


def obter_ou_criar_baralho(db, login, nome):
    nome = (nome or "").strip() or "Geral"
    b = db.fc_baralhos.find_one({"usuario_login": login, "nome": nome})
    if b:
        return str(b["_id"]), False
    res = db.fc_baralhos.insert_one({"usuario_login": login, "nome": nome, "descricao": "",
                                     "criado_em": _agora()})
    return str(res.inserted_id), True


def renomear_baralho(db, login, baralho_id, novo_nome):
    novo_nome = (novo_nome or "").strip()
    if not novo_nome:
        return False
    if db.fc_baralhos.find_one({"usuario_login": login, "nome": novo_nome, "_id": {"$ne": _oid(baralho_id)}}):
        return False
    db.fc_baralhos.update_one({"_id": _oid(baralho_id), "usuario_login": login}, {"$set": {"nome": novo_nome}})
    return True


def excluir_baralho(db, login, baralho_id):
    oid = _oid(baralho_id)
    db.fc_cartoes.delete_many({"usuario_login": login, "baralho_id": oid})
    db.fc_baralhos.delete_one({"_id": oid, "usuario_login": login})


def _novo_cartao(login, baralho_oid, frente, verso):
    return {"usuario_login": login, "baralho_id": baralho_oid,
            "frente": frente.strip(), "verso": verso.strip(),
            "caixa": 1, "proxima_revisao": _hoje_str(), "ultima_revisao": None,
            "revisoes": 0, "acertos": 0, "erros": 0, "criado_em": _agora()}


def listar_cartoes(db, login, baralho_ids=None):
    filtro = {"usuario_login": login}
    if baralho_ids is not None:
        filtro["baralho_id"] = {"$in": [o for o in (_oid(b) for b in baralho_ids) if o]}
    return list(db.fc_cartoes.find(filtro).sort("criado_em", ASCENDING))


def adicionar_cartoes(db, login, baralho_id, pares):
    """Insere pares (frente, verso) ignorando frentes já existentes no baralho.
    Retorna (inseridos, repetidos)."""
    boid = _oid(baralho_id)
    existentes = set(db.fc_cartoes.distinct("frente", {"usuario_login": login, "baralho_id": boid}))
    docs, repetidos = [], 0
    for frente, verso in pares:
        frente, verso = (frente or "").strip(), (verso or "").strip()
        if not frente or not verso:
            continue
        if frente in existentes:
            repetidos += 1
            continue
        existentes.add(frente)
        docs.append(_novo_cartao(login, boid, frente, verso))
    if docs:
        db.fc_cartoes.insert_many(docs, ordered=False)
    return len(docs), repetidos


def registrar_revisao(db, login, cartao_id, nota):
    """nota: 'errei' | 'acertei' | 'facil' — sistema de Leitner."""
    c = db.fc_cartoes.find_one({"_id": _oid(cartao_id), "usuario_login": login})
    if not c:
        return None
    caixa = int(c.get("caixa") or 1)
    if nota == "errei":
        caixa, dias = 1, 0  # volta para o início e reaparece ainda hoje
    else:
        caixa = min(MAX_CAIXA, caixa + (2 if nota == "facil" else 1))
        dias = INTERVALOS[caixa]
    proxima = (_hoje() + timedelta(days=dias)).isoformat()
    db.fc_cartoes.update_one({"_id": c["_id"]}, {
        "$set": {"caixa": caixa, "proxima_revisao": proxima, "ultima_revisao": _agora()},
        "$inc": {"revisoes": 1, "acertos": 0 if nota == "errei" else 1, "erros": 1 if nota == "errei" else 0},
    })
    return proxima


# =============================
# Importação
# =============================
def _decodificar(conteudo):
    if isinstance(conteudo, bytes):
        try:
            return conteudo.decode("utf-8-sig")
        except UnicodeDecodeError:
            return conteudo.decode("latin-1")
    return str(conteudo or "").lstrip("﻿")


def detectar_delimitador(txt):
    """Escolhe o delimitador presente de forma consistente nas primeiras linhas."""
    linhas = [l for l in txt.splitlines()[:20] if l.strip()]
    if not linhas:
        return ";"
    for d in DELIMITADORES:  # ordem de preferência
        if all(l.count(d) >= 1 for l in linhas):
            return d
    return max(DELIMITADORES, key=lambda d: sum(l.count(d) for l in linhas))


def interpretar_importacao(txt, delimitador=None):
    """Lê frente/verso/baralho de CSV, TSV (export do Anki) ou texto colado.
    Cabeçalho opcional: se a 1ª linha tiver 'frente' e 'verso', as colunas são lidas pelo nome;
    senão, 1ª coluna = frente, 2ª = verso, 3ª (opcional) = baralho.
    Retorna (linhas, erros) com linhas = [{"frente", "verso", "baralho"}]."""
    txt = _decodificar(txt)
    delim = delimitador or detectar_delimitador(txt)
    rows = list(csv.reader(io.StringIO(txt), delimiter=delim))
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return [], ["Nenhuma linha encontrada."]

    cab = [(c or "").strip().lower() for c in rows[0]]
    if "frente" in cab and "verso" in cab:
        i_f, i_v = cab.index("frente"), cab.index("verso")
        i_b = cab.index("baralho") if "baralho" in cab else None
        dados, inicio = rows[1:], 2
    else:
        i_f, i_v, i_b = 0, 1, 2
        dados, inicio = rows, 1

    out, erros = [], []
    for n, r in enumerate(dados, start=inicio):
        if len(r) <= max(i_f, i_v):
            erros.append(f"Linha {n}: esperava frente e verso separados por '{DELIMITADORES.get(delim, delim)}'.")
            continue
        frente = r[i_f].strip().replace("\\n", "\n")
        verso = r[i_v].strip().replace("\\n", "\n")
        if not frente or not verso:
            erros.append(f"Linha {n}: frente ou verso vazio.")
            continue
        baralho = r[i_b].strip() if (i_b is not None and len(r) > i_b) else ""
        out.append({"frente": frente, "verso": verso, "baralho": baralho})
    return out, erros


def importar_linhas(db, login, linhas, baralho_destino=None):
    """baralho_destino=None usa a coluna 'baralho' (vazia -> 'Geral').
    Retorna {nome_baralho: (inseridos, repetidos, criado)}."""
    grupos = {}
    for l in linhas:
        nome = (baralho_destino or l.get("baralho") or "Geral").strip()
        grupos.setdefault(nome, []).append((l["frente"], l["verso"]))
    resumo = {}
    for nome, pares in grupos.items():
        bid, criado = obter_ou_criar_baralho(db, login, nome)
        ins, rep = adicionar_cartoes(db, login, bid, pares)
        resumo[nome] = (ins, rep, criado)
    return resumo


def cartoes_do_plano(db, login):
    """Flashcards do Plano de Estudos do usuário: {nome_do_plano: [(frente, verso), ...]}."""
    planos = {p["_id"]: p.get("nome", "Plano") for p in db.est_planos.find({"usuario_login": login}, {"nome": 1})}
    if not planos:
        return {}
    itens = {}
    for it in db.est_planejamento.find({"plano_id": {"$in": list(planos)}}, {"plano_id": 1}):
        itens[str(it["_id"])] = planos.get(it["plano_id"], "Plano")
    if not itens:
        return {}
    chaves = list(itens) + [ObjectId(i) for i in itens]
    out = {}
    for c in db.flashcards.find({"item_id": {"$in": chaves}}, {"item_id": 1, "frente": 1, "verso": 1}):
        nome = itens.get(str(c.get("item_id")))
        if nome and c.get("frente") and c.get("verso"):
            out.setdefault(nome, []).append((c["frente"], c["verso"]))
    return out


# =============================
# Interface
# =============================
def _css():
    st.markdown("""<style>
    .fc2-card{border-radius:14px;padding:28px 24px;min-height:170px;display:flex;align-items:center;
      justify-content:center;text-align:center;font-size:18px;line-height:1.6;white-space:pre-wrap;
      margin:6px 0 12px 0;color:#1a1a1a}
    .fc2-frente{background:#f0f9ff;border:2px solid #19747E}
    .fc2-verso{background:#f6fbf7;border:2px solid #28a745}
    .fc2-meta{font-size:12px;color:#888;margin-bottom:2px}
    </style>""", unsafe_allow_html=True)


def _confirmar(label, key, mensagem):
    flag = f"_fc_conf_{key}"
    if st.session_state.get(flag):
        st.warning(mensagem)
        c1, c2 = st.columns(2)
        if c1.button("Sim, confirmar", key=f"{key}_s", type="primary"):
            st.session_state.pop(flag, None)
            return True
        if c2.button("Cancelar", key=f"{key}_n"):
            st.session_state.pop(flag, None)
            st.rerun()
        return False
    if st.button(label, key=key):
        st.session_state[flag] = True
        st.rerun()
    return False


def render_page(db, login):
    _css()
    st.title("🗂️ Flashcards")
    msg = st.session_state.pop("_fc_msg", None)
    if msg:
        st.success(msg)

    baralhos = listar_baralhos(db, login)
    a_revisar = sum(b["hoje"] for b in baralhos)
    c1, c2, c3 = st.columns(3)
    c1.metric("Baralhos", len(baralhos))
    c2.metric("Cartões", sum(b["total"] for b in baralhos))
    c3.metric("Para revisar hoje", a_revisar)

    t_estudar, t_baralhos, t_importar = st.tabs(["📖 Estudar", "🗃️ Baralhos e cartões", "📥 Importar"])
    with t_estudar:
        _aba_estudar(db, login, baralhos)
    with t_baralhos:
        _aba_baralhos(db, login, baralhos)
    with t_importar:
        _aba_importar(db, login, baralhos)


# ---- Estudar ----
def _aba_estudar(db, login, baralhos):
    if not any(b["total"] for b in baralhos):
        st.info("Nenhum cartão ainda. Crie um baralho em **Baralhos e cartões** ou use **Importar**.")
        return

    fila = st.session_state.get("fc_fila")
    if fila is None:
        nomes = {b["id"]: f"{b['nome']} ({b['hoje']} hoje / {b['total']})" for b in baralhos if b["total"]}
        sel = st.multiselect("Baralhos", list(nomes), default=list(nomes), format_func=nomes.get)
        modo = st.radio("Modo", ["Revisão do dia (espaçada)", "Todos os cartões (treino livre)"],
                        horizontal=True, key="fc_modo",
                        help="Revisão do dia mostra só os cartões vencidos. No treino livre, as respostas "
                             "também atualizam o agendamento.")
        embaralhar = st.checkbox("Embaralhar", value=True, key="fc_emb")
        if st.button("▶ Começar", type="primary", disabled=not sel):
            cartoes = listar_cartoes(db, login, sel)
            if modo.startswith("Revisão"):
                hoje = _hoje_str()
                cartoes = [c for c in cartoes if (c.get("proxima_revisao") or hoje) <= hoje]
                cartoes.sort(key=lambda c: (c.get("caixa", 1), c.get("proxima_revisao") or ""))
            if embaralhar:
                random.shuffle(cartoes)
            if not cartoes:
                st.success("🎉 Nada para revisar hoje nesses baralhos. Volte amanhã ou use o treino livre.")
                return
            st.session_state["fc_fila"] = [str(c["_id"]) for c in cartoes]
            st.session_state["fc_pos"] = 0
            st.session_state["fc_virado"] = False
            st.session_state["fc_placar"] = {"acertei": 0, "errei": 0, "facil": 0}
            st.rerun()
        return

    pos = st.session_state.get("fc_pos", 0)
    placar = st.session_state.get("fc_placar", {})
    if pos >= len(fila):
        total = sum(placar.values()) or 1
        st.success(f"Sessão concluída: {len(fila)} cartão(ões) vistos.")
        p1, p2, p3 = st.columns(3)
        p1.metric("😕 Errei", placar.get("errei", 0))
        p2.metric("🙂 Acertei", placar.get("acertei", 0))
        p3.metric("😎 Fácil", placar.get("facil", 0))
        st.progress((placar.get("acertei", 0) + placar.get("facil", 0)) / total,
                    text="Aproveitamento da sessão")
        if st.button("Nova sessão", type="primary"):
            _encerrar_sessao()
            st.rerun()
        return

    cartao = db.fc_cartoes.find_one({"_id": _oid(fila[pos]), "usuario_login": login})
    if not cartao:  # excluído no meio da sessão
        st.session_state["fc_pos"] = pos + 1
        st.rerun()

    st.progress(pos / len(fila), text=f"Cartão {pos + 1} de {len(fila)}")
    st.markdown(f"<div class='fc2-meta'>Caixa {cartao.get('caixa', 1)} de {MAX_CAIXA} · "
                f"revisado {cartao.get('revisoes', 0)}x</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='fc2-card fc2-frente'>{_esc(cartao['frente'])}</div>", unsafe_allow_html=True)

    if not st.session_state.get("fc_virado"):
        if st.button("👀 Mostrar resposta", type="primary", width="stretch"):
            st.session_state["fc_virado"] = True
            st.rerun()
    else:
        st.markdown(f"<div class='fc2-card fc2-verso'>{_esc(cartao['verso'])}</div>", unsafe_allow_html=True)
        caixa = int(cartao.get("caixa") or 1)
        b1, b2, b3 = st.columns(3)
        b1.button("😕 Errei · hoje", width="stretch", on_click=_responder,
                  args=(db, login, fila[pos], "errei"))
        b2.button(f"🙂 Acertei · {INTERVALOS[min(MAX_CAIXA, caixa + 1)]}d", width="stretch", type="primary",
                  on_click=_responder, args=(db, login, fila[pos], "acertei"))
        b3.button(f"😎 Fácil · {INTERVALOS[min(MAX_CAIXA, caixa + 2)]}d", width="stretch",
                  on_click=_responder, args=(db, login, fila[pos], "facil"))

    if st.button("⏹ Encerrar sessão", key="fc_parar"):
        _encerrar_sessao()
        st.rerun()


def _responder(db, login, cartao_id, nota):
    registrar_revisao(db, login, cartao_id, nota)
    fila = st.session_state["fc_fila"]
    if nota == "errei" and fila.count(cartao_id) < 3:
        fila.append(cartao_id)  # errou: volta no fim da sessão (no máximo 3 vezes)
    st.session_state["fc_placar"][nota] = st.session_state["fc_placar"].get(nota, 0) + 1
    st.session_state["fc_pos"] = st.session_state.get("fc_pos", 0) + 1
    st.session_state["fc_virado"] = False


def _encerrar_sessao():
    for k in ("fc_fila", "fc_pos", "fc_virado", "fc_placar"):
        st.session_state.pop(k, None)


# ---- Baralhos e cartões ----
def _aba_baralhos(db, login, baralhos):
    with st.expander("➕ Novo baralho", expanded=not baralhos):
        nome = st.text_input("Nome do baralho", key="fc_novo_nome")
        if st.button("Criar baralho", key="fc_criar"):
            if not nome.strip():
                st.warning("Informe um nome.")
            else:
                _, criado = obter_ou_criar_baralho(db, login, nome)
                st.session_state["_fc_msg"] = "Baralho criado." if criado else "Já existia um baralho com esse nome."
                st.rerun()
    if not baralhos:
        return

    st.dataframe([{"Baralho": b["nome"], "Cartões": b["total"], "Para revisar hoje": b["hoje"]} for b in baralhos],
                 width="stretch", hide_index=True)

    nomes = {b["id"]: b["nome"] for b in baralhos}
    bid = st.selectbox("Baralho", list(nomes), format_func=nomes.get)

    c1, c2, c3 = st.columns([2, 1, 1])
    novo = c1.text_input("Renomear", value=nomes[bid], key=f"fc_ren_{bid}", label_visibility="collapsed")
    if c2.button("Salvar nome", key=f"fc_ren_btn_{bid}"):
        if renomear_baralho(db, login, bid, novo):
            st.session_state["_fc_msg"] = "Nome atualizado."
            st.rerun()
        st.warning("Nome vazio ou já usado por outro baralho.")
    with c3:
        if _confirmar("🗑️ Excluir baralho", f"fc_del_{bid}",
                      f"Excluir o baralho '{nomes[bid]}' e todos os seus cartões?"):
            excluir_baralho(db, login, bid)
            _encerrar_sessao()
            st.session_state["_fc_msg"] = "Baralho excluído."
            st.rerun()

    with st.form(f"fc_add_{bid}", clear_on_submit=True):
        st.markdown("**Adicionar cartão**")
        f1, f2 = st.columns(2)
        frente = f1.text_area("Frente (pergunta)", height=90)
        verso = f2.text_area("Verso (resposta)", height=90)
        if st.form_submit_button("Adicionar"):
            ins, rep = adicionar_cartoes(db, login, bid, [(frente, verso)])
            if ins:
                st.session_state["_fc_msg"] = "Cartão adicionado."
                st.rerun()
            st.warning("Já existe um cartão com essa frente." if rep else "Preencha frente e verso.")

    cartoes = listar_cartoes(db, login, [bid])
    if not cartoes:
        st.caption("Este baralho ainda não tem cartões.")
        return

    st.markdown("**Cartões** — edite direto na tabela; marque linhas e use a lixeira para excluir.")
    df = pd.DataFrame([{
        "id": str(c["_id"]), "Frente": c.get("frente", ""), "Verso": c.get("verso", ""),
        "Caixa": c.get("caixa", 1), "Próxima revisão": _fmt_data(c.get("proxima_revisao") or ""),
        "Acertos": c.get("acertos", 0), "Erros": c.get("erros", 0),
    } for c in cartoes])
    editado = st.data_editor(
        df, key=f"fc_editor_{bid}", hide_index=True, width="stretch", num_rows="dynamic",
        column_config={"id": None,
                       "Frente": st.column_config.TextColumn(width="large", required=True),
                       "Verso": st.column_config.TextColumn(width="large", required=True)},
        disabled=["Caixa", "Próxima revisão", "Acertos", "Erros"])

    b1, b2, b3 = st.columns(3)
    if b1.button("💾 Salvar alterações", key=f"fc_salvar_{bid}", type="primary"):
        st.session_state["_fc_msg"] = _salvar_editor(db, login, bid, df, editado)
        st.rerun()
    if b2.button("↺ Reiniciar agendamento", key=f"fc_reset_{bid}",
                 help="Todos os cartões voltam à caixa 1 e ficam para revisar hoje."):
        db.fc_cartoes.update_many({"usuario_login": login, "baralho_id": _oid(bid)},
                                  {"$set": {"caixa": 1, "proxima_revisao": _hoje_str()}})
        st.session_state["_fc_msg"] = "Agendamento reiniciado."
        st.rerun()
    csv_txt = "frente;verso;baralho\n" + "\n".join(
        ";".join(_csv_campo(x) for x in (c.get("frente", ""), c.get("verso", ""), nomes[bid])) for c in cartoes)
    b3.download_button("⬇️ Exportar CSV", csv_txt.encode("utf-8-sig"), file_name=f"flashcards_{nomes[bid]}.csv",
                       mime="text/csv", key=f"fc_exp_{bid}")


def _csv_campo(s):
    s = str(s or "").replace("\n", "\\n")
    return '"' + s.replace('"', '""') + '"' if any(ch in s for ch in ';"') else s


def _salvar_editor(db, login, bid, original, editado):
    ids_orig = set(original["id"])
    ids_novos = set(x for x in editado["id"] if isinstance(x, str) and x)
    removidos = [i for i in ids_orig - ids_novos]
    if removidos:
        db.fc_cartoes.delete_many({"usuario_login": login, "_id": {"$in": [_oid(i) for i in removidos]}})

    por_id = original.set_index("id")
    alterados, novos = 0, []
    for _, r in editado.iterrows():
        frente, verso = str(r.get("Frente") or "").strip(), str(r.get("Verso") or "").strip()
        rid = r.get("id")
        if isinstance(rid, str) and rid in por_id.index:
            o = por_id.loc[rid]
            if frente and verso and (frente != o["Frente"] or verso != o["Verso"]):
                db.fc_cartoes.update_one({"_id": _oid(rid), "usuario_login": login},
                                         {"$set": {"frente": frente, "verso": verso}})
                alterados += 1
        elif frente and verso:
            novos.append((frente, verso))
    ins, _ = adicionar_cartoes(db, login, bid, novos)
    return f"Salvo: {alterados} editado(s), {ins} novo(s), {len(removidos)} excluído(s)."


# ---- Importar ----
def _aba_importar(db, login, baralhos):
    st.markdown("Importe de **CSV**, **TXT** ou do **Anki** (exportar como *texto separado por tabulação*). "
                "Também dá para colar o texto direto.")
    with st.expander("📄 Ver modelo"):
        st.code(MODELO_IMPORTACAO, language="text")
        st.caption("Cabeçalho opcional. Sem cabeçalho: 1ª coluna = frente, 2ª = verso, 3ª = baralho (opcional). "
                   "Separadores aceitos: ponto e vírgula, tabulação, barra vertical ou vírgula. "
                   "Cartões com a mesma frente no mesmo baralho são ignorados.")

    up = st.file_uploader("Arquivo", type=["csv", "txt", "tsv"], key="fc_up")
    colado = st.text_area("... ou cole aqui (uma linha por cartão)", height=150, key="fc_colar",
                          placeholder="Pergunta;Resposta\nOutra pergunta;Outra resposta")
    conteudo = up.getvalue() if up is not None else colado
    if conteudo and _decodificar(conteudo).strip():
        txt = _decodificar(conteudo)
        auto = detectar_delimitador(txt)
        opcoes = list(DELIMITADORES)
        delim = st.selectbox("Separador", opcoes, index=opcoes.index(auto), format_func=DELIMITADORES.get,
                             key="fc_delim", help="Detectado automaticamente; troque se a prévia estiver errada.")
        linhas, erros = interpretar_importacao(txt, delim)

        tem_coluna = any(l["baralho"] for l in linhas)
        destinos = (["(usar a coluna baralho)"] if tem_coluna else []) + [b["nome"] for b in baralhos] + ["➕ Novo baralho…"]
        destino = st.selectbox("Baralho de destino", destinos)
        nome_novo = ""
        if destino == "➕ Novo baralho…":
            nome_novo = st.text_input("Nome do novo baralho", key="fc_destino_novo")

        st.caption(f"Prévia: {len(linhas)} cartão(ões) válidos" + (f" · {len(erros)} linha(s) com problema" if erros else ""))
        if linhas:
            st.dataframe(pd.DataFrame(linhas[:10]).rename(columns=str.capitalize), width="stretch", hide_index=True)
        if erros:
            with st.expander(f"⚠️ {len(erros)} linha(s) ignoradas"):
                for e in erros[:100]:
                    st.write("- ", e)

        pode = bool(linhas) and (destino != "➕ Novo baralho…" or nome_novo.strip())
        if st.button("📥 Importar cartões", type="primary", disabled=not pode, key="fc_imp_btn"):
            alvo = None if destino == "(usar a coluna baralho)" else (nome_novo if destino == "➕ Novo baralho…" else destino)
            resumo = importar_linhas(db, login, linhas, alvo)
            partes = [f"{n}: {i} novo(s)" + (f", {r} repetido(s)" if r else "") + (" (baralho criado)" if c else "")
                      for n, (i, r, c) in resumo.items()]
            st.session_state["_fc_msg"] = "Importação concluída — " + " · ".join(partes)
            st.rerun()

    st.divider()
    st.markdown("#### Trazer do Plano de Estudos")
    do_plano = cartoes_do_plano(db, login)
    if not do_plano:
        st.caption("Nenhum flashcard encontrado nos seus planos de estudo.")
        return
    st.caption("Copia os flashcards dos assuntos do plano para um baralho 'Plano: <nome>'. "
               "Os originais continuam no plano; cartões repetidos são ignorados.")
    sel = st.multiselect("Planos", list(do_plano), default=list(do_plano),
                         format_func=lambda p: f"{p} ({len(do_plano[p])} cartões)", key="fc_planos")
    if st.button("Copiar para baralhos", key="fc_copiar_plano", disabled=not sel):
        linhas = [{"frente": f, "verso": v, "baralho": f"Plano: {p}"} for p in sel for f, v in do_plano[p]]
        resumo = importar_linhas(db, login, linhas)
        st.session_state["_fc_msg"] = "Copiados: " + " · ".join(f"{n}: {i} novo(s)" for n, (i, _, _) in resumo.items())
        st.rerun()
