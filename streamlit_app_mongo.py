import os
import random
from datetime import datetime, timezone
from functools import lru_cache

import streamlit as st
import pandas as pd

from pymongo import MongoClient, ASCENDING, DESCENDING
from bson import ObjectId

# =============================
# Config & Globals
# =============================
MONGO_URI = st.secrets.get("MONGO_URI", os.environ.get("MONGO_URI", ""))
MONGO_DB_NAME = st.secrets.get("MONGO_DB_NAME", os.environ.get("MONGO_DB_NAME", "quiz_app"))

st.set_page_config(page_title="Estudos | Questionários & Simulados", layout="wide")

# =============================
# Estilo customizado (layout mais moderno e leve)
# =============================
def apply_custom_style():
    st.markdown(
        """
        <style>
        /* Fundo claro, sem tema escuro */
        .stApp {
            background-color: #E2E2E2; /* Platinum */
            background-image:
                radial-gradient(circle at 0% 0%, #D1E8E2 0, transparent 55%),
                radial-gradient(circle at 100% 0%, #A9D6E5 0, transparent 55%);
            color: #222222;
        }

        h1, h2, h3, h4, h5 {
            color: #19747E; /* Dark Cyan */
        }

        /* Botões com a paleta nova */
        .stButton>button {
            border-radius: 999px;
            border: 1px solid #19747E;
            background: linear-gradient(90deg, #19747E, #A9D6E5);
            color: #ffffff;
            padding: 0.35rem 1.1rem;
            font-weight: 500;
        }
        .stButton>button:hover {
            filter: brightness(1.05);
            border-color: #19747E;
        }

        /* Campos de entrada claros */
        .stTextInput>div>div>input,
        .stTextArea>div>textarea,
        .stSelectbox>div>div>select,
        .stNumberInput>div>input {
            background-color: #ffffff !important;
            color: #222222 !important;
            border-radius: 8px;
            border: 1px solid #A9D6E5 !important; /* Light Blue */
        }

        /* Labels e pequenos textos */
        label, .css-10trblm, .css-16idsys, .stMarkdown {
            color: #222222;
        }

        /* Cards/containers com borda suave */
        [data-testid="stVerticalBlock"] > div[style*="border"],
        .stContainer {
            border-radius: 12px !important;
        }

        /* Métricas com cor de destaque */
        [data-testid="stMetricValue"] {
            color: #19747E; /* Dark Cyan */
        }

        /* Progress bar com cores da paleta */
        .stProgress > div > div > div {
            background: linear-gradient(90deg, #19747E, #D1E8E2);
        }

        /* Expander com fundo levemente mint */
        details {
            background-color: #D1E8E2 !important; /* Soft Mint Green */
            border-radius: 10px;
        }

        /* Remover fundo muito escuro de radios/checkboxes, deixar padrão claro */
        div[role="radiogroup"] label, div[role="checkbox"] label {
            color: #222222 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

apply_custom_style()

# =============================
# Connection Management (otimizado)
# =============================
@st.cache_resource
def get_mongo_client():
    """Cria e cacheia a conexão MongoDB para reutilização"""
    if not MONGO_URI:
        return None
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)

def get_db():
    client = get_mongo_client()
    if client is None:
        raise ValueError("MongoDB URI não configurado")
    return client[MONGO_DB_NAME]

# Verificação simplificada de conexão (apenas uma vez)
def connection_status():
    with st.sidebar:
        st.caption("⚙️ Conexão MongoDB")
        if not MONGO_URI:
            st.error("MONGO_URI não definido em Secrets/Env.")
            return False
        try:
            client = get_mongo_client()
            if client is None:
                st.error("Cliente MongoDB não disponível")
                return False
            client.admin.command("ping")
            st.success("MongoDB conectado ✅")
            return True
        except Exception as e:
            st.error(f"Falha de conexão: {e}")
            return False

# =============================
# Database helpers (otimizado)
# =============================
def init_db():
    """Inicializa índices e questionários especiais (executado apenas uma vez)"""
    db = get_db()
    try:
        # Índices
        db.questionarios.create_index([("nome", ASCENDING)], name="uq_nome", unique=True, background=True)
        db.questionarios.create_index([("disciplina", ASCENDING), ("nome", ASCENDING)], name="idx_disciplina_nome", background=True)
        db.questoes.create_index([("questionario_id", ASCENDING)], background=True)
        db.respostas.create_index([("questionario_id", ASCENDING)], background=True)
        db.respostas.create_index([("questao_id", ASCENDING)], background=True)

        # Garante existência dos cadernos especiais (com disciplina do sistema)
        if db.questionarios.count_documents({"nome": "Favoritos"}, limit=1) == 0:
            db.questionarios.insert_one({
                "nome": "Favoritos",
                "descricao": "Questões salvas como favoritas.",
                "disciplina": "— Sistema —",
                "created_at": datetime.now(timezone.utc).isoformat()
            })
        if db.questionarios.count_documents({"nome": "Caderno de Erros"}, limit=1) == 0:
            db.questionarios.insert_one({
                "nome": "Caderno de Erros",
                "descricao": "Questões respondidas incorretamente.",
                "disciplina": "— Sistema —",
                "created_at": datetime.now(timezone.utc).isoformat()
            })
        # Atualiza documentos antigos sem 'disciplina'
        db.questionarios.update_many(
            {"disciplina": {"$exists": False}},
            {"$set": {"disciplina": "Sem Disciplina"}}
        )
    except Exception:
        # Silencia erros de índice já existente
        pass

def _doc_to_row_q(q):
    """Converte questionário Mongo -> dict (id:str)."""
    return {
        "id": str(q["_id"]),
        "nome": q.get("nome",""),
        "descricao": q.get("descricao",""),
        "disciplina": q.get("disciplina", "Sem Disciplina")
    }

def _doc_to_row_questao(d):
    return {
        "id": str(d["_id"]),
        "questionario_id": str(d["questionario_id"]),
        "tipo": d["tipo"],
        "texto": d["texto"],
        "explicacao": d.get("explicacao",""),
        "correta_text": d["correta_text"],
        "op_a": d.get("op_a"),
        "op_b": d.get("op_b"),
        "op_c": d.get("op_c"),
        "op_d": d.get("op_d"),
        "op_e": d.get("op_e"),
        "created_at": d.get("created_at"),
    }

# Cache de questionários para melhor performance
@st.cache_data(ttl=10)
def get_questionarios():
    db = get_db()
    try:
        cur = db.questionarios.find({}).sort([("disciplina", ASCENDING), ("nome", ASCENDING)])
        return [_doc_to_row_q(x) for x in cur]
    except Exception as e:
        st.error(f"[get_questionarios] erro: {e}")
        return []

def get_all_disciplinas():
    """Lista de disciplinas existentes (ordenadas)"""
    db = get_db()
    try:
        vals = db.questionarios.distinct("disciplina")
        vals = [v or "Sem Disciplina" for v in vals]
        # Garante ordenação, com '— Sistema —' no fim
        base = sorted([v for v in vals if v != "— Sistema —" and v is not None])
        if "— Sistema —" in vals:
            base.append("— Sistema —")
        return base or ["Sem Disciplina"]
    except Exception:
        return ["Sem Disciplina"]

def get_questionario_by_name(name):
    db = get_db()
    q = db.questionarios.find_one({"nome": name})
    return _doc_to_row_q(q) if q else None

def add_questionario(nome, descricao="", disciplina="Sem Disciplina"):
    db = get_db()
    res = db.questionarios.insert_one({
        "nome": nome,
        "descricao": descricao,
        "disciplina": disciplina or "Sem Disciplina",
        "created_at": datetime.now(timezone.utc).isoformat()
    })
    get_questionarios.clear()
    return str(res.inserted_id)

def update_questionario_disciplina(qid, disciplina):
    db = get_db()
    db.questionarios.update_one({"_id": ObjectId(qid)}, {"$set": {"disciplina": disciplina or "Sem Disciplina"}})
    get_questionarios.clear()

def update_questionario_descricao(qid, descricao):
    db = get_db()
    db.questionarios.update_one({"_id": ObjectId(qid)}, {"$set": {"descricao": descricao or ""}})
    get_questionarios.clear()

def delete_questionario(qid):
    db = get_db()
    oid = ObjectId(qid)
    db.questoes.delete_many({"questionario_id": oid})
    db.respostas.delete_many({"questionario_id": oid})
    db.questionarios.delete_one({"_id": oid})
    get_questionarios.clear()

def resetar_resolucoes(qid):
    """Remove histórico de respostas para o questionário e reinicia sessão atual."""
    db = get_db()
    oid = ObjectId(qid)
    db.respostas.delete_many({"questionario_id": oid})
    # Limpa chaves de estado relacionadas
    keys_to_del = [
        k for k in st.session_state.keys()
        if any(
            k.startswith(prefix)
            for prefix in (
                "answered_",
                "result_",
                "vf_",
                "mc_",
                f"pool_{qid}",
                f"idx_{qid}",
            )
        )
    ]
    for k in keys_to_del:
        del st.session_state[k]
    st.toast("Resoluções resetadas para este questionário.")

def add_questao_vf(questionario_id, texto, correta, explicacao=""):
    correta_text = "V" if bool(correta) else "F"
    db = get_db()
    db.questoes.insert_one({
        "questionario_id": ObjectId(questionario_id),
        "tipo": "VF",
        "texto": texto,
        "explicacao": explicacao,
        "correta_text": correta_text,
        "created_at": datetime.now(timezone.utc).isoformat()
    })

def add_questao_mc(questionario_id, texto, alternativas, correta_letra, explicacao=""):
    op = alternativas + [None] * (5 - len(alternativas))
    correta_letra = str(correta_letra).upper().strip()
    letras_validas = list("ABCDE")[:len(alternativas)]
    if correta_letra not in letras_validas:
        idx = None
        for i, alt in enumerate(alternativas):
            if alt and str(alt).strip().lower() == correta_letra.strip().lower():
                idx = i
                break
        if idx is None:
            raise ValueError("Resposta correta inválida para questão MC.")
        correta_letra = "ABCDE"[idx]

    db = get_db()
    db.questoes.insert_one({
        "questionario_id": ObjectId(questionario_id),
        "tipo": "MC",
        "texto": texto,
        "explicacao": explicacao,
        "correta_text": correta_letra,
        "op_a": op[0], "op_b": op[1], "op_c": op[2], "op_d": op[3], "op_e": op[4],
        "created_at": datetime.now(timezone.utc).isoformat()
    })

def get_questoes(questionario_id):
    db = get_db()
    qid = ObjectId(questionario_id)
    return [_doc_to_row_questao(x) for x in db.questoes.find({"questionario_id": qid}).sort("_id", ASCENDING)]

def get_questao_by_id(questao_id):
    db = get_db()
    d = db.questoes.find_one({"_id": ObjectId(questao_id)})
    return _doc_to_row_questao(d) if d else None

def get_random_questoes(questionario_ids, n):
    db = get_db()
    oids = [ObjectId(x) for x in questionario_ids]
    pipeline = [
        {"$match": {"questionario_id": {"$in": oids}}},
        {"$sample": {"size": int(n)}},
    ]
    return [_doc_to_row_questao(x) for x in db.questoes.aggregate(pipeline)]

def save_resposta(questionario_id, questao_id, correto):
    db = get_db()
    db.respostas.insert_one({
        "questionario_id": ObjectId(questionario_id),
        "questao_id": ObjectId(questao_id),
        "correto": 1 if correto else 0,
        "respondido_em": datetime.now(timezone.utc).isoformat()
    })

def _last_correct_map(respostas):
    """Mapeia questao_id -> bool (se última resposta foi correta)."""
    last = {}
    for r in sorted(respostas, key=lambda x: x.get("respondido_em","")):
        last[str(r["questao_id"])] = bool(r.get("correto",0))
    return last

def desempenho_questionario(questionario_id):
    """Retorna: total, corretas (última resposta correto), perc"""
    db = get_db()
    qid = ObjectId(questionario_id)
    total = db.questoes.count_documents({"questionario_id": qid})
    respostas = list(db.respostas.find({"questionario_id": qid}))
    last_map = _last_correct_map(respostas)
    acertos = sum(1 for v in last_map.values() if v)
    perc = (acertos/total)*100 if total > 0 else 0.0
    return total, acertos, perc

def respondidas_questionario(questionario_id):
    db = get_db()
    qid = ObjectId(questionario_id)
    return len({str(r["questao_id"]) for r in db.respostas.find({"questionario_id": qid}, {"questao_id":1})})

def duplicar_questao_para_favoritos(questao_id):
    db = get_db()
    fav = db.questionarios.find_one({"nome":"Favoritos"})
    if not fav:
        init_db()
        fav = db.questionarios.find_one({"nome":"Favoritos"})
    d = db.questoes.find_one({"_id": ObjectId(questao_id)})
    if not d:
        return False
    db.questoes.insert_one({
        "questionario_id": fav["_id"],
        "tipo": d["tipo"],
        "texto": d["texto"],
        "explicacao": d.get("explicacao",""),
        "correta_text": d["correta_text"],
        "op_a": d.get("op_a"), "op_b": d.get("op_b"), "op_c": d.get("op_c"),
        "op_d": d.get("op_d"), "op_e": d.get("op_e"),
        "created_at": datetime.now(timezone.utc).isoformat()
    })
    return True

def duplicar_questao_para_erros(questao_id):
    db = get_db()
    erros = db.questionarios.find_one({"nome":"Caderno de Erros"})
    if not erros:
        init_db()
        erros = db.questionarios.find_one({"nome":"Caderno de Erros"})
    d = db.questoes.find_one({"_id": ObjectId(questao_id)})
    if not d:
        return False
    
    existe = db.questoes.find_one({
        "questionario_id": erros["_id"],
        "texto": d["texto"]
    })
    if existe:
        return False
    
    db.questoes.insert_one({
        "questionario_id": erros["_id"],
        "tipo": d["tipo"],
        "texto": d["texto"],
        "explicacao": d.get("explicacao",""),
        "correta_text": d["correta_text"],
        "op_a": d.get("op_a"), "op_b": d.get("op_b"), "op_c": d.get("op_c"),
        "op_d": d.get("op_d"), "op_e": d.get("op_e"),
        "created_at": datetime.now(timezone.utc).isoformat()
    })
    return True

def update_questao_explicacao(questao_id, texto_exp):
    db = get_db()
    db.questoes.update_one({"_id": ObjectId(questao_id)}, {"$set": {"explicacao": texto_exp}})

def popular_caderno_erros():
    """Popula o Caderno de Erros com questões já respondidas incorretamente"""
    db = get_db()
    erros = db.questionarios.find_one({"nome": "Caderno de Erros"})
    if not erros:
        init_db()
        erros = db.questionarios.find_one({"nome": "Caderno de Erros"})
    respostas = list(db.respostas.find({}))
    last_map = _last_correct_map(respostas)
    adicionadas = 0
    for questao_id_str, correto in last_map.items():
        if not correto:
            questao = db.questoes.find_one({"_id": ObjectId(questao_id_str)})
            if questao:
                existe = db.questoes.find_one({
                    "questionario_id": erros["_id"],
                    "texto": questao["texto"]
                })
                if not existe:
                    db.questoes.insert_one({
                        "questionario_id": erros["_id"],
                        "tipo": questao["tipo"],
                        "texto": questao["texto"],
                        "explicacao": questao.get("explicacao", ""),
                        "correta_text": questao["correta_text"],
                        "op_a": questao.get("op_a"),
                        "op_b": questao.get("op_b"),
                        "op_c": questao.get("op_c"),
                        "op_d": questao.get("op_d"),
                        "op_e": questao.get("op_e"),
                        "created_at": datetime.now(timezone.utc).isoformat()
                    })
                    adicionadas += 1
    return adicionadas

# =============================
# CSV Import
# =============================
TEMPLATE_DOC = """
FORMATO CSV SUPORTADO (delimitador vírgula ou ponto e vírgula)

Colunas mínimas (ordem livre, cabeçalho obrigatório):
- tipo                -> 'VF' ou 'MC'
- questionario        -> nome do questionário (será criado se não existir)
- texto               -> enunciado da questão
- correta             -> VF: 'V', 'F', 'True', 'False'; MC: 'A'..'E' OU o texto exato da alternativa correta
- explicacao          -> (opcional)
- alternativas        -> (apenas MC) string com alternativas separadas por '@@', na ordem A..E
- disciplina          -> (opcional) nome da disciplina para classificar o questionário
"""

def normalize_bool(val):
    if isinstance(val, (bool, int)):
        return bool(val)
    s = str(val).strip().lower()
    return s in ("v","true","t","1","sim","s","verdadeiro")

def parse_alternativas(val):
    if val is None:
        return []
    s = str(val).strip()
    parts = [p.strip() for p in s.split("@@") if p.strip()]
    if len(parts) > 5:
        parts = parts[:5]
    return parts

def ensure_questionario(nome, disciplina="Sem Disciplina"):
    nome = str(nome).strip() or "Sem Título"
    q = get_questionario_by_name(nome)
    if q:
        # Se já existe mas sem disciplina setada, não mexe; se quiser reclassificar, faz pela UI
        return q["id"]
    return add_questionario(nome, "", disciplina=disciplina)

def processar_texto(texto):
    """Converte \\n em quebras de linha reais"""
    if texto:
        return str(texto).replace('\\n', '\n')
    return texto

def import_csv_to_db(filelike_or_str):
    import io, csv
    if hasattr(filelike_or_str, "read"):
        content = filelike_or_str.read()
        try: txt = content.decode("utf-8")
        except Exception: txt = content.decode("latin-1")
    else:
        txt = str(filelike_or_str)

    sample = txt[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,")
        delimiter = dialect.delimiter
    except Exception:
        delimiter = ";" if ";" in sample else ","

    reader = csv.DictReader(io.StringIO(txt), delimiter=delimiter)
    required = {"tipo", "questionario", "texto", "correta"}
    missing = [r for r in required if r not in reader.fieldnames]
    if missing:
        raise ValueError(f"CSV sem colunas obrigatórias: {missing}. Cabeçalho encontrado: {reader.fieldnames}")

    ok, erros = 0, []
    for i, row in enumerate(reader, start=2):
        try:
            tipo = str(row.get("tipo","")).strip().upper()
            questionario = row.get("questionario","").strip() or "Sem Título"
            disciplina_csv = (row.get("disciplina") or "").strip() or "Sem Disciplina"
            texto = processar_texto(row.get("texto","").strip())
            correta = row.get("correta","").strip()
            explicacao = processar_texto(row.get("explicacao","") or "")

            if not texto:
                raise ValueError("Texto da questão vazio.")

            qid = ensure_questionario(questionario, disciplina_csv)

            if tipo == "VF":
                val = normalize_bool(correta)
                add_questao_vf(qid, texto, val, explicacao)
                ok += 1
            elif tipo == "MC":
                alternativas_raw = parse_alternativas(row.get("alternativas",""))
                alternativas = [processar_texto(alt) for alt in alternativas_raw]
                if len(alternativas) < 2:
                    raise ValueError("Questão MC requer ao menos 2 alternativas.")
                add_questao_mc(qid, texto, alternativas, correta, explicacao)
                ok += 1
            else:
                raise ValueError("tipo deve ser 'VF' ou 'MC'.")
        except Exception as e:
            erros.append(f"Linha {i}: {e}")

    get_questionarios.clear()
    return ok, erros

# =============================
# UI Helpers
# =============================
def show_desempenho_block(qid, show_respondidas=False):
    total, acertos, perc = desempenho_questionario(qid)
    cols = st.columns([1,1,1,2]) if show_respondidas else st.columns([1,1,2])
    if show_respondidas:
        c1, c2, c3, c4 = cols
        with c1:
            st.metric("Total", total)
        with c2:
            st.metric("Respondidas", respondidas_questionario(qid))
        with c3:
            st.metric("Corretas", acertos)
        with c4:
            st.progress(int(perc), text=f"Aproveitamento: {perc:.1f}%")
    else:
        c1, c2, c3 = cols
        with c1:
            st.metric("Total", total)
        with c2:
            st.metric("Corretas", acertos)
        with c3:
            st.progress(int(perc), text=f"Aproveitamento: {perc:.1f}%")

def render_questao(q_row, parent_qid, questao_numero=None):
    """
    Renderiza uma questão individual na página Praticar.

    - Para VF: comportamento normal.
    - Para MC:
        * Mostra um "rascunho" clicável para riscar/destacar alternativas.
        * Esse rascunho NÃO conta como resposta e não vai para o banco.
        * A resposta oficial continua sendo o radio abaixo.
    """
    qid = q_row["id"]
    tipo = q_row["tipo"]
    answered_key = f"answered_{qid}"
    result_key = f"result_{qid}"

    if questao_numero:
        st.markdown(f"#### Questão {questao_numero}")
    st.markdown(f"**{q_row['texto']}**")

    # ======================
    # QUESTÃO VERDADEIRO/FALSO
    # ======================
    if tipo == "VF":
        vf_options = ["— Selecione —", "Verdadeiro", "Falso"]
        escolha = st.radio("Sua resposta", vf_options, key=f"vf_{qid}", index=0)
        if answered_key not in st.session_state and escolha != "— Selecione —":
            gabarito = (q_row["correta_text"] == "V")
            user = (escolha == "Verdadeiro")
            is_correct = (gabarito == user)
            st.session_state[answered_key] = True
            st.session_state[result_key] = is_correct
            save_resposta(parent_qid, qid, is_correct)
            if not is_correct:
                duplicar_questao_para_erros(qid)

    # ======================
    # QUESTÃO MÚLTIPLA ESCOLHA (MC)
    # ======================
    else:
        alternativas = [q_row["op_a"], q_row["op_b"], q_row["op_c"], q_row["op_d"], q_row["op_e"]]
        letras = ["A", "B", "C", "D", "E"]
        opts = [(letras[i], alt) for i, alt in enumerate(alternativas) if alt]

        # ----- BLOCO DE RASCUNHO (riscar alternativas) -----
        st.caption("Clique para riscar mentalmente alternativas (não conta como resposta):")

        for letra, alt in opts:
            strike_key = f"strike_{qid}_{letra}"
            if strike_key not in st.session_state:
                st.session_state[strike_key] = False

            col_cb, col_txt = st.columns([0.08, 0.92])
            with col_cb:
                marcado = st.checkbox("", key=strike_key, value=st.session_state[strike_key])
                st.session_state[strike_key] = marcado

            with col_txt:
                if st.session_state[strike_key]:
                    # alternativa riscada
                    st.markdown(
                        f"<span style='text-decoration: line-through; color: #6b7280;'>{letra}) {alt}</span>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(f"{letra}) {alt}")

        st.markdown("---")

        # ----- RESPOSTA OFICIAL (que realmente conta) -----
        labels = ["— Selecione —"] + [f"{letra}) {alt}" for letra, alt in opts]
        escolha = st.radio(
            "Escolha uma alternativa (resposta oficial)",
            labels,
            key=f"mc_{qid}",
            index=0,
        )

        if answered_key not in st.session_state and escolha != "— Selecione —":
            letra_escolhida = escolha.split(")")[0].strip()
            is_correct = (letra_escolhida == q_row["correta_text"])
            st.session_state[answered_key] = True
            st.session_state[result_key] = is_correct
            save_resposta(parent_qid, qid, is_correct)
            if not is_correct:
                duplicar_questao_para_erros(qid)

    # ======================
    # FEEDBACK ACERTO / ERRO
    # ======================
    if st.session_state.get(answered_key):
        if st.session_state.get(result_key):
            st.success("✅ Você acertou esta questão.")
        else:
            st.error(f"❌ Você errou esta questão. Gabarito: {q_row['correta_text']}")

    # ======================
    # EXPLICAÇÃO (sempre aberta, altura fixa)
    # ======================
    with st.expander("Ver explicação / editar", expanded=True):
        exp_key = f"exp_{qid}"
        explicacao_atual = q_row.get("explicacao", "")
        new_exp = st.text_area(
            "Texto da explicação:",
            value=explicacao_atual,
            key=exp_key,
            height=180,
        )
        if st.button("Salvar explicação", key=f"save_exp_{qid}"):
            update_questao_explicacao(qid, new_exp)
            st.toast("Explicação atualizada.")

    # Botão de favoritos
    if st.button("⭐ Salvar nos Favoritos", key=f"fav_{qid}"):
        if duplicar_questao_para_favoritos(qid):
            st.toast("Adicionada em 'Favoritos'.")

    st.divider()

# =============================
# Páginas
# =============================
def page_dashboard():
    st.title("📚 Painel de Questionários (Agrupado por Disciplina)")

    # Botão para atualizar Caderno de Erros com histórico
    if st.button("📔 Atualizar Caderno de Erros com histórico"):
        with st.spinner("Analisando respostas anteriores..."):
            n = popular_caderno_erros()
            if n > 0:
                st.success(f"✅ {n} questões erradas adicionadas ao Caderno de Erros!")
            else:
                st.info("Nenhuma questão nova para adicionar.")

    st.divider()

    # Caderno de Erros fixado no topo do painel
    all_qs = get_questionarios()
    caderno_erros = next((q for q in all_qs if q["nome"] == "Caderno de Erros"), None)
    if caderno_erros:
        with st.container(border=True):
            st.subheader("🧨 Caderno de Erros (fixado)")
            show_desempenho_block(caderno_erros["id"], show_respondidas=True)
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Praticar Caderno de Erros", key="pr_erros"):
                    st.session_state["current_qid"] = caderno_erros["id"]
                    st.session_state["go_to"] = "Praticar"
                    st.rerun()
            with c2:
                if st.button("Gerenciar Caderno de Erros", key="ger_erros"):
                    st.session_state["current_qid"] = caderno_erros["id"]
                    st.session_state["go_to"] = "Gerenciar"
                    st.rerun()

    st.divider()

    # Demais questionários (sem Caderno de Erros e sem Favoritos)
    qs = [q for q in all_qs if q["nome"] not in ("Caderno de Erros", "Favoritos")]
    if not qs:
        st.info("Nenhum questionário cadastrado ainda. Vá em **Importar CSV** para começar.")
        return

    filtro_global = st.text_input("🔎 Buscar por nome de questionário (filtra dentro dos dropdowns)")

    # Agrupa por disciplina
    grupos = {}
    for q in qs:
        grupos.setdefault(q["disciplina"] or "Sem Disciplina", []).append(q)

    # Render: um card por disciplina, com estatísticas agregadas + dropdown
    for disc, items in sorted(grupos.items()):
        with st.container(border=True):
            st.subheader(f"📦 {disc}")

            # Estatísticas agregadas da disciplina (soma de todos os cadernos daquela disciplina)
            total_disc = 0
            acertos_disc = 0
            respondidas_disc = 0
            for it in items:
                t, a, _ = desempenho_questionario(it["id"])
                total_disc += t
                acertos_disc += a
                respondidas_disc += respondidas_questionario(it["id"])
            perc_disc = (acertos_disc / total_disc) * 100 if total_disc > 0 else 0.0

            col1, col2, col3, col4 = st.columns([1, 1, 1, 2])
            with col1:
                st.metric("Total (disciplina)", total_disc)
            with col2:
                st.metric("Respondidas (disciplina)", respondidas_disc)
            with col3:
                st.metric("Corretas (disciplina)", acertos_disc)
            with col4:
                st.progress(int(perc_disc), text=f"Aproveitamento da disciplina: {perc_disc:.1f}%")

            st.markdown("---")

            # Filtro por nome dentro da disciplina
            nomes_validos = [
                i["nome"]
                for i in items
                if (not filtro_global or filtro_global.lower() in i["nome"].lower())
            ]
            if not nomes_validos:
                st.caption("Nenhum questionário correspondente ao filtro.")
                continue

            sel = st.selectbox(
                f"Selecione um questionário de {disc}",
                nomes_validos,
                key=f"sel_{disc}",
            )
            escolhido = next((x for x in items if x["nome"] == sel), None)
            if escolhido:
                show_desempenho_block(escolhido["id"])
                b1, b2, b3, b4 = st.columns(4)
                with b1:
                    if st.button("Praticar", key=f"pr_{escolhido['id']}"):
                        st.session_state["current_qid"] = escolhido["id"]
                        st.session_state["go_to"] = "Praticar"
                        st.rerun()
                with b2:
                    if st.button("Gerenciar", key=f"ger_{escolhido['id']}"):
                        st.session_state["current_qid"] = escolhido["id"]
                        st.session_state["go_to"] = "Gerenciar"
                        st.rerun()
                with b3:
                    if st.button("Resetar resoluções", key=f"reset_{escolhido['id']}"):
                        resetar_resolucoes(escolhido["id"])
                        st.rerun()
                with b4:
                    if st.button("Excluir", key=f"del_{escolhido['id']}"):
                        delete_questionario(escolhido["id"])
                        st.success(f"Questionário '{escolhido['nome']}' excluído.")
                        st.rerun()

def page_praticar():
    st.title("🎯 Praticar")
    qs = get_questionarios()
    # Pode praticar Favoritos, mas não o Caderno de Erros automaticamente aqui
    qs = [q for q in qs if q["nome"] != "Caderno de Erros"]
    if not qs:
        st.info("Nenhum questionário cadastrado.")
        return

    nomes = {q["nome"]: q["id"] for q in qs}
    default_id = st.session_state.get("current_qid")
    default_name = None
    if default_id:
        for name, _id in nomes.items():
            if _id == default_id:
                default_name = name
                break

    escolha = st.selectbox(
        "Selecione um questionário",
        list(nomes.keys()),
        index=(list(nomes.keys()).index(default_name) if default_name in nomes else 0),
    )
    qid = nomes[escolha]
    st.session_state["current_qid"] = qid

    # Ações rápidas
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔄 Resetar resoluções deste questionário"):
            resetar_resolucoes(qid)
            st.rerun()
    with c2:
        st.caption("O reset remove apenas o histórico de respostas. As questões permanecem.")

    # Cabeçalho de desempenho
    st.subheader("Desempenho")
    show_desempenho_block(qid, show_respondidas=True)

    # Estado de navegação: lista fixa de questões + índice atual
    key_pool = f"pool_{qid}"
    key_idx = f"idx_{qid}"

    if key_pool not in st.session_state:
        # Embaralha apenas uma vez por questionário
        st.session_state[key_pool] = [r["id"] for r in get_questoes(qid)]
        random.shuffle(st.session_state[key_pool])

    pool = st.session_state[key_pool]
    if not pool:
        st.info("Acabaram as questões! Você pode **resetar resoluções** para reiniciar.")
        return

    # Garante índice válido
    st.session_state.setdefault(key_idx, 0)
    idx = st.session_state[key_idx]
    idx = max(0, min(idx, len(pool) - 1))
    st.session_state[key_idx] = idx

    current_qid = pool[idx]
    row = get_questao_by_id(current_qid)
    total_questoes = len(pool)
    questao_numero = idx + 1

    # Navegação: voltar / avançar + indicador da posição
    nav_col1, nav_col2, nav_col3 = st.columns([1, 1, 2])
    with nav_col1:
        if st.button("◀ Questão anterior", key="prev_top", disabled=(idx == 0)):
            st.session_state[key_idx] = max(0, idx - 1)
            st.rerun()
    with nav_col2:
        if st.button("Próxima questão ▶", key="next_top", disabled=(idx >= total_questoes - 1)):
            st.session_state[key_idx] = min(total_questoes - 1, idx + 1)
            st.rerun()
    with nav_col3:
        st.markdown(f"**Questão {questao_numero} de {total_questoes}**")

    # Render da questão atual
    render_questao(row, parent_qid=qid, questao_numero=questao_numero)

    st.subheader("Desempenho (atualizado)")
    show_desempenho_block(qid, show_respondidas=True)

    # Botão extra de próxima questão no fim da página
    if st.button("Próxima questão ▶", key="next_bottom", disabled=(idx >= total_questoes - 1)):
        st.session_state[key_idx] = min(total_questoes - 1, idx + 1)
        st.rerun()

def page_gerenciar():
    st.title("🧰 Gerenciar Questionário")
    qs = get_questionarios()
    if not qs:
        st.info("Nenhum questionário cadastrado.")
        return
    nomes = {q["nome"]: q["id"] for q in qs}
    default_id = st.session_state.get("current_qid")
    default_name = None
    if default_id:
        for name, _id in nomes.items():
            if _id == default_id:
                default_name = name
                break
    escolha = st.selectbox("Selecione um questionário", list(nomes.keys()), index=(list(nomes.keys()).index(default_name) if default_name in nomes else 0))
    qid = nomes[escolha]
    st.session_state["current_qid"] = qid

    # Metadados editáveis: Disciplina e Descrição
    qinfo = next((q for q in get_questionarios() if q["id"] == qid), None)
    if qinfo:
        st.markdown("### Metadados")
        col1, col2, col3 = st.columns([2,2,1])

        with col1:
            # Disciplinas existentes + opção nova
            existentes = [d for d in get_all_disciplinas() if d != "— Sistema —"]
            opcoes = ["(Sem Disciplina)"] + existentes + ["+ Nova disciplina..."]
            escolha_disc = st.selectbox("Disciplina", opcoes, index=(opcoes.index(qinfo["disciplina"]) if qinfo["disciplina"] in opcoes else 0))
        with col2:
            nova_disc = ""
            if escolha_disc == "+ Nova disciplina...":
                nova_disc = st.text_input("Nome da nova disciplina", value="")
        with col3:
            if st.button("Salvar disciplina", use_container_width=True):
                final_disc = nova_disc.strip() if escolha_disc == "+ Nova disciplina..." else (None if escolha_disc == "(Sem Disciplina)" else escolha_disc)
                update_questionario_disciplina(qid, final_disc or "Sem Disciplina")
                st.success("Disciplina atualizada.")
                st.rerun()

        desc = st.text_area("Descrição (opcional)", value=qinfo.get("descricao",""), height=80)
        if st.button("Salvar descrição"):
            update_questao_explicacao(qid, desc) if False else update_questionario_descricao(qid, desc)  # mantém comportamento original
            st.toast("Descrição atualizada.")

        st.divider()
        c1, _ = st.columns([1,3])
        with c1:
            if st.button("🔄 Resetar resoluções deste questionário"):
                resetar_resolucoes(qid)
                st.rerun()

    show_desempenho_block(qid)
    st.subheader("Questões")
    rows = get_questoes(qid)
    if not rows:
        st.info("Sem questões aqui ainda.")
    for idx, r in enumerate(rows, 1):
        with st.expander(f"Questão {idx} • {r['tipo']} • {r['texto'][:70]}"):
            st.write(f"**Tipo**: {r['tipo']}")
            if r["tipo"] == "MC":
                alts = [("A", r["op_a"]), ("B", r["op_b"]), ("C", r["op_c"]), ("D", r["op_d"]), ("E", r["op_e"])]
                st.write("**Alternativas:**")
                for l, a in alts:
                    if a:
                        mark = "✅" if l == r["correta_text"] else "▫️"
                        st.write(f"{mark} {l}) {a}")
            else:
                st.write(f"**Gabarito:** {'Verdadeiro' if r['correta_text']=='V' else 'Falso'}")
            st.write("**Explicação (edite abaixo):**")
            exp_key = f"m_exp_{r['id']}"
            new_exp = st.text_area("", value=r.get("explicacao",""), key=exp_key, height=120)
            if st.button("Salvar explicação", key=f"m_save_{r['id']}"):
                update_questao_explicacao(r["id"], new_exp)
                st.toast("Explicação atualizada.")
            if st.button("⭐ Favoritar", key=f"m_fav_{r['id']}"):
                if duplicar_questao_para_favoritos(r["id"]):
                    st.toast("Adicionada em 'Favoritos'.")

def page_importar():
    st.title("📥 Importar questões via CSV")
    st.markdown("Faça upload de um CSV **com cabeçalho**. Veja o modelo abaixo.")

    with st.expander("📄 Ver modelo de CSV suportado"):
        st.code(TEMPLATE_DOC, language="text")

    up = st.file_uploader("Enviar arquivo CSV", type=["csv"])
    txt = st.text_area("... ou cole aqui o conteúdo do CSV", height=180, placeholder="tipo,questionario,disciplina,texto,correta,explicacao,alternativas\n...")
    
    if st.button("Importar", type="primary"):
        with st.spinner("Importando questões..."):
            try:
                if up is not None:
                    ok, erros = import_csv_to_db(up)
                elif txt.strip():
                    ok, erros = import_csv_to_db(txt)
                else:
                    st.warning("Envie um arquivo ou cole o conteúdo do CSV.")
                    return

                if ok > 0:
                    st.success(f"✅ {ok} questões importadas com sucesso!")
                else:
                    st.warning("Nenhuma questão foi importada.")
                
                if erros:
                    with st.expander(f"⚠️ {len(erros)} erro(s) encontrado(s)"):
                        for e in erros[:100]:
                            st.write("- ", e)
            except Exception as e:
                st.error(f"❌ Falha na importação: {e}")

def page_simulado():
    st.title("📝 Simulados")
    qs_all = [q for q in get_questionarios() if q["nome"] != "Favoritos"]
    if not qs_all:
        st.info("Crie ou importe questionários primeiro.")
        return
    options = {f"{q['nome']}": q['id'] for q in qs_all}
    escolha = st.multiselect("Selecione um ou mais questionários", list(options.keys()))
    qids = [options[k] for k in escolha]

    total_disp = 0
    if qids:
        total_disp = sum(len(get_questoes(qid)) for qid in qids)

    n = st.number_input("Número de questões no simulado", min_value=1, value=min(10, max(1,total_disp)), max_value=max(1,total_disp) if total_disp else 1, step=1, disabled=(total_disp==0))

    if st.button("Iniciar Simulado", type="primary", disabled=(not qids or total_disp == 0)):
        st.session_state["simulado_qids"] = qids
        st.session_state["simulado_pool"] = [dict(r) for r in get_random_questoes(qids, n)]
        st.session_state["simulado_idx"] = 0
        st.session_state["simulado_acertos"] = 0
        st.session_state["mode"] = "run_simulado"
        st.session_state["go_to"] = "Simulados"
        st.rerun()

def page_run_simulado():
    st.title("🧪 Simulado em andamento")
    pool = st.session_state.get("simulado_pool", [])
    idx = st.session_state.get("simulado_idx", 0)
    acertos = st.session_state.get("simulado_acertos", 0)

    if idx >= len(pool):
        total = len(pool)
        perc = (acertos/total)*100 if total else 0
        st.success(f"✅ Fim do simulado! Acertos: {acertos}/{total} ({perc:.1f}%).")
        if st.button("Voltar aos simulados", type="primary"):
            st.session_state["mode"] = None
            st.session_state["go_to"] = "Simulados"
            st.rerun()
        return

    q = pool[idx]
    st.info(f"Questão {idx+1} de {len(pool)}")

    qid = q["id"]
    tipo = q["tipo"]
    st.markdown(f"**{q['texto']}**")

    answered_key = f"answered_sim_{qid}"
    result_key = f"result_sim_{qid}"

    if tipo == "VF":
        vf_options = ["— Selecione —", "Verdadeiro", "Falso"]
        escolha = st.radio("Sua resposta", vf_options, key=f"vf_sim_{qid}", index=0)
        if answered_key not in st.session_state and escolha != "— Selecione —":
            gabarito = (q["correta_text"] == "V")
            user = (escolha == "Verdadeiro")
            st.session_state[answered_key] = True
            st.session_state[result_key] = (gabarito == user)
            if st.session_state[result_key]:
                st.session_state["simulado_acertos"] = acertos + 1
    else:
        alternativas = [q["op_a"], q["op_b"], q["op_c"], q["op_d"], q["op_e"]]
        letras = ["A","B","C","D","E"]
        opts = [(letras[i], alt) for i, alt in enumerate(alternativas) if alt]
        labels = ["— Selecione —"] + [f"{letra}) {alt}" for letra, alt in opts]
        escolha = st.radio("Escolha uma alternativa", labels, key=f"mc_sim_{qid}", index=0)
        if answered_key not in st.session_state and escolha != "— Selecione —":
            letra_escolhida = escolha.split(")")[0]
            st.session_state[answered_key] = True
            st.session_state[result_key] = (letra_escolhida == q["correta_text"])
            if st.session_state[result_key]:
                st.session_state["simulado_acertos"] = acertos + 1

    if st.session_state.get(answered_key):
        if st.session_state.get(result_key):
            st.success("✅ Correto!")
        else:
            st.error("❌ Incorreto.")
        with st.expander("Ver explicação / editar"):
            exp_key = f"exp_sim_{qid}"
            new_exp = st.text_area("Texto da explicação (salvo no banco):", value=q.get("explicacao",""), key=exp_key, height=160)
            if st.button("Salvar explicação", key=f"save_exp_sim_{qid}"):
                update_questao_explicacao(qid, new_exp)
                st.toast("Explicação atualizada.")

    if st.button("Próxima ▶", type="primary"):
        st.session_state["simulado_idx"] = idx + 1
        for k in [answered_key, result_key, f"vf_sim_{qid}", f"mc_sim_{qid}"]:
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()

# =============================
# Main Navigation
# =============================
def main():
    if "db_checked" not in st.session_state:
        ok = connection_status()
        if not ok:
            st.stop()
        st.session_state["db_checked"] = True
        init_db()
    
    st.session_state.setdefault("nav_choice", "Painel")
    if "go_to" in st.session_state:
        st.session_state["nav_choice"] = st.session_state.pop("go_to")

    with st.sidebar:
        st.header("Navegação")
        choice = st.radio("Ir para", ["Painel", "Praticar", "Gerenciar", "Importar CSV", "Simulados"], key="nav_choice")

    if choice == "Painel":
        page_dashboard()
    elif choice == "Praticar":
        page_praticar()
    elif choice == "Gerenciar":
        page_gerenciar()
    elif choice == "Importar CSV":
        page_importar()
    elif choice == "Simulados":
        if st.session_state.get("mode") == "run_simulado":
            page_run_simulado()
        else:
            page_simulado()

if __name__ == "__main__":
    main()
