"""Módulo de Flashcards — baralhos por disciplina, cartões por assunto, revisão espaçada
(Leitner) e importação.

Independente do Plano de Estudos: usa as coleções `fc_baralhos` (com `disciplina`) e
`fc_cartoes` (com `assunto`).
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

import altair as alt
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
SEM_DISCIPLINA = "Sem disciplina"
SEM_ASSUNTO = "Sem assunto"
# Ordem das colunas quando o arquivo vem sem cabeçalho
COLUNAS = ["frente", "verso", "disciplina", "assunto", "baralho"]
# Respostas possíveis; "não sei" e "errei" levam o cartão de volta à caixa 1
NOTAS = {"naosei": "🤷 Não sei", "errei": "😕 Errei", "acertei": "🙂 Acertei", "facil": "😎 Fácil"}
RUIM = ("naosei", "errei")
FAV = "__favoritos__"  # baralho fixo virtual: cartões com favorito=True

MODELO_IMPORTACAO = """frente;verso;disciplina;assunto;baralho
O que é habeas corpus?;Remédio constitucional que protege a liberdade de locomoção.;Direito Constitucional;Remédios constitucionais;Constitucional
Prazo da apelação no CPC?;15 dias úteis (art. 1.003, §5º).;Direito Processual Civil;Recursos;Processo Civil
Requisitos da tutela de urgência?;Probabilidade do direito\\nPerigo de dano ou risco ao resultado útil (art. 300).;Direito Processual Civil;Tutela provisória;Processo Civil"""


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


def _txt(v):
    """Texto limpo de célula (trata None/NaN do data_editor)."""
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return str(v).strip()


def _rotulo_disc(d):
    return d or SEM_DISCIPLINA


def _rotulo_assunto(a):
    return a or SEM_ASSUNTO


def init_indexes(db):
    for fn in (
        lambda: db.fc_baralhos.create_index([("usuario_login", ASCENDING), ("nome", ASCENDING)],
                                            name="uq_fc_baralho", unique=True),
        lambda: db.fc_cartoes.create_index([("usuario_login", ASCENDING), ("baralho_id", ASCENDING),
                                            ("proxima_revisao", ASCENDING)], name="idx_fc_fila"),
        lambda: db.fc_cartoes.create_index([("usuario_login", ASCENDING), ("baralho_id", ASCENDING),
                                            ("assunto", ASCENDING)], name="idx_fc_assunto"),
        lambda: db.fc_revisoes.create_index([("usuario_login", ASCENDING), ("dia", ASCENDING)],
                                            name="idx_fc_rev_dia"),
    ):
        try:
            fn()
        except Exception as e:
            print(f"[flashcards] {e}")


# =============================
# Baralhos e cartões (banco)
# =============================
def listar_baralhos(db, login):
    """Baralhos com disciplina, assuntos, total e cartões a revisar hoje (uma agregação)."""
    hoje = _hoje_str()
    cont = {}
    pipe = [
        {"$match": {"usuario_login": login}},
        {"$group": {"_id": "$baralho_id", "total": {"$sum": 1},
                    "hoje": {"$sum": {"$cond": [{"$lte": ["$proxima_revisao", hoje]}, 1, 0]}},
                    "assuntos": {"$addToSet": {"$ifNull": ["$assunto", ""]}}}},
    ]
    for d in db.fc_cartoes.aggregate(pipe):
        cont[str(d["_id"])] = (d["total"], d["hoje"], sorted(d["assuntos"], key=lambda a: (a == "", a)))
    out = []
    for b in db.fc_baralhos.find({"usuario_login": login}):
        total, a_revisar, assuntos = cont.get(str(b["_id"]), (0, 0, []))
        out.append({"id": str(b["_id"]), "nome": b.get("nome", ""), "disciplina": b.get("disciplina", ""),
                    "descricao": b.get("descricao", ""), "total": total, "hoje": a_revisar,
                    "assuntos": assuntos})
    out.sort(key=lambda b: (b["disciplina"] == "", b["disciplina"].lower(), b["nome"].lower()))
    return out


def resumo_por_assunto(db, login, baralho_ids=None):
    """Linhas por baralho + assunto: cartões, a revisar hoje, caixa média e % de acerto."""
    match = {"usuario_login": login}
    if baralho_ids is not None:
        match["baralho_id"] = {"$in": [o for o in (_oid(b) for b in baralho_ids) if o]}
    pipe = [
        {"$match": match},
        {"$group": {"_id": {"b": "$baralho_id", "a": {"$ifNull": ["$assunto", ""]}},
                    "total": {"$sum": 1},
                    "hoje": {"$sum": {"$cond": [{"$lte": ["$proxima_revisao", _hoje_str()]}, 1, 0]}},
                    "caixa": {"$avg": {"$ifNull": ["$caixa", 1]}},
                    "acertos": {"$sum": {"$ifNull": ["$acertos", 0]}},
                    "erros": {"$sum": {"$add": [{"$ifNull": ["$erros", 0]}, {"$ifNull": ["$naosei", 0]}]}}}},
    ]
    return [{"baralho_id": str(d["_id"]["b"]), "assunto": d["_id"]["a"], "total": d["total"],
             "hoje": d["hoje"], "caixa": d["caixa"], "acertos": d["acertos"], "erros": d["erros"]}
            for d in db.fc_cartoes.aggregate(pipe)]


def disciplinas_sugeridas(db, login):
    """Disciplinas já usadas nos baralhos e nos questionários do sistema."""
    nomes = set(db.fc_baralhos.distinct("disciplina", {"usuario_login": login}))
    try:
        nomes |= set(db.questionarios.distinct("disciplina"))
    except Exception:
        pass
    return sorted(n for n in nomes if n and not n.startswith("—") and n != "Sem Disciplina")


def obter_ou_criar_baralho(db, login, nome, disciplina=""):
    """Retorna (id, criado). Se o baralho existir sem disciplina, recebe a informada."""
    nome = (nome or "").strip() or (disciplina or "").strip() or "Geral"
    disciplina = (disciplina or "").strip()
    b = db.fc_baralhos.find_one({"usuario_login": login, "nome": nome})
    if b:
        if disciplina and not b.get("disciplina"):
            db.fc_baralhos.update_one({"_id": b["_id"]}, {"$set": {"disciplina": disciplina}})
        return str(b["_id"]), False
    res = db.fc_baralhos.insert_one({"usuario_login": login, "nome": nome, "disciplina": disciplina,
                                     "descricao": "", "criado_em": _agora()})
    return str(res.inserted_id), True


def atualizar_baralho(db, login, baralho_id, novo_nome, disciplina):
    novo_nome = (novo_nome or "").strip()
    if not novo_nome:
        return False
    if db.fc_baralhos.find_one({"usuario_login": login, "nome": novo_nome, "_id": {"$ne": _oid(baralho_id)}}):
        return False
    db.fc_baralhos.update_one({"_id": _oid(baralho_id), "usuario_login": login},
                              {"$set": {"nome": novo_nome, "disciplina": (disciplina or "").strip()}})
    return True


def renomear_assunto(db, login, baralho_id, antigo, novo):
    r = db.fc_cartoes.update_many(
        {"usuario_login": login, "baralho_id": _oid(baralho_id),
         "assunto": antigo if antigo else {"$in": ["", None]}},
        {"$set": {"assunto": (novo or "").strip()}})
    return r.modified_count


def excluir_baralho(db, login, baralho_id):
    oid = _oid(baralho_id)
    db.fc_cartoes.delete_many({"usuario_login": login, "baralho_id": oid})
    db.fc_revisoes.delete_many({"usuario_login": login, "baralho_id": oid})
    db.fc_baralhos.delete_one({"_id": oid, "usuario_login": login})


def _novo_cartao(login, baralho_oid, frente, verso, assunto=""):
    return {"usuario_login": login, "baralho_id": baralho_oid,
            "frente": frente.strip(), "verso": verso.strip(), "assunto": (assunto or "").strip(),
            "caixa": 1, "proxima_revisao": _hoje_str(), "ultima_revisao": None,
            "revisoes": 0, "acertos": 0, "erros": 0, "criado_em": _agora()}


def listar_cartoes(db, login, baralho_ids=None, assuntos=None):
    """assuntos=None: todos; senão, só os assuntos listados ('' = sem assunto).
    FAV em baralho_ids inclui os favoritos de qualquer baralho."""
    filtro = {"usuario_login": login}
    if baralho_ids is not None:
        cond = [{"baralho_id": {"$in": [o for o in (_oid(b) for b in baralho_ids if b != FAV) if o]}}]
        if FAV in baralho_ids:
            cond.append({"favorito": True})
        filtro["$or"] = cond
    if assuntos is not None:
        vals = list(assuntos) + ([None] if "" in assuntos else [])
        filtro["assunto"] = {"$in": vals}
    return list(db.fc_cartoes.find(filtro).sort([("assunto", ASCENDING), ("criado_em", ASCENDING)]))


def adicionar_cartoes(db, login, baralho_id, cartoes):
    """cartoes: tuplas (frente, verso) ou (frente, verso, assunto). Frentes já existentes
    no baralho são ignoradas. Retorna (inseridos, repetidos)."""
    boid = _oid(baralho_id)
    existentes = set(db.fc_cartoes.distinct("frente", {"usuario_login": login, "baralho_id": boid}))
    docs, repetidos = [], 0
    for c in cartoes:
        frente, verso = (c[0] or "").strip(), (c[1] or "").strip()
        assunto = (c[2] or "").strip() if len(c) > 2 else ""
        if not frente or not verso:
            continue
        if frente in existentes:
            repetidos += 1
            continue
        existentes.add(frente)
        docs.append(_novo_cartao(login, boid, frente, verso, assunto))
    if docs:
        db.fc_cartoes.insert_many(docs, ordered=False)
    return len(docs), repetidos


def registrar_revisao(db, login, cartao_id, nota):
    """nota: 'naosei' | 'errei' | 'acertei' | 'facil' — sistema de Leitner.
    Cada resposta também fica em `fc_revisoes`, base do termômetro de desempenho."""
    c = db.fc_cartoes.find_one({"_id": _oid(cartao_id), "usuario_login": login})
    if not c:
        return None
    antes = caixa = int(c.get("caixa") or 1)
    if nota in RUIM:
        caixa, dias = 1, 0  # volta para o início e reaparece ainda hoje
    else:
        caixa = min(MAX_CAIXA, caixa + (2 if nota == "facil" else 1))
        dias = INTERVALOS[caixa]
    proxima = (_hoje() + timedelta(days=dias)).isoformat()
    db.fc_cartoes.update_one({"_id": c["_id"]}, {
        "$set": {"caixa": caixa, "proxima_revisao": proxima, "ultima_revisao": _agora(), "ultima_nota": nota},
        "$inc": {"revisoes": 1, "acertos": int(nota not in RUIM), "erros": int(nota == "errei"),
                 "naosei": int(nota == "naosei")},
    })
    db.fc_revisoes.insert_one({"usuario_login": login, "cartao_id": c["_id"], "baralho_id": c.get("baralho_id"),
                               "assunto": c.get("assunto") or "", "nota": nota, "caixa_antes": antes,
                               "dia": _hoje_str(), "em": _agora()})
    return proxima


def favoritar(db, login, cartao_id, valor=True):
    db.fc_cartoes.update_one({"_id": _oid(cartao_id), "usuario_login": login}, {"$set": {"favorito": bool(valor)}})


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
    """Lê frente/verso/disciplina/assunto/baralho de CSV, TSV (export do Anki) ou texto colado.
    Cabeçalho opcional: se a 1ª linha tiver 'frente' e 'verso', as colunas são lidas pelo nome
    (em qualquer ordem); senão, na ordem frente, verso, disciplina, assunto, baralho
    (só as duas primeiras são obrigatórias).
    Retorna (linhas, erros) com linhas = [{frente, verso, disciplina, assunto, baralho}]."""
    txt = _decodificar(txt)
    delim = delimitador or detectar_delimitador(txt)
    rows = list(csv.reader(io.StringIO(txt), delimiter=delim))
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return [], ["Nenhuma linha encontrada."]

    cab = [(c or "").strip().lower() for c in rows[0]]
    if "frente" in cab and "verso" in cab:
        idx = {col: cab.index(col) for col in COLUNAS if col in cab}
        dados, inicio = rows[1:], 2
    else:
        idx = {col: i for i, col in enumerate(COLUNAS)}
        dados, inicio = rows, 1

    def campo(r, col):
        i = idx.get(col)
        return r[i].strip().replace("\\n", "\n") if i is not None and i < len(r) else ""

    out, erros = [], []
    for n, r in enumerate(dados, start=inicio):
        if len(r) <= max(idx["frente"], idx["verso"]):
            erros.append(f"Linha {n}: esperava frente e verso separados por '{DELIMITADORES.get(delim, delim)}'.")
            continue
        linha = {col: campo(r, col) for col in COLUNAS}
        if not linha["frente"] or not linha["verso"]:
            erros.append(f"Linha {n}: frente ou verso vazio.")
            continue
        out.append(linha)
    return out, erros


def importar_linhas(db, login, linhas, baralho_destino=None, disciplina_destino=None, assunto_padrao=""):
    """baralho_destino=None usa a coluna 'baralho' (vazia -> nome da disciplina -> 'Geral').
    disciplina_destino sobrepõe a coluna 'disciplina'; assunto_padrao vale para linhas sem assunto.
    Retorna {nome_baralho: (inseridos, repetidos, criado)}."""
    grupos = {}
    for l in linhas:
        disc = (disciplina_destino or l.get("disciplina") or "").strip()
        nome = (baralho_destino or l.get("baralho") or disc or "Geral").strip()
        g = grupos.setdefault(nome, {"disciplina": "", "cartoes": []})
        g["disciplina"] = g["disciplina"] or disc
        g["cartoes"].append((l["frente"], l["verso"], l.get("assunto") or assunto_padrao))
    resumo = {}
    for nome, g in grupos.items():
        bid, criado = obter_ou_criar_baralho(db, login, nome, g["disciplina"])
        ins, rep = adicionar_cartoes(db, login, bid, g["cartoes"])
        resumo[nome] = (ins, rep, criado)
    return resumo


def cartoes_do_plano(db, login):
    """Flashcards do Plano de Estudos do usuário, já com disciplina e assunto do item:
    {nome_do_plano: [{frente, verso, disciplina, assunto, baralho}, ...]}."""
    planos = {p["_id"]: p.get("nome", "Plano") for p in db.est_planos.find({"usuario_login": login}, {"nome": 1})}
    if not planos:
        return {}
    itens = {}
    for it in db.est_planejamento.find({"plano_id": {"$in": list(planos)}},
                                       {"plano_id": 1, "disciplina_nome": 1, "assunto_nome": 1, "titulo": 1}):
        plano = planos.get(it["plano_id"], "Plano")
        disc = (it.get("disciplina_nome") or "").strip()
        itens[str(it["_id"])] = {"plano": plano, "disciplina": disc,
                                 "assunto": (it.get("assunto_nome") or it.get("titulo") or "").strip(),
                                 "baralho": disc or f"Plano: {plano}"}
    if not itens:
        return {}
    chaves = list(itens) + [ObjectId(i) for i in itens]
    out = {}
    for c in db.flashcards.find({"item_id": {"$in": chaves}}, {"item_id": 1, "frente": 1, "verso": 1}):
        it = itens.get(str(c.get("item_id")))
        if it and c.get("frente") and c.get("verso"):
            out.setdefault(it["plano"], []).append({
                "frente": c["frente"], "verso": c["verso"], "disciplina": it["disciplina"],
                "assunto": it["assunto"], "baralho": it["baralho"]})
    return out


def exportar_csv(cartoes, baralho):
    cab = ";".join(COLUNAS)
    linhas = [";".join(_csv_campo(x) for x in (c.get("frente", ""), c.get("verso", ""),
                                                baralho.get("disciplina", ""), c.get("assunto", ""),
                                                baralho.get("nome", "")))
              for c in cartoes]
    return (cab + "\n" + "\n".join(linhas)).encode("utf-8-sig")


def _csv_campo(s):
    s = str(s or "").replace("\n", "\\n")
    return '"' + s.replace('"', '""') + '"' if any(ch in s for ch in ';"') else s


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
    .fc2-tag{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;
      margin-right:6px;background:#e6f3f4;color:#19747E}
    .fc2-tag.assunto{background:#f1ecfd;color:#6b46c1}
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


def _escolher_texto(label, opcoes, atual="", key=None, help=None):
    """Selectbox que aceita um valor novo digitado (disciplina/assunto)."""
    opcoes = [""] + [o for o in opcoes if o]
    if atual and atual not in opcoes:
        opcoes.append(atual)
    v = st.selectbox(label, opcoes, index=opcoes.index(atual) if atual in opcoes else 0,
                     accept_new_options=True, key=key, help=help,
                     format_func=lambda x: x or "— nenhum —")
    return (v or "").strip()


def render_page(db, login):
    _css()
    st.title("🗂️ Flashcards")
    msg = st.session_state.pop("_fc_msg", None)
    if msg:
        st.success(msg)

    baralhos = listar_baralhos(db, login)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Disciplinas", len({b["disciplina"] for b in baralhos if b["disciplina"]}))
    c2.metric("Baralhos", len(baralhos))
    c3.metric("Cartões", sum(b["total"] for b in baralhos))
    c4.metric("Para revisar hoje", sum(b["hoje"] for b in baralhos))

    t_desemp, t_estudar, t_baralhos, t_importar = st.tabs(
        ["🌡️ Desempenho", "📖 Estudar", "🗃️ Baralhos e cartões", "📥 Importar"])
    with t_desemp:
        _aba_desempenho(db, login, baralhos)
    with t_estudar:
        _aba_estudar(db, login, baralhos)
    with t_baralhos:
        _aba_baralhos(db, login, baralhos)
    with t_importar:
        _aba_importar(db, login, baralhos)


# ---- Estudar ----
MODOS = {"dia": "Revisão do dia (espaçada)", "livre": "Treino livre (todos)",
         "ruins": "Só os que errei ou não sabia"}


def _iniciar_sessao(ids, inverter=False):
    st.session_state.update(fc_fila=list(ids), fc_pos=0, fc_virado=False, fc_registrado=False,
                            fc_inverter=inverter, fc_placar={k: 0 for k in NOTAS}, fc_por_assunto={})


def _aba_estudar(db, login, baralhos):
    com_cartoes = [b for b in baralhos if b["total"]]
    if not com_cartoes:
        st.info("Nenhum cartão ainda. Crie um baralho em **Baralhos e cartões** ou use **Importar**.")
        return

    fila = st.session_state.get("fc_fila")
    if fila is None:
        discs = list(dict.fromkeys(b["disciplina"] for b in com_cartoes))
        sel_disc = st.multiselect("Disciplinas", discs, default=discs, format_func=_rotulo_disc)
        candidatos = [b for b in com_cartoes if b["disciplina"] in sel_disc]
        n_fav = db.fc_cartoes.count_documents({"usuario_login": login, "favorito": True})
        nomes = {FAV: f"⭐ Favoritos ({n_fav})"} if n_fav else {}
        nomes.update({b["id"]: f"{b['nome']} ({b['hoje']} hoje / {b['total']})" for b in candidatos})
        sel = st.multiselect("Baralhos", list(nomes), default=[i for i in nomes if i != FAV], format_func=nomes.get,
                             help="⭐ Favoritos é um baralho fixo com os cartões que você marcou, de qualquer "
                                  "disciplina. Marque com a estrela durante o estudo ou na tabela de cartões.")

        assuntos = sorted({a for b in candidatos if b["id"] in sel for a in b["assuntos"]},
                          key=lambda a: (a == "", a.lower()))
        sel_ass = st.multiselect("Assuntos (vazio = todos)", assuntos, format_func=_rotulo_assunto)

        modo = st.radio("Modo", list(MODOS), format_func=MODOS.get, horizontal=True, key="fc_modo2",
                        help="Em todos os modos as respostas atualizam o agendamento.")
        o1, o2, o3 = st.columns(3)
        embaralhar = o1.checkbox("Embaralhar", value=True, key="fc_emb")
        inverter = o2.checkbox("Verso primeiro", key="fc_inv", help="Mostra a resposta e você lembra a pergunta.")
        limite = o3.number_input("Máx. de cartões (0 = todos)", 0, 1000, 0, step=10, key="fc_lim")
        if st.button("▶ Começar", type="primary", disabled=not sel):
            cartoes = listar_cartoes(db, login, sel, sel_ass or None)
            if modo == "dia":
                hoje = _hoje_str()
                cartoes = [c for c in cartoes if (c.get("proxima_revisao") or hoje) <= hoje]
                cartoes.sort(key=lambda c: (c.get("caixa", 1), c.get("proxima_revisao") or ""))
            elif modo == "ruins":
                cartoes = [c for c in cartoes if c.get("ultima_nota") in RUIM]
            if embaralhar:
                random.shuffle(cartoes)
            if limite:
                cartoes = cartoes[:limite]
            if not cartoes:
                st.success("🎉 Nenhum cartão com esses filtros neste modo.")
                return
            _iniciar_sessao([str(c["_id"]) for c in cartoes], inverter)
            st.rerun()
        return

    pos = st.session_state.get("fc_pos", 0)
    placar = st.session_state.get("fc_placar", {})
    if pos >= len(fila):
        _fim_sessao(placar)
        return

    cid = fila[pos]
    cartao = db.fc_cartoes.find_one({"_id": _oid(cid), "usuario_login": login})
    if not cartao:  # excluído no meio da sessão
        _avancar()
        st.rerun()

    bar = next((b for b in baralhos if b["id"] == str(cartao.get("baralho_id"))), {})
    inv = st.session_state.get("fc_inverter", False)
    pergunta, resposta = (cartao["verso"], cartao["frente"]) if inv else (cartao["frente"], cartao["verso"])
    assunto = cartao.get("assunto") or ""
    fav = bool(cartao.get("favorito"))

    st.progress(pos / len(fila), text=f"Cartão {pos + 1} de {len(fila)}")
    t1, t2 = st.columns([6, 1])
    t1.markdown(
        f"<span class='fc2-tag'>{_esc(_rotulo_disc(bar.get('disciplina')))}</span>"
        f"<span class='fc2-tag assunto'>{_esc(_rotulo_assunto(assunto))}</span>"
        f"<span class='fc2-meta'>{_esc(bar.get('nome', ''))} · caixa {cartao.get('caixa', 1)} de {MAX_CAIXA} · "
        f"revisado {cartao.get('revisoes', 0)}x</span>", unsafe_allow_html=True)
    t2.button("⭐" if fav else "☆", key=f"fc_fav_{cid}_{pos}", width="stretch",
              help="Tirar dos favoritos" if fav else "Guardar no baralho ⭐ Favoritos",
              on_click=favoritar, args=(db, login, cid, not fav))
    st.markdown(f"<div class='fc2-card fc2-frente'>{_esc(pergunta)}</div>", unsafe_allow_html=True)

    if not st.session_state.get("fc_virado"):
        b1, b2 = st.columns([3, 1])
        b1.button("👀 Mostrar resposta", type="primary", width="stretch", on_click=_virar)
        b2.button("🤷 Não sei", width="stretch", help="Registra que você não lembrava e mostra a resposta.",
                  on_click=_responder, args=(db, login, cid, "naosei", assunto, False))
    else:
        st.markdown(f"<div class='fc2-card fc2-verso'>{_esc(resposta)}</div>", unsafe_allow_html=True)
        if st.session_state.get("fc_registrado"):
            st.caption("Registrado como **Não sei**: o cartão volta para a caixa 1 e reaparece nesta sessão.")
            st.button("Próximo ▶", type="primary", width="stretch", on_click=_avancar)
        else:
            caixa = int(cartao.get("caixa") or 1)
            rot = {"naosei": "🤷 Não sei · hoje", "errei": "😕 Errei · hoje",
                   "acertei": f"🙂 Acertei · {INTERVALOS[min(MAX_CAIXA, caixa + 1)]}d",
                   "facil": f"😎 Fácil · {INTERVALOS[min(MAX_CAIXA, caixa + 2)]}d"}
            for col, (nota, label) in zip(st.columns(4), rot.items()):
                col.button(label, width="stretch", type="primary" if nota == "acertei" else "secondary",
                           key=f"fc_r_{nota}", on_click=_responder, args=(db, login, cid, nota, assunto))

    if st.button("⏹ Encerrar sessão", key="fc_parar"):
        _encerrar_sessao()
        st.rerun()


def _fim_sessao(placar):
    vistos = sum(placar.values())
    st.success(f"Sessão concluída: {vistos} resposta(s).")
    for col, (nota, label) in zip(st.columns(4), NOTAS.items()):
        col.metric(label, placar.get(nota, 0))
    st.progress((placar.get("acertei", 0) + placar.get("facil", 0)) / (vistos or 1),
                text="Aproveitamento da sessão")
    por_assunto = st.session_state.get("fc_por_assunto") or {}
    if len(por_assunto) > 1:
        st.markdown("**Na sessão, por assunto**")
        st.dataframe(sorted(({"Assunto": _rotulo_assunto(a), "Respostas": v["n"], "Erros/não sei": v["erros"],
                              "Acerto (%)": round((v["n"] - v["erros"]) / v["n"] * 100, 1)}
                             for a, v in por_assunto.items()), key=lambda r: r["Acerto (%)"]),
                     width="stretch", hide_index=True)
    if st.button("Nova sessão", type="primary"):
        _encerrar_sessao()
        st.rerun()


def _virar():
    st.session_state["fc_virado"] = True


def _avancar():
    st.session_state["fc_pos"] = st.session_state.get("fc_pos", 0) + 1
    st.session_state["fc_virado"] = False
    st.session_state["fc_registrado"] = False


def _responder(db, login, cartao_id, nota, assunto="", avancar=True):
    registrar_revisao(db, login, cartao_id, nota)
    fila = st.session_state["fc_fila"]
    if nota in RUIM and fila.count(cartao_id) < 3:
        fila.append(cartao_id)  # volta no fim da sessão (no máximo 3 vezes)
    placar = st.session_state.setdefault("fc_placar", {})
    placar[nota] = placar.get(nota, 0) + 1
    pa = st.session_state.setdefault("fc_por_assunto", {}).setdefault(assunto, {"n": 0, "erros": 0})
    pa["n"] += 1
    pa["erros"] += 1 if nota in RUIM else 0
    if avancar:
        _avancar()
    else:
        st.session_state["fc_virado"] = True
        st.session_state["fc_registrado"] = True


def _encerrar_sessao():
    for k in ("fc_fila", "fc_pos", "fc_virado", "fc_registrado", "fc_inverter", "fc_placar", "fc_por_assunto"):
        st.session_state.pop(k, None)


# ---- Baralhos e cartões ----
def _aba_baralhos(db, login, baralhos):
    sugestoes = disciplinas_sugeridas(db, login)
    with st.expander("➕ Novo baralho", expanded=not baralhos):
        n1, n2 = st.columns(2)
        with n1:
            nome = st.text_input("Nome do baralho", key="fc_novo_nome")
        with n2:
            disc = _escolher_texto("Disciplina", sugestoes, key="fc_novo_disc",
                                   help="Escolha uma existente ou digite uma nova.")
        if st.button("Criar baralho", key="fc_criar"):
            if not nome.strip():
                st.warning("Informe um nome.")
            else:
                _, criado = obter_ou_criar_baralho(db, login, nome, disc)
                st.session_state["_fc_msg"] = "Baralho criado." if criado else "Já existia um baralho com esse nome."
                st.rerun()
    if not baralhos:
        return

    st.dataframe([{
        "Disciplina": _rotulo_disc(b["disciplina"]), "Baralho": b["nome"],
        "Assuntos": len([a for a in b["assuntos"] if a]),
        "Lista de assuntos": ", ".join(_rotulo_assunto(a) for a in b["assuntos"]),
        "Cartões": b["total"], "Para revisar hoje": b["hoje"],
    } for b in baralhos], width="stretch", hide_index=True)

    por_id = {b["id"]: b for b in baralhos}
    bid = st.selectbox("Baralho", list(por_id),
                       format_func=lambda i: f"{_rotulo_disc(por_id[i]['disciplina'])} › {por_id[i]['nome']}")
    bar = por_id[bid]

    with st.expander("✏️ Nome, disciplina e assuntos do baralho"):
        e1, e2 = st.columns(2)
        with e1:
            novo = st.text_input("Nome", value=bar["nome"], key=f"fc_ren_{bid}")
        with e2:
            nova_disc = _escolher_texto("Disciplina", sugestoes, atual=bar["disciplina"], key=f"fc_disc_{bid}")
        if st.button("Salvar baralho", key=f"fc_ren_btn_{bid}"):
            if atualizar_baralho(db, login, bid, novo, nova_disc):
                st.session_state["_fc_msg"] = "Baralho atualizado."
                st.rerun()
            st.warning("Nome vazio ou já usado por outro baralho.")

        if bar["assuntos"]:
            st.markdown("**Renomear ou juntar assuntos** (dar o mesmo nome a dois assuntos junta os cartões)")
            r1, r2, r3 = st.columns([2, 2, 1])
            antigo = r1.selectbox("Assunto", bar["assuntos"], format_func=_rotulo_assunto, key=f"fc_ass_old_{bid}")
            novo_ass = r2.text_input("Novo nome", key=f"fc_ass_new_{bid}")
            r3.write("")
            if r3.button("Aplicar", key=f"fc_ass_btn_{bid}"):
                n = renomear_assunto(db, login, bid, antigo, novo_ass)
                st.session_state["_fc_msg"] = f"{n} cartão(ões) atualizados."
                st.rerun()

        if _confirmar("🗑️ Excluir baralho", f"fc_del_{bid}",
                      f"Excluir o baralho '{bar['nome']}' e todos os seus cartões?"):
            excluir_baralho(db, login, bid)
            _encerrar_sessao()
            st.session_state["_fc_msg"] = "Baralho excluído."
            st.rerun()

    with st.form(f"fc_add_{bid}", clear_on_submit=True):
        st.markdown("**Adicionar cartão**")
        assunto = st.selectbox("Assunto", [""] + [a for a in bar["assuntos"] if a], accept_new_options=True,
                               format_func=lambda x: x or "— sem assunto —",
                               help="Escolha um assunto do baralho ou digite um novo.")
        f1, f2 = st.columns(2)
        frente = f1.text_area("Frente (pergunta)", height=90)
        verso = f2.text_area("Verso (resposta)", height=90)
        if st.form_submit_button("Adicionar"):
            ins, rep = adicionar_cartoes(db, login, bid, [(frente, verso, assunto or "")])
            if ins:
                st.session_state["_fc_msg"] = "Cartão adicionado."
                st.rerun()
            st.warning("Já existe um cartão com essa frente." if rep else "Preencha frente e verso.")

    filtro_ass = None
    if len(bar["assuntos"]) > 1:
        escolha = st.multiselect("Filtrar por assunto", bar["assuntos"], format_func=_rotulo_assunto,
                                 key=f"fc_filtro_ass_{bid}")
        filtro_ass = escolha or None
    cartoes = listar_cartoes(db, login, [bid], filtro_ass)
    if not cartoes:
        st.caption("Este baralho ainda não tem cartões.")
        return

    st.markdown("**Cartões** — edite direto na tabela; marque linhas e use a lixeira para excluir.")
    df = pd.DataFrame([{
        "id": str(c["_id"]), "⭐": bool(c.get("favorito")), "Assunto": c.get("assunto") or "",
        "Frente": c.get("frente", ""),
        "Verso": c.get("verso", ""), "Caixa": c.get("caixa", 1),
        "Próxima revisão": _fmt_data(c.get("proxima_revisao") or ""),
        "Acertos": c.get("acertos", 0), "Erros": c.get("erros", 0),
    } for c in cartoes])
    editado = st.data_editor(
        df, key=f"fc_editor_{bid}_{'|'.join(filtro_ass or [])}", hide_index=True, width="stretch",
        num_rows="dynamic",
        column_config={"id": None,
                       "⭐": st.column_config.CheckboxColumn("⭐", help="Favorito", width="small", default=False),
                       "Assunto": st.column_config.TextColumn(width="medium"),
                       "Frente": st.column_config.TextColumn(width="large", required=True),
                       "Verso": st.column_config.TextColumn(width="large", required=True)},
        disabled=["Caixa", "Próxima revisão", "Acertos", "Erros"])

    b1, b2, b3 = st.columns(3)
    if b1.button("💾 Salvar alterações", key=f"fc_salvar_{bid}", type="primary"):
        st.session_state["_fc_msg"] = _salvar_editor(db, login, bid, df, editado)
        st.rerun()
    if b2.button("↺ Reiniciar agendamento", key=f"fc_reset_{bid}",
                 help="Os cartões listados voltam à caixa 1 e ficam para revisar hoje."):
        db.fc_cartoes.update_many({"usuario_login": login, "_id": {"$in": [c["_id"] for c in cartoes]}},
                                  {"$set": {"caixa": 1, "proxima_revisao": _hoje_str()}})
        st.session_state["_fc_msg"] = "Agendamento reiniciado."
        st.rerun()
    b3.download_button("⬇️ Exportar CSV", exportar_csv(cartoes, bar), file_name=f"flashcards_{bar['nome']}.csv",
                       mime="text/csv", key=f"fc_exp_{bid}")


def _salvar_editor(db, login, bid, original, editado):
    ids_orig = set(original["id"])
    ids_novos = set(x for x in editado["id"] if isinstance(x, str) and x)
    removidos = [i for i in ids_orig - ids_novos]
    if removidos:
        db.fc_cartoes.delete_many({"usuario_login": login, "_id": {"$in": [_oid(i) for i in removidos]}})

    por_id = original.set_index("id")
    alterados, novos = 0, []
    for _, r in editado.iterrows():
        frente, verso, assunto = _txt(r.get("Frente")), _txt(r.get("Verso")), _txt(r.get("Assunto"))
        rid = r.get("id")
        if isinstance(rid, str) and rid in por_id.index:
            o = por_id.loc[rid]
            fav = bool(r.get("⭐")) if not pd.isna(r.get("⭐")) else False
            if frente and verso and (frente != o["Frente"] or verso != o["Verso"] or assunto != o["Assunto"]
                                     or fav != bool(o["⭐"])):
                db.fc_cartoes.update_one({"_id": _oid(rid), "usuario_login": login},
                                         {"$set": {"frente": frente, "verso": verso, "assunto": assunto,
                                                   "favorito": fav}})
                alterados += 1
        elif frente and verso:
            novos.append((frente, verso, assunto))
    ins, _ = adicionar_cartoes(db, login, bid, novos)
    return f"Salvo: {alterados} editado(s), {ins} novo(s), {len(removidos)} excluído(s)."


# ---- Desempenho (termômetro) ----
FAIXAS = {"🔴 Reforçar": "#d64545", "🟡 Atenção": "#e0a526", "🟢 Em dia": "#2e9e5b", "⚪ Não iniciado": "#9aa0a6"}
MIN_RECENTE = 5  # respostas nos últimos 30 dias para usar a janela recente


def _faixa(p):
    if pd.isna(p):
        return "⚪ Não iniciado"
    return "🔴 Reforçar" if p >= 45 else "🟡 Atenção" if p >= 25 else "🟢 Em dia"


def dados_termometro(db, login, baralhos, dias=84):
    """(cartões, revisões dos últimos `dias`) como DataFrames, com disciplina e assunto já resolvidos."""
    disc_de = {b["id"]: _rotulo_disc(b["disciplina"]) for b in baralhos}
    nome_de = {b["id"]: b["nome"] for b in baralhos}
    hoje = _hoje_str()
    cards = db.fc_cartoes.find({"usuario_login": login},
                               {"baralho_id": 1, "assunto": 1, "caixa": 1, "proxima_revisao": 1, "frente": 1,
                                "acertos": 1, "erros": 1, "naosei": 1})
    c = pd.DataFrame([{
        "id": str(x["_id"]), "Disciplina": disc_de.get(str(x.get("baralho_id")), _rotulo_disc("")),
        "Baralho": nome_de.get(str(x.get("baralho_id")), "—"), "Assunto": _rotulo_assunto(x.get("assunto")),
        "Frente": x.get("frente", ""), "caixa": int(x.get("caixa") or 1),
        "prox": x.get("proxima_revisao") or hoje, "a": x.get("acertos") or 0, "e": x.get("erros") or 0,
        "ns": x.get("naosei") or 0} for x in cards])
    if c.empty:
        return c, pd.DataFrame(columns=["id", "nota", "dia"])
    c["dom"] = (c["caixa"] - 1) / (MAX_CAIXA - 1)
    c["venc"] = c["prox"] <= hoje
    corte = (_hoje() - timedelta(days=dias)).isoformat()
    logs = db.fc_revisoes.find({"usuario_login": login, "dia": {"$gte": corte}}, {"cartao_id": 1, "nota": 1, "dia": 1})
    l = pd.DataFrame([{"id": str(x["cartao_id"]), "nota": x["nota"], "dia": x["dia"]} for x in logs],
                     columns=["id", "nota", "dia"])
    return c, l


def indicadores(c, l, chaves, janela=30):
    """Índice de prioridade de reforço (0–100) por grupo.
    45% erro (retenção recente, ou histórico se houver poucas respostas recentes) + 25% falta de domínio
    (caixa média) + 15% taxa de 'não sei' + 15% cartões vencidos."""
    g = c.groupby(chaves).agg(Cartões=("id", "size"), dom=("dom", "mean"), venc=("venc", "mean"),
                              a=("a", "sum"), e=("e", "sum"), ns=("ns", "sum")).reset_index()
    corte = (_hoje() - timedelta(days=janela)).isoformat()
    rec = l[l["dia"] >= corte].merge(c[["id"] + chaves], on="id")
    rec = rec.assign(bom=~rec["nota"].isin(RUIM), nsr=rec["nota"].eq("naosei"))
    r = rec.groupby(chaves).agg(n_rec=("nota", "size"), bom_rec=("bom", "sum"), ns_rec=("nsr", "sum")).reset_index()
    g = g.merge(r, on=chaves, how="left")
    g[["n_rec", "bom_rec", "ns_rec"]] = g[["n_rec", "bom_rec", "ns_rec"]].fillna(0)
    tot = g["a"] + g["e"] + g["ns"]
    recente = g["n_rec"] >= MIN_RECENTE
    g["ret"] = (g["bom_rec"] / g["n_rec"].clip(lower=1)).where(recente, (g["a"] / tot.clip(lower=1)).where(tot > 0))
    g["nsp"] = (g["ns_rec"] / g["n_rec"].clip(lower=1)).where(recente, (g["ns"] / tot.clip(lower=1)).fillna(0))
    g["Prioridade"] = (100 * (0.45 * (1 - g["ret"]) + 0.25 * (1 - g["dom"]) + 0.15 * g["nsp"]
                              + 0.15 * g["venc"])).round(0)
    g["Situação"] = g["Prioridade"].map(_faixa)
    g["Base"] = ["30 dias" if r_ else ("histórico" if t else "—") for r_, t in zip(recente, tot)]
    g["Retenção (%)"] = (g["ret"] * 100).round(1)
    g["Domínio (%)"] = (g["dom"] * 100).round(1)
    g["Não sei (%)"] = (g["nsp"] * 100).round(1)
    g["Vencidos (%)"] = (g["venc"] * 100).round(1)
    g["Respostas"] = tot.astype(int)
    return g.sort_values("Prioridade", ascending=False, na_position="last")


def _sequencia(dias_estudo):
    """Dias seguidos de estudo terminando hoje (ou ontem, se hoje ainda não estudou)."""
    d = _hoje()
    if d.isoformat() not in dias_estudo:
        d -= timedelta(days=1)
    n = 0
    while d.isoformat() in dias_estudo:
        n, d = n + 1, d - timedelta(days=1)
    return n


def _treinar(ids, rotulo):
    if not ids:
        return
    _encerrar_sessao()
    _iniciar_sessao(ids)
    st.session_state["_fc_msg"] = f"Sessão de reforço montada ({len(ids)} cartões: {rotulo}). Abra a aba 📖 Estudar."


def _cor_faixa():
    return alt.Scale(domain=list(FAIXAS), range=list(FAIXAS.values()))


def tendencia(c, l, chaves, dias=14):
    """Variação da retenção (pontos percentuais): últimos `dias` × os `dias` anteriores.
    Só aparece para grupos com pelo menos 3 respostas em cada período."""
    hoje = _hoje()
    c1, c0 = (hoje - timedelta(days=dias)).isoformat(), (hoje - timedelta(days=2 * dias)).isoformat()
    ev = l[l["dia"] > c0].merge(c[["id"] + chaves], on="id")
    if ev.empty:
        return pd.DataFrame(columns=chaves + ["Tendência (pp)"])
    ev = ev.assign(bom=~ev["nota"].isin(RUIM), agora=ev["dia"] > c1)
    g = ev.groupby(chaves + ["agora"]).agg(ret=("bom", "mean"), n=("bom", "size")).reset_index()
    m = g[g["agora"]].merge(g[~g["agora"]], on=chaves, suffixes=("_a", "_b"))
    m = m[(m["n_a"] >= 3) & (m["n_b"] >= 3)]
    m["Tendência (pp)"] = ((m["ret_a"] - m["ret_b"]) * 100).round(0)
    return m[chaves + ["Tendência (pp)"]]


def _seta(v):
    if pd.isna(v):
        return "—"
    return f"▲ +{v:.0f}" if v >= 5 else f"▼ {v:.0f}" if v <= -5 else f"● {v:+.0f}"


CORES_CAIXA = ["#d64545", "#e88a3c", "#e0a526", "#8fbf4d", "#2e9e5b"]
MAX_BARRAS = 25  # assuntos por gráfico


def _aba_desempenho(db, login, baralhos):
    c, l = dados_termometro(db, login, baralhos)
    if c.empty:
        st.info("Sem cartões ainda. Importe ou crie cartões para ver o termômetro.")
        return
    hoje = _hoje()
    filtro = st.multiselect("Disciplinas", sorted(c["Disciplina"].unique()), key="fc_term_disc", placeholder="Todas",
                            help="Filtra todo o painel. Com uma disciplina só, os gráficos mostram os assuntos dela.")
    if filtro:
        c = c[c["Disciplina"].isin(filtro)]
        l = l[l["id"].isin(set(c["id"]))]
    varias = c["Disciplina"].nunique() > 1
    c = c.assign(Tópico=c["Assunto"] + " · " + c["Disciplina"] if varias else c["Assunto"])

    corte30, corte7 = (hoje - timedelta(days=30)).isoformat(), (hoje - timedelta(days=6)).isoformat()
    l30 = l[l["dia"] >= corte30]
    k = st.columns(5)
    k[0].metric("Retenção (30 dias)", f"{(~l30['nota'].isin(RUIM)).mean() * 100:.0f}%" if len(l30) else "—",
                help="Respostas Acertei/Fácil sobre o total nos últimos 30 dias.")
    k[1].metric("Respostas (7 dias)", int((l["dia"] >= corte7).sum()))
    k[2].metric("Sequência", f"{_sequencia(set(l['dia']))} dia(s)")
    k[3].metric("Dominados", f"{(c['caixa'] >= 4).mean() * 100:.0f}%", help="Cartões nas caixas 4 e 5.")
    k[4].metric("Vencidos hoje", int(c["venc"].sum()))

    ch = ["Disciplina", "Assunto", "Tópico"]
    por_ass = indicadores(c, l, ch).merge(tendencia(c, l, ch), on=ch, how="left")
    por_ass["Tendência"] = por_ass["Tendência (pp)"].map(_seta)
    barra = {col: st.column_config.ProgressColumn(col, format="%.0f", min_value=0, max_value=100)
             for col in ("Prioridade", "Retenção (%)", "Domínio (%)")}
    cols = ["Situação", "Prioridade", "Retenção (%)", "Domínio (%)", "Não sei (%)", "Vencidos (%)",
            "Cartões", "Respostas", "Base"]
    cont = por_ass["Situação"].value_counts()
    st.markdown("**Assuntos:** " + " · ".join(f"{f} {int(cont.get(f, 0))}" for f in FAIXAS))

    if varias:
        por_disc = indicadores(c, l, ["Disciplina"])
        with st.expander("🌡️ Visão por disciplina"):
            st.altair_chart(alt.Chart(por_disc.dropna(subset=["Prioridade"])).mark_bar().encode(
                x=alt.X("Prioridade:Q", scale=alt.Scale(domain=[0, 100]), title="Prioridade de reforço"),
                y=alt.Y("Disciplina:N", sort="-x", title=None),
                color=alt.Color("Situação:N", scale=_cor_faixa(), legend=None),
                tooltip=["Disciplina", "Prioridade", "Retenção (%)", "Domínio (%)", "Cartões"]))
            st.dataframe(por_disc[["Disciplina"] + cols], width="stretch", hide_index=True, column_config=barra)

    # ---- Termômetro por assunto ----
    st.markdown("#### 🌡️ Termômetro por assunto")
    medidos = por_ass.dropna(subset=["Prioridade"])
    topo = medidos.head(MAX_BARRAS)
    ordem = list(topo["Tópico"])
    if topo.empty:
        st.caption("Aparece depois das primeiras revisões.")
    else:
        st.altair_chart(alt.Chart(topo).mark_bar().encode(
            x=alt.X("Prioridade:Q", scale=alt.Scale(domain=[0, 100]), title="Prioridade de reforço"),
            y=alt.Y("Tópico:N", sort=ordem, title=None, axis=alt.Axis(labelLimit=320)),
            color=alt.Color("Situação:N", scale=_cor_faixa(), legend=alt.Legend(orient="bottom")),
            tooltip=["Assunto", "Disciplina", "Prioridade", "Retenção (%)", "Domínio (%)", "Não sei (%)",
                     "Vencidos (%)", "Cartões", "Tendência"]).properties(height=max(120, 26 * len(topo))))
        if len(medidos) > MAX_BARRAS:
            st.caption(f"Mostrando os {MAX_BARRAS} assuntos mais urgentes de {len(medidos)}. "
                       "Filtre por disciplina para ver os demais.")
    with st.expander("ℹ️ Como a prioridade é calculada"):
        st.markdown(
            "**Índice de prioridade de reforço (0 a 100)**, quanto maior, mais urgente:\n"
            "- **45% erro**: 100% − retenção. Usa as respostas dos últimos 30 dias quando há pelo menos "
            f"{MIN_RECENTE}; senão, o histórico do cartão (coluna *Base*).\n"
            "- **25% falta de domínio**: cartões nas primeiras caixas da repetição espaçada.\n"
            "- **15% 'não sei'**: não lembrar nada pesa mais do que lembrar errado.\n"
            "- **15% atraso**: cartões vencidos e não revisados esquecem rápido (curva do esquecimento).\n\n"
            "🔴 **Reforçar** ≥ 45 · 🟡 **Atenção** 25–44 · 🟢 **Em dia** < 25 · ⚪ ainda não revisado.\n\n"
            "**Tendência**: variação da retenção nos últimos 14 dias contra os 14 anteriores, em pontos "
            "percentuais (precisa de 3 respostas em cada período).")
    with st.expander("Tabela completa por assunto"):
        st.dataframe(por_ass[["Disciplina", "Assunto"] + cols[:2] + ["Tendência"] + cols[2:]], width="stretch",
                     hide_index=True, column_config=barra)

    # ---- Tópicos para reforçar ----
    st.markdown("#### 🎯 Tópicos para reforçar")
    alvo = por_ass[por_ass["Situação"].isin(["🔴 Reforçar", "🟡 Atenção"])].head(8)
    if alvo.empty:
        st.success("Nenhum assunto em alerta com esses filtros. 👏")
    for i, r in enumerate(alvo.itertuples()):
        a1, a2 = st.columns([5, 1])
        ret = "—" if pd.isna(r.ret) else f"{r.ret * 100:.0f}%"
        a1.markdown(f"{r.Situação} **{_esc(r.Assunto)}** · {_esc(r.Disciplina)}  \n"
                    f"prioridade {r.Prioridade:.0f} · retenção {ret} · tendência {r.Tendência} · "
                    f"{r.Cartões} cartões · {round(r.venc * r.Cartões)} vencidos")
        sel = c[c["Tópico"] == r.Tópico].sort_values(["caixa", "prox"])
        a2.button("▶ Treinar", key=f"fc_tr_{i}", width="stretch", on_click=_treinar,
                  args=(list(sel["id"]), r.Assunto))
    vermelhos = por_ass[por_ass["Situação"] == "🔴 Reforçar"]
    if len(vermelhos) > 1:
        ids = c[c["Tópico"].isin(set(vermelhos["Tópico"]))].sort_values("caixa")["id"]
        st.button(f"🔥 Treinar todos os {len(vermelhos)} assuntos em vermelho", type="primary",
                  on_click=_treinar, args=(list(ids), "assuntos em vermelho"))

    # ---- Estágio de aprendizado ----
    st.markdown("#### 📦 Estágio de aprendizado por assunto")
    st.caption("Em que caixa da repetição espaçada estão os cartões de cada assunto. Muito vermelho = ainda não "
               "fixou; muito verde = memória que já resistiu a intervalos longos.")
    ordem_cx = ordem or list(por_ass["Tópico"].head(MAX_BARRAS))
    cx = c[c["Tópico"].isin(ordem_cx)].groupby(["Tópico", "caixa"]).size().reset_index(name="Cartões")
    cx["Caixa"] = "Caixa " + cx["caixa"].astype(str)
    caixas = [f"Caixa {i}" for i in range(1, MAX_CAIXA + 1)]
    st.altair_chart(alt.Chart(cx).mark_bar().encode(
        x=alt.X("Cartões:Q", stack="normalize", axis=alt.Axis(format="%"), title="Cartões do assunto"),
        y=alt.Y("Tópico:N", sort=ordem_cx, title=None, axis=alt.Axis(labelLimit=320)),
        color=alt.Color("Caixa:N", scale=alt.Scale(domain=caixas, range=CORES_CAIXA),
                        legend=alt.Legend(orient="bottom", title=None)),
        order=alt.Order("caixa:Q"), tooltip=["Tópico", "Caixa", "Cartões"],
    ).properties(height=max(120, 26 * len(ordem_cx))))

    # ---- Mapa ----
    st.markdown("#### 🗺️ Mapa dos assuntos")
    st.caption("Canto inferior esquerdo = pouco domínio e baixa retenção: é onde está o maior retorno do estudo.")
    pontos = por_ass.dropna(subset=["Retenção (%)"])
    if not pontos.empty:
        st.altair_chart(alt.Chart(pontos).mark_circle(opacity=0.8).encode(
            x=alt.X("Domínio (%):Q", scale=alt.Scale(domain=[0, 100])),
            y=alt.Y("Retenção (%):Q", scale=alt.Scale(domain=[0, 100])),
            size=alt.Size("Cartões:Q", legend=None), color=alt.Color("Situação:N", scale=_cor_faixa(), legend=None),
            tooltip=["Disciplina", "Assunto", "Prioridade", "Retenção (%)", "Domínio (%)", "Cartões", "Tendência"]))

    # ---- Evolução ----
    st.markdown("#### 📈 Evolução semanal por assunto")
    ev = l.merge(c[["id", "Tópico"]], on="id")
    if ev.empty:
        st.caption("Aparece depois das primeiras revisões.")
    else:
        ev = ev.assign(Semana=pd.to_datetime(ev["dia"]).dt.to_period("W").dt.start_time, bom=~ev["nota"].isin(RUIM))
        mais = [t for t in ordem if t in set(ev["Tópico"])][:15] or list(ev["Tópico"].value_counts().index[:15])
        hm = ev[ev["Tópico"].isin(mais)].groupby(["Semana", "Tópico"], as_index=False).agg(
            Retenção=("bom", "mean"), Respostas=("bom", "size"))
        hm["Retenção"] = (hm["Retenção"] * 100).round(0)
        hm["Sem"] = hm["Semana"].dt.strftime("%d/%m")
        semanas = list(hm.sort_values("Semana")["Sem"].drop_duplicates())
        base = alt.Chart(hm).encode(x=alt.X("Sem:O", sort=semanas, title="Semana (início)"),
                                    y=alt.Y("Tópico:N", sort=mais, title=None, axis=alt.Axis(labelLimit=320)))
        st.altair_chart((base.mark_rect().encode(
            color=alt.Color("Retenção:Q", scale=alt.Scale(domain=[0, 100], scheme="redyellowgreen"), legend=None),
            tooltip=["Tópico", "Sem", "Retenção", "Respostas"]) + base.mark_text(fontSize=11).encode(
            text=alt.Text("Retenção:Q", format=".0f"))).properties(height=max(120, 28 * len(mais))))
        st.caption("Retenção (%) de cada assunto por semana, nos 15 mais urgentes. Linha que vai do vermelho ao "
                   "verde = o reforço está funcionando; em branco = semana sem revisão do assunto.")

    # ---- Raio-X ----
    st.markdown("#### 🔎 Raio-X de um assunto")
    topicos = list(por_ass["Tópico"])
    top = st.selectbox("Assunto", topicos, key="fc_raiox") if topicos else None
    if top:
        r = por_ass[por_ass["Tópico"] == top].iloc[0]
        ca = c[c["Tópico"] == top]
        fmt = lambda v, s="": "—" if pd.isna(v) else f"{v:.0f}{s}"
        m = st.columns(6)
        m[0].metric("Situação", r["Situação"].split(" ", 1)[1])
        m[1].metric("Prioridade", fmt(r["Prioridade"]))
        m[2].metric("Retenção", fmt(r["Retenção (%)"], "%"), None if pd.isna(r["Tendência (pp)"])
                    else f"{r['Tendência (pp)']:+.0f} pp")
        m[3].metric("Domínio", fmt(r["Domínio (%)"], "%"))
        m[4].metric("Cartões", int(r["Cartões"]))
        m[5].metric("Vencidos", int(ca["venc"].sum()))
        x1, x2 = st.columns(2)
        with x1:
            ea = l[l["id"].isin(set(ca["id"]))]
            if ea.empty:
                st.caption("Sem revisões registradas neste assunto ainda.")
            else:
                ea = ea.assign(Semana=pd.to_datetime(ea["dia"]).dt.to_period("W").dt.start_time,
                               bom=~ea["nota"].isin(RUIM))
                ea = ea.groupby("Semana", as_index=False).agg(Retenção=("bom", "mean"), Respostas=("bom", "size"))
                ea["Retenção"] = (ea["Retenção"] * 100).round(1)
                st.altair_chart(alt.Chart(ea).mark_line(point=True).encode(
                    x=alt.X("Semana:T", title=None),
                    y=alt.Y("Retenção:Q", scale=alt.Scale(domain=[0, 100]), title="Retenção (%)"),
                    tooltip=["Semana:T", "Retenção", "Respostas"]).properties(height=220))
        with x2:
            dist = ca.groupby("caixa").size().reindex(range(1, MAX_CAIXA + 1), fill_value=0).reset_index(name="Cartões")
            dist["Caixa"] = "Caixa " + dist["caixa"].astype(str)
            st.altair_chart(alt.Chart(dist).mark_bar().encode(
                x=alt.X("Caixa:N", sort=caixas, title=None), y="Cartões:Q",
                color=alt.Color("Caixa:N", scale=alt.Scale(domain=caixas, range=CORES_CAIXA), legend=None),
                tooltip=["Caixa", "Cartões"]).properties(height=220))
        det = ca.assign(Falhas=ca["e"] + ca["ns"]).sort_values(["caixa", "Falhas"], ascending=[True, False])
        st.dataframe(det[["Frente", "caixa", "a", "e", "ns", "prox"]].rename(columns={
            "caixa": "Caixa", "a": "Acertos", "e": "Erros", "ns": "Não sei", "prox": "Próxima revisão"}),
            width="stretch", hide_index=True)
        st.button(f"▶ Treinar este assunto ({len(det)} cartões)", key="fc_tr_raiox", on_click=_treinar,
                  args=(list(det["id"]), r["Assunto"]))

    # ---- Agenda e cartões-problema ----
    st.markdown("#### 📅 Revisões previstas (14 dias)")
    dias = [(hoje + timedelta(days=i)).isoformat() for i in range(14)]
    prev = pd.DataFrame({"Dia": pd.to_datetime(dias),
                         "Cartões": [int((c["prox"] <= dias[0]).sum())]
                         + [int((c["prox"] == d).sum()) for d in dias[1:]]})
    st.altair_chart(alt.Chart(prev).mark_bar().encode(
        x=alt.X("Dia:T", title=None), y="Cartões:Q", tooltip=["Dia:T", "Cartões"]).properties(height=200))
    st.caption("Hoje inclui os atrasados.")

    st.markdown("#### 🧱 Cartões-problema")
    prob = c[(c["e"] + c["ns"] >= 4) & (c["a"] < (c["e"] + c["ns"]))].assign(
        Falhas=lambda d: d["e"] + d["ns"]).sort_values("Falhas", ascending=False)
    if prob.empty:
        st.caption("Nenhum cartão com falhas repetidas. 👍")
    else:
        st.caption("Cartões que você erra mais do que acerta (4+ falhas). Costumam ser grandes demais ou ambíguos: "
                   "divida em cartões menores, reescreva ou guarde nos ⭐ favoritos.")
        st.dataframe(prob[["Disciplina", "Assunto", "Frente", "Falhas", "a", "caixa"]].rename(
            columns={"a": "Acertos", "caixa": "Caixa"}).head(20), width="stretch", hide_index=True)
        st.button(f"▶ Treinar os {min(len(prob), 30)} cartões-problema", key="fc_tr_prob", on_click=_treinar,
                  args=(list(prob["id"].head(30)), "cartões-problema"))


# ---- Importar ----
def _aba_importar(db, login, baralhos):
    st.markdown("Importe de **CSV**, **TXT** ou do **Anki** (exportar como *texto separado por tabulação*). "
                "Também dá para colar o texto direto.")
    with st.expander("📄 Ver modelo"):
        st.code(MODELO_IMPORTACAO, language="text")
        st.caption("Com cabeçalho, as colunas podem vir em qualquer ordem e só frente e verso são obrigatórias. "
                   "Sem cabeçalho, a ordem é: frente, verso, disciplina, assunto, baralho. "
                   "Sem a coluna baralho, o baralho recebe o nome da disciplina. "
                   "Separadores aceitos: ponto e vírgula, tabulação, barra vertical ou vírgula. "
                   "Cartões com a mesma frente no mesmo baralho são ignorados.")

    up = st.file_uploader("Arquivo", type=["csv", "txt", "tsv"], key="fc_up")
    colado = st.text_area("... ou cole aqui (uma linha por cartão)", height=150, key="fc_colar",
                          placeholder="Pergunta;Resposta;Disciplina;Assunto\nOutra pergunta;Outra resposta")
    conteudo = up.getvalue() if up is not None else colado
    if conteudo and _decodificar(conteudo).strip():
        txt = _decodificar(conteudo)
        auto = detectar_delimitador(txt)
        opcoes = list(DELIMITADORES)
        delim = st.selectbox("Separador", opcoes, index=opcoes.index(auto), format_func=DELIMITADORES.get,
                             key="fc_delim", help="Detectado automaticamente; troque se a prévia estiver errada.")
        linhas, erros = interpretar_importacao(txt, delim)

        tem_bar = any(l["baralho"] or l["disciplina"] for l in linhas)
        usar_col = "(usar as colunas do arquivo)"
        destinos = ([usar_col] if tem_bar else []) + [b["nome"] for b in baralhos] + ["➕ Novo baralho…"]
        d1, d2 = st.columns(2)
        with d1:
            destino = st.selectbox("Baralho de destino", destinos,
                                   help="Com as colunas do arquivo, cada linha vai para o baralho da coluna "
                                        "'baralho' (ou, sem ela, para um baralho com o nome da disciplina).")
            nome_novo = st.text_input("Nome do novo baralho", key="fc_destino_novo") \
                if destino == "➕ Novo baralho…" else ""
        with d2:
            disc_forcada = ""
            if destino != usar_col:
                atual = next((b["disciplina"] for b in baralhos if b["nome"] == destino), "")
                disc_forcada = _escolher_texto("Disciplina do baralho", disciplinas_sugeridas(db, login),
                                               atual=atual, key=f"fc_imp_disc_{destino}",
                                               help="Vazio = usar a coluna 'disciplina' do arquivo, se houver.")
            assunto_padrao = st.text_input("Assunto para linhas sem assunto (opcional)", key="fc_imp_ass")

        st.caption(f"Prévia: {len(linhas)} cartão(ões) válidos" + (f" · {len(erros)} linha(s) com problema" if erros else ""))
        if linhas:
            prev = pd.DataFrame(linhas[:10]).rename(columns=str.capitalize)
            st.dataframe(prev.loc[:, (prev != "").any()], width="stretch", hide_index=True)
        if erros:
            with st.expander(f"⚠️ {len(erros)} linha(s) ignoradas"):
                for e in erros[:100]:
                    st.write("- ", e)

        pode = bool(linhas) and (destino != "➕ Novo baralho…" or nome_novo.strip())
        if st.button("📥 Importar cartões", type="primary", disabled=not pode, key="fc_imp_btn"):
            alvo = None if destino == usar_col else (nome_novo if destino == "➕ Novo baralho…" else destino)
            resumo = importar_linhas(db, login, linhas, alvo, disc_forcada or None, assunto_padrao)
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
    st.caption("Copia os flashcards do plano mantendo a organização: um baralho por disciplina do plano, "
               "com o assunto de cada item. Os originais continuam no plano; cartões repetidos são ignorados.")
    sel = st.multiselect("Planos", list(do_plano), default=list(do_plano),
                         format_func=lambda p: f"{p} ({len(do_plano[p])} cartões)", key="fc_planos")
    if st.button("Copiar para baralhos", key="fc_copiar_plano", disabled=not sel):
        resumo = importar_linhas(db, login, [l for p in sel for l in do_plano[p]])
        st.session_state["_fc_msg"] = "Copiados: " + " · ".join(f"{n}: {i} novo(s)" for n, (i, _, _) in resumo.items())
        st.rerun()
