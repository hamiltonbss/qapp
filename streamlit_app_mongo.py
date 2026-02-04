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

        # Simulados
        db.simulados.create_index([("updated_at", DESCENDING)], name="idx_sim_updated_at", background=True)
        db.simulados.create_index([("nome", ASCENDING)], name="idx_sim_nome", background=True)

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
    # Remove progresso salvo (retomar de onde parou)
    db.questionarios.update_one({"_id": oid}, {"$unset": {"progress_pool": "", "progress_idx": "", "progress_updated_at": ""}})
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


def get_balanced_random_questoes_por_questionario(questionario_ids, n):
    """Sorteia questões de forma equilibrada entre questionários.
    - Distribui a cota de n o mais uniformemente possível entre os questionários.
    - Se algum questionário tiver menos questões do que sua cota, redistribui o restante.
    Retorna lista de questões (dicts) e total_disponivel.
    """
    db = get_db()
    qids = [ObjectId(x) for x in questionario_ids]
    if not qids or int(n) <= 0:
        return [], 0

    # Quantidade disponível por questionário
    counts = {str(qid): db.questoes.count_documents({"questionario_id": qid}) for qid in qids}
    total_disp = sum(counts.values())
    if total_disp == 0:
        return [], 0

    target = min(int(n), total_disp)
    # Alocação inicial uniforme
    alive = [str(qid) for qid in qids if counts.get(str(qid), 0) > 0]
    if not alive:
        return [], 0

    alloc = {qid: 0 for qid in alive}
    base = target // len(alive)
    rem = target % len(alive)

    for qid in alive:
        alloc[qid] = min(base, counts[qid])

    # distribui o resto (round-robin) respeitando disponibilidade
    remaining = target - sum(alloc.values())
    order = list(alive)
    i = 0
    while remaining > 0 and order:
        qid = order[i % len(order)]
        if alloc[qid] < counts[qid]:
            alloc[qid] += 1
            remaining -= 1
        i += 1
        # segurança contra loop infinito
        if i > 100000:
            break

    # Agora busca amostras por questionário
    out = []
    for qid_str, k in alloc.items():
        if k <= 0:
            continue
        pipeline = [
            {"$match": {"questionario_id": ObjectId(qid_str)}},
            {"$sample": {"size": int(k)}},
        ]
        out.extend([_doc_to_row_questao(x) for x in db.questoes.aggregate(pipeline)])

    random.shuffle(out)
    return out, total_disp

def get_questionarios_por_disciplina(disciplinas):
    """Retorna lista de questionários (dict) cujas disciplinas estão em 'disciplinas'."""
    if not disciplinas:
        return []
    qs = get_questionarios()
    disciplinas_set = set(disciplinas)
    # Não inclui Favoritos
    return [q for q in qs if q.get("nome") != "Favoritos" and (q.get("disciplina") or "Sem Disciplina") in disciplinas_set]


def save_resposta(questionario_id, questao_id, correto):
    db = get_db()
    db.respostas.insert_one({
        "questionario_id": ObjectId(questionario_id),
        "questao_id": ObjectId(questao_id),
        "correto": 1 if correto else 0,
        "respondido_em": datetime.now(timezone.utc).isoformat()
    })



# =============================
# Simulados (persistência)
# =============================
def create_simulado(nome, pool_ids, meta=None):
    """Cria um simulado persistido no MongoDB e retorna o id (str)."""
    db = get_db()
    doc = {
        "nome": (nome or "").strip() or f"Simulado {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
        "pool_ids": [str(x) for x in (pool_ids or [])],
        "idx": 0,
        "acertos": 0,
        "total": int(len(pool_ids or [])),
        "status": "in_progress",  # in_progress | finished
        "meta": meta or {},
        "respostas": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    res = db.simulados.insert_one(doc)
    return str(res.inserted_id)

@st.cache_data(ttl=10)
def list_simulados():
    """Lista simulados (mais recentes primeiro)."""
    db = get_db()
    cur = db.simulados.find(
        {},
        {"nome": 1, "status": 1, "total": 1, "acertos": 1, "idx": 1, "created_at": 1, "updated_at": 1},
    ).sort([("updated_at", DESCENDING)])
    out = []
    for d in cur:
        out.append({
            "id": str(d["_id"]),
            "nome": d.get("nome",""),
            "status": d.get("status","in_progress"),
            "total": int(d.get("total") or 0),
            "acertos": int(d.get("acertos") or 0),
            "idx": int(d.get("idx") or 0),
            "created_at": d.get("created_at"),
            "updated_at": d.get("updated_at"),
        })
    return out

def get_simulado(sim_id):
    db = get_db()
    d = db.simulados.find_one({"_id": ObjectId(sim_id)})
    if not d:
        return None
    d["id"] = str(d["_id"])
    return d

def update_simulado_nome(sim_id, novo_nome):
    db = get_db()
    db.simulados.update_one(
        {"_id": ObjectId(sim_id)},
        {"$set": {"nome": (novo_nome or "").strip(), "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    list_simulados.clear()

def update_simulado_progress(sim_id, idx=None, acertos=None, status=None):
    db = get_db()
    sets = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if idx is not None:
        sets["idx"] = int(idx)
    if acertos is not None:
        sets["acertos"] = int(acertos)
    if status is not None:
        sets["status"] = status
    db.simulados.update_one({"_id": ObjectId(sim_id)}, {"$set": sets})
    list_simulados.clear()

def add_simulado_resposta(sim_id, questao_id, correto, resposta_raw):
    """Registra resposta (append) e atualiza updated_at."""
    db = get_db()
    db.simulados.update_one(
        {"_id": ObjectId(sim_id)},
        {"$push": {"respostas": {
            "questao_id": str(questao_id),
            "correto": 1 if correto else 0,
            "resposta": resposta_raw,
            "respondido_em": datetime.now(timezone.utc).isoformat()
        }},
         "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    list_simulados.clear()

def delete_simulado(sim_id):
    db = get_db()
    db.simulados.delete_one({"_id": ObjectId(sim_id)})
    list_simulados.clear()


def get_questionario_progress(questionario_id):
    """Carrega progresso salvo do questionário (pool e índice atual)."""
    db = get_db()
    doc = db.questionarios.find_one(
        {"_id": ObjectId(questionario_id)},
        {"progress_pool": 1, "progress_idx": 1}
    ) or {}
    pool = doc.get("progress_pool") or []
    idx = int(doc.get("progress_idx") or 0)
    # Sanitiza
    if not isinstance(pool, list):
        pool = []
    idx = max(0, idx)
    return pool, idx

def set_questionario_progress(questionario_id, pool, idx):
    """Salva progresso (para retomar de onde parou)."""
    db = get_db()
    db.questionarios.update_one(
        {"_id": ObjectId(questionario_id)},
        {"$set": {
            "progress_pool": list(pool),
            "progress_idx": int(idx),
            "progress_updated_at": datetime.now(timezone.utc).isoformat()
        }}
    )


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

def update_questao_texto(questao_id, novo_texto):
    db = get_db()
    db.questoes.update_one({"_id": ObjectId(questao_id)}, {"$set": {"texto": novo_texto}})

def update_questao_gabarito(questao_id, correta_text):
    """Atualiza o campo 'correta_text' (VF: 'V'/'F'; MC: 'A'..'E')."""
    db = get_db()
    db.questoes.update_one({"_id": ObjectId(questao_id)}, {"$set": {"correta_text": correta_text}})

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

    - VF: igual antes (radio padrão).
    - MC:
        * Cada alternativa aparece em UMA linha com:
            [checkbox para riscar] [botão tipo radio] [texto da alternativa]
        * O checkbox é só visual (rascunho), não conta como resposta nem vai para o banco.
        * O botão 🔘 / ⚪ funciona como radio: só uma letra fica selecionada.
        * Quando a primeira letra é escolhida, a resposta é gravada (save_resposta) e travada.
    """
    qid = q_row["id"]
    tipo = q_row["tipo"]
    answered_key = f"answered_{qid}"          # se já foi respondida (verdadeiro/falso)
    result_key = f"result_{qid}"              # True/False se acertou
    answer_letter_key = f"answer_letter_{qid}"  # letra escolhida na MC (A, B, C...)

    if questao_numero:
        st.markdown(f"#### Questão {questao_numero}")
    st.markdown(f"**{q_row['texto']}**")
# ======================
    # EDIÇÃO RÁPIDA (enunciado e gabarito) - direto na resolução
    # ======================
    with st.expander("✏️ Editar enunciado / gabarito (nesta questão)", expanded=False):
        txt_key = f"edit_texto_{qid}"
        gab_key = f"edit_gab_{qid}"

        novo_texto = st.text_area("Enunciado:", value=q_row.get("texto",""), key=txt_key, height=120)

        if tipo == "VF":
            gab_opts = ["Verdadeiro", "Falso"]
            gab_idx = 0 if q_row.get("correta_text") == "V" else 1
            novo_gab = st.radio("Gabarito:", gab_opts, index=gab_idx, key=gab_key, horizontal=True)
            nova_correta_text = "V" if novo_gab == "Verdadeiro" else "F"
        else:
            alts = [q_row.get("op_a"), q_row.get("op_b"), q_row.get("op_c"), q_row.get("op_d"), q_row.get("op_e")]
            letras = ["A", "B", "C", "D", "E"]
            letras_validas = [letras[i] for i, a in enumerate(alts) if a]
            if not letras_validas:
                letras_validas = ["A", "B", "C", "D", "E"]
            idx_sel = letras_validas.index(q_row.get("correta_text")) if q_row.get("correta_text") in letras_validas else 0
            nova_correta_text = st.selectbox("Gabarito (letra correta):", letras_validas, index=idx_sel, key=gab_key)

        if st.button("Salvar enunciado + gabarito", key=f"save_edit_{qid}"):
            # Salva apenas o que mudou
            if (novo_texto or "").strip() != (q_row.get("texto") or "").strip():
                update_questao_texto(qid, novo_texto)
            if (nova_correta_text or "").strip() != (q_row.get("correta_text") or "").strip():
                update_questao_gabarito(qid, nova_correta_text)
            st.toast("Questão atualizada.")
            st.rerun()


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

        st.caption("Clique para riscar mentalmente alternativas e escolher a resposta:")

        # letra atualmente selecionada (se houver)
        current_letter = st.session_state.get(answer_letter_key, None)

        for letra, alt in opts:
            strike_key = f"strike_{qid}_{letra}"
            if strike_key not in st.session_state:
                st.session_state[strike_key] = False

            # 3 colunas: [checkbox riscar] [botão tipo radio] [texto]
            col_cb, col_radio, col_txt = st.columns([0.06, 0.06, 0.88])

            # checkbox de riscar (rascunho visual)
            with col_cb:
                st.checkbox("", key=strike_key)

            # botão que se comporta como radio
            with col_radio:
                # símbolo visual: ⚪ não selecionado, 🔘 selecionado
                simbolo = "🔘" if current_letter == letra else "⚪"
                # se a questão já foi respondida, não deixa mudar a resposta
                disabled = answered_key in st.session_state
                clicked = st.button(simbolo, key=f"ansbtn_{qid}_{letra}", disabled=disabled)
                if clicked and answered_key not in st.session_state:
                    st.session_state[answer_letter_key] = letra
                    current_letter = letra  # reflete imediatamente nesta renderização

            # texto da alternativa (com ou sem risco)
            with col_txt:
                if st.session_state.get(strike_key, False):
                    st.markdown(
                        f"<span style='text-decoration: line-through; color: #6b7280;'>{letra}) {alt}</span>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(f"{letra}) {alt}")

        # texto informando o que está selecionado (só informativo)
        sel_txt = st.session_state.get(answer_letter_key, None)
        st.caption(f"Resposta selecionada: **{sel_txt if sel_txt else 'nenhuma'}**")

        # grava a resposta no banco apenas na primeira escolha
        if (
            tipo == "MC"
            and answered_key not in st.session_state
            and st.session_state.get(answer_letter_key) is not None
        ):
            letra_escolhida = st.session_state[answer_letter_key]
            is_correct = (letra_escolhida == q_row["correta_text"])
            st.session_state[answered_key] = True
            st.session_state[result_key] = is_correct
            save_resposta(parent_qid, qid, is_correct)
            if not is_correct:
                duplicar_questao_para_erros(qid)

    # ======================
    # FEEDBACK ACERTO / ERRO (com explicação)
    # ======================
    if st.session_state.get(answered_key):
        if st.session_state.get(result_key):
            st.success("✅ Você acertou esta questão.")
        else:
            st.error(f"❌ Você errou esta questão. Gabarito: {q_row['correta_text']}")

        # Mostra a explicação junto do feedback (se houver)
        exp_txt = (q_row.get("explicacao") or "").strip()
        if exp_txt:
            st.markdown(
                f"""
                <div style="
                    background-color:#fff8c4;
                    padding:14px;
                    border-radius:6px;
                    border:1px solid #e6d97a;
                    margin-top:10px;
                ">
                    <strong>Explicação:</strong><br><br>
                    {exp_txt}
                </div>
                """,
                unsafe_allow_html=True
            )

    # ======================
    # EXPLICAÇÃO (sempre aberta, altura fixa)
    # ======================
    with st.expander("Ver explicação / editar", expanded=False):
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
    st.title("📚 Painel de Questionários (rápido)")

    # Botão para atualizar Caderno de Erros com histórico
    if st.button("📔 Atualizar Caderno de Erros com histórico"):
        with st.spinner("Analisando respostas anteriores..."):
            n = popular_caderno_erros()
            if n > 0:
                st.success(f"✅ {n} questões erradas adicionadas ao Caderno de Erros!")
            else:
                st.info("Nenhuma questão nova para adicionar.")

    st.divider()

    # -------------------------
    # Helpers: estatísticas em lote (evita 2*N queries)
    # -------------------------
    def _bulk_stats(questionario_ids):
        """Retorna dict: qid(str) -> {total, respondidas, corretas, perc}.
        Calcula em poucas agregações no MongoDB para ficar leve no Painel.
        """
        if not questionario_ids:
            return {}

        db = get_db()
        oids = [ObjectId(qid) for qid in questionario_ids]

        # Total de questões por questionário
        totals_map = {qid: 0 for qid in questionario_ids}
        try:
            pipe_tot = [
                {"$match": {"questionario_id": {"$in": oids}}},
                {"$group": {"_id": "$questionario_id", "total": {"$sum": 1}}},
            ]
            for d in db.questoes.aggregate(pipe_tot):
                totals_map[str(d["_id"])] = int(d.get("total", 0))
        except Exception:
            pass

        # Última resposta por questão -> corretas + respondidas por questionário
        resp_map = {qid: {"respondidas": 0, "corretas": 0} for qid in questionario_ids}
        try:
            pipe_resp = [
                {"$match": {"questionario_id": {"$in": oids}}},
                {"$sort": {"respondido_em": 1}},
                {"$group": {
                    "_id": {"questionario_id": "$questionario_id", "questao_id": "$questao_id"},
                    "last_correto": {"$last": "$correto"},
                }},
                {"$group": {
                    "_id": "$_id.questionario_id",
                    "respondidas": {"$sum": 1},
                    "corretas": {"$sum": {"$cond": [{"$eq": ["$last_correto", 1]}, 1, 0]}},
                }},
            ]
            for d in db.respostas.aggregate(pipe_resp):
                qid_str = str(d["_id"])
                resp_map[qid_str] = {
                    "respondidas": int(d.get("respondidas", 0)),
                    "corretas": int(d.get("corretas", 0)),
                }
        except Exception:
            pass

        out = {}
        for qid in questionario_ids:
            total = int(totals_map.get(qid, 0))
            respondidas = int(resp_map.get(qid, {}).get("respondidas", 0))
            corretas = int(resp_map.get(qid, {}).get("corretas", 0))
            perc = (corretas / total) * 100 if total > 0 else 0.0
            out[qid] = {"total": total, "respondidas": respondidas, "corretas": corretas, "perc": perc}
        return out

    # -------------------------
    # Carrega metadados (leve) e organiza navegação
    # -------------------------
    all_qs = get_questionarios()
    if not all_qs:
        st.info("Nenhum questionário cadastrado ainda. Vá em **Importar CSV** para começar.")
        return

    # Identifica especiais
    caderno_erros = next((q for q in all_qs if q["nome"] == "Caderno de Erros"), None)

    # Caderno de Erros fixado no topo (estatística em lote, 1x)
    if caderno_erros:
        stats_erros = _bulk_stats([caderno_erros["id"]]).get(caderno_erros["id"], {"total": 0, "respondidas": 0, "corretas": 0, "perc": 0.0})
        with st.container(border=True):
            st.subheader("🧨 Caderno de Erros (fixado)")
            col1, col2, col3, col4 = st.columns([1, 1, 1, 2])
            with col1:
                st.metric("Total", stats_erros["total"])
            with col2:
                st.metric("Respondidas", stats_erros["respondidas"])
            with col3:
                st.metric("Corretas", stats_erros["corretas"])
            with col4:
                st.progress(int(stats_erros["perc"]), text=f"Aproveitamento: {stats_erros['perc']:.1f}%")

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

    # Demais questionários (exclui especiais para o Painel)
    qs = [q for q in all_qs if q["nome"] not in ("Caderno de Erros", "Favoritos")]
    if not qs:
        st.info("Nenhum questionário cadastrado ainda. Vá em **Importar CSV** para começar.")
        return

    # Filtros leves (não calcula estatísticas aqui)
    filtro_global = st.text_input("🔎 Buscar por nome do questionário", key="dash_busca")
    disciplinas = sorted({(q.get("disciplina") or "Sem Disciplina") for q in qs})
    escolha_disc = st.selectbox("📦 Disciplina", ["Todas (resumo)"] + disciplinas, key="dash_disciplina")

    # -------------------------
    # Modo 1: Todas (resumo leve)
    # - Uma única rodada de agregação para todos os questionários.
    # - Mostra tabela de disciplinas (não renderiza cards de todos).
    # -------------------------
    if escolha_disc == "Todas (resumo)":
        # Aplica filtro de busca só para reduzir universo se o usuário quiser
        qs_filtrados = [
            q for q in qs
            if (not filtro_global or filtro_global.lower() in q["nome"].lower())
        ]

        if not qs_filtrados:
            st.caption("Nenhum questionário corresponde ao filtro.")
            return

        qids = [q["id"] for q in qs_filtrados]
        stats = _bulk_stats(qids)

        # Agrega por disciplina
        agg = {}
        for q in qs_filtrados:
            disc = q.get("disciplina") or "Sem Disciplina"
            s = stats.get(q["id"], {"total": 0, "respondidas": 0, "corretas": 0})
            a = agg.setdefault(disc, {"questionarios": 0, "total": 0, "respondidas": 0, "corretas": 0})
            a["questionarios"] += 1
            a["total"] += int(s["total"])
            a["respondidas"] += int(s["respondidas"])
            a["corretas"] += int(s["corretas"])

        rows = []
        for disc, a in sorted(agg.items(), key=lambda x: x[0]):
            perc = (a["corretas"] / a["total"]) * 100 if a["total"] else 0.0
            rows.append({
                "Disciplina": disc,
                "Questionários": a["questionarios"],
                "Total": a["total"],
                "Respondidas": a["respondidas"],
                "Corretas": a["corretas"],
                "Aproveitamento (%)": round(perc, 1),
            })

        st.subheader("Resumo por disciplina")
        st.dataframe(rows, use_container_width=True, hide_index=True)
        st.caption("Dica: selecione uma disciplina acima para ver e acessar os questionários sem carregar o painel inteiro.")
        return

    # -------------------------
    # Modo 2: Uma disciplina (carrega só o necessário)
    # -------------------------
    qs_disc = [q for q in qs if (q.get("disciplina") or "Sem Disciplina") == escolha_disc]
    if filtro_global:
        qs_disc = [q for q in qs_disc if filtro_global.lower() in q["nome"].lower()]

    if not qs_disc:
        st.caption("Nenhum questionário nesta disciplina corresponde ao filtro.")
        return

    # Estatísticas apenas para os questionários desta disciplina (bem mais leve)
    qids_disc = [q["id"] for q in qs_disc]
    stats_disc = _bulk_stats(qids_disc)

    # Agregado da disciplina
    total_disc = sum(stats_disc[qid]["total"] for qid in qids_disc)
    respondidas_disc = sum(stats_disc[qid]["respondidas"] for qid in qids_disc)
    corretas_disc = sum(stats_disc[qid]["corretas"] for qid in qids_disc)
    perc_disc = (corretas_disc / total_disc) * 100 if total_disc else 0.0

    with st.container(border=True):
        st.subheader(f"📦 {escolha_disc}")
        col1, col2, col3, col4 = st.columns([1, 1, 1, 2])
        with col1:
            st.metric("Total (disciplina)", total_disc)
        with col2:
            st.metric("Respondidas (disciplina)", respondidas_disc)
        with col3:
            st.metric("Corretas (disciplina)", corretas_disc)
        with col4:
            st.progress(int(perc_disc), text=f"Aproveitamento da disciplina: {perc_disc:.1f}%")

        st.markdown("---")

        nomes_validos = [q["nome"] for q in qs_disc]
        sel = st.selectbox("Selecione um questionário", nomes_validos, key="dash_sel_q")
        escolhido = next((x for x in qs_disc if x["nome"] == sel), None)

        if escolhido:
            s = stats_disc.get(escolhido["id"], {"total": 0, "respondidas": 0, "corretas": 0, "perc": 0.0})
            col1, col2, col3, col4 = st.columns([1, 1, 1, 2])
            with col1:
                st.metric("Total", s["total"])
            with col2:
                st.metric("Respondidas", s["respondidas"])
            with col3:
                st.metric("Corretas", s["corretas"])
            with col4:
                st.progress(int(s["perc"]), text=f"Aproveitamento: {s['perc']:.1f}%")

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

    # Se a sessão for interrompida, tenta retomar do ponto salvo no MongoDB.
    if key_pool not in st.session_state:
        saved_pool, saved_idx = get_questionario_progress(qid)

        # Valida pool salvo contra as questões atuais do questionário
        current_ids = {r["id"] for r in get_questoes(qid)}
        saved_pool = [x for x in saved_pool if x in current_ids]

        if saved_pool:
            st.session_state[key_pool] = saved_pool
            st.session_state[key_idx] = min(max(int(saved_idx), 0), len(saved_pool) - 1)
        else:
            # Embaralha apenas uma vez (quando não há progresso salvo)
            st.session_state[key_pool] = [r["id"] for r in get_questoes(qid)]
            random.shuffle(st.session_state[key_pool])
            st.session_state[key_idx] = 0

        # Persiste imediatamente (para garantir consistência)
        set_questionario_progress(qid, st.session_state[key_pool], st.session_state[key_idx])

    pool = st.session_state[key_pool]
    if not pool:
        st.info("Acabaram as questões! Você pode **resetar resoluções** para reiniciar.")
        return

    # Garante índice válido
    st.session_state.setdefault(key_idx, 0)
    idx = st.session_state[key_idx]
    idx = max(0, min(idx, len(pool) - 1))
    st.session_state[key_idx] = idx
    set_questionario_progress(qid, pool, idx)

    current_qid = pool[idx]
    row = get_questao_by_id(current_qid)
    total_questoes = len(pool)
    questao_numero = idx + 1

    # Navegação: voltar / avançar + indicador da posição
    nav_col1, nav_col2, nav_col3 = st.columns([1, 1, 2])
    with nav_col1:
        if st.button("◀ Questão anterior", key="prev_top", disabled=(idx == 0)):
            st.session_state[key_idx] = max(0, idx - 1)
            set_questionario_progress(qid, pool, st.session_state[key_idx])
            st.rerun()
    with nav_col2:
        if st.button("Próxima questão ▶", key="next_top", disabled=(idx >= total_questoes - 1)):
            st.session_state[key_idx] = min(total_questoes - 1, idx + 1)
            set_questionario_progress(qid, pool, st.session_state[key_idx])
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
        set_questionario_progress(qid, pool, st.session_state[key_idx])
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

    # -------------------------
    # Simulados salvos (lista)
    # -------------------------
    sims = list_simulados()
    with st.expander("📚 Simulados salvos", expanded=True):
        if sims:
            labels = []
            for s in sims:
                status = "✅ finalizado" if s.get("status") == "finished" else "⏳ em andamento"
                labels.append(f"{s.get('nome','(Sem nome)')} • {status} • {s.get('acertos',0)}/{s.get('total',0)}")
            sel_label = st.selectbox("Selecione um simulado", labels, index=0, key="sel_simulado_salvo")
            sel_sim = sims[labels.index(sel_label)]
            c1, c2, c3 = st.columns([1,1,2])
            with c1:
                if st.button("▶ Abrir / Continuar", key="btn_open_sim"):
                    st.session_state["current_simulado_id"] = sel_sim["id"]
                    st.session_state["mode"] = "run_simulado"
                    st.session_state["go_to"] = "Simulados"
                    st.rerun()
            with c2:
                if st.button("🗑️ Excluir", key="btn_del_sim"):
                    delete_simulado(sel_sim["id"])
                    if st.session_state.get("current_simulado_id") == sel_sim["id"]:
                        st.session_state.pop("current_simulado_id", None)
                        st.session_state["mode"] = None
                    st.rerun()
            with c3:
                novo_nome = st.text_input("Renomear", value=sel_sim.get("nome",""), key="rename_sim")
                if st.button("Salvar nome", key="btn_rename_sim"):
                    update_simulado_nome(sel_sim["id"], novo_nome)
                    st.toast("Nome atualizado.")
                    st.rerun()
        else:
            st.caption("Nenhum simulado salvo ainda. Crie um novo abaixo.")
    qs_all = [q for q in get_questionarios() if q["nome"] != "Favoritos"]
    if not qs_all:
        st.info("Crie ou importe questionários primeiro.")
        return

    st.caption("Você pode montar o simulado por **disciplinas** (com distribuição equilibrada entre questionários) ou selecionar **questionários** diretamente.")

    modo = st.radio("Modo de seleção", ["Por disciplina", "Por questionário"], horizontal=True)

    sim_nome = st.text_input("Nome do simulado (opcional)", value="", placeholder="Ex.: Simulado Constitucional - 20/01")

    # -------------------------
    # MODO: POR DISCIPLINA
    # -------------------------
    if modo == "Por disciplina":
        disciplinas = [d for d in get_all_disciplinas() if d != "— Sistema —"]
        if not disciplinas:
            st.info("Nenhuma disciplina encontrada. Classifique questionários em **Gerenciar** ou importe via CSV com a coluna 'disciplina'.")
            return

        sel_disc = st.multiselect("Selecione 1 ou mais disciplinas", disciplinas)

        if not sel_disc:
            st.info("Selecione ao menos uma disciplina para montar o simulado.")
            return

        # Questionários elegíveis por disciplina (mantém Favoritos fora)
        qs_por_disc = get_questionarios_por_disciplina(sel_disc)

        if not qs_por_disc:
            st.warning("Não encontrei questionários nas disciplinas selecionadas (ou eles estão vazios).")
            return

        # Agrupa questionários por disciplina
        grupos = {}
        for q in qs_por_disc:
            grupos.setdefault(q.get("disciplina") or "Sem Disciplina", []).append(q)

        st.markdown("### Quantidade de questões por disciplina")
        n_por_disc = {}
        total_planejado = 0
        for disc in sel_disc:
            qids_disc = [q["id"] for q in grupos.get(disc, [])]
            total_disp_disc = sum(len(get_questoes(qid)) for qid in qids_disc) if qids_disc else 0

            col1, col2 = st.columns([2, 1])
            with col1:
                st.write(f"**{disc}**")
                st.caption(f"Questionários: {len(qids_disc)} • Questões disponíveis: {total_disp_disc}")
            with col2:
                n_val = st.number_input(
                    f"Qtd ({disc})",
                    min_value=0,
                    value=min(10, total_disp_disc) if total_disp_disc else 0,
                    max_value=total_disp_disc if total_disp_disc else 0,
                    step=1,
                    key=f"n_disc_{disc}",
                    disabled=(total_disp_disc == 0),
                )
            n_por_disc[disc] = int(n_val)
            total_planejado += int(n_val)

        st.divider()
        st.metric("Total de questões no simulado", total_planejado)

        if st.button("Iniciar Simulado", type="primary", disabled=(total_planejado <= 0)):
            pool_final = []
            for disc in sel_disc:
                qtd = int(n_por_disc.get(disc, 0))
                if qtd <= 0:
                    continue
                qids_disc = [q["id"] for q in grupos.get(disc, [])]
                if not qids_disc:
                    continue
                # Equilibra dentro da disciplina entre os questionários
                parte, total_disp = get_balanced_random_questoes_por_questionario(qids_disc, qtd)
                pool_final.extend([dict(r) for r in parte])

            random.shuffle(pool_final)

            if not pool_final:
                st.warning("Não foi possível montar o simulado com as escolhas atuais.")
                return

            pool_ids = [q["id"] for q in pool_final]
            sim_id = create_simulado(
                sim_nome,
                pool_ids,
                meta={"modo": "Por disciplina", "disciplinas": list(sel_disc), "n_por_disc": dict(n_por_disc)},
            )
            st.session_state["current_simulado_id"] = sim_id
            st.session_state["mode"] = "run_simulado"
            st.session_state["go_to"] = "Simulados"
            st.rerun()

    # -------------------------
    # MODO: POR QUESTIONÁRIO (mantém a forma antiga)
    # -------------------------
    else:
        options = {f"{q['nome']}": q["id"] for q in qs_all}
        escolha = st.multiselect("Selecione um ou mais questionários", list(options.keys()))
        qids = [options[k] for k in escolha]

        total_disp = 0
        if qids:
            total_disp = sum(len(get_questoes(qid)) for qid in qids)

        n = st.number_input(
            "Número de questões no simulado",
            min_value=1,
            value=min(10, max(1, total_disp)),
            max_value=max(1, total_disp) if total_disp else 1,
            step=1,
            disabled=(total_disp == 0),
        )

        if st.button("Iniciar Simulado", type="primary", disabled=(not qids or total_disp == 0)):
            pool_final = [dict(r) for r in get_random_questoes(qids, n)]
            pool_ids = [q["id"] for q in pool_final]
            sim_id = create_simulado(
                sim_nome,
                pool_ids,
                meta={"modo": "Por questionário", "questionarios": list(qids), "n_total": int(n)},
            )
            st.session_state["current_simulado_id"] = sim_id
            st.session_state["mode"] = "run_simulado"
            st.session_state["go_to"] = "Simulados"
            st.rerun()

def page_run_simulado():

    st.title("🧪 Simulado em andamento")

    sim_id = st.session_state.get("current_simulado_id")
    if not sim_id:
        st.info("Nenhum simulado selecionado. Vá em **Simulados** e crie/abra um simulado.")
        st.session_state["mode"] = None
        return

    sim = get_simulado(sim_id)
    if not sim:
        st.error("Simulado não encontrado (pode ter sido excluído).")
        st.session_state.pop("current_simulado_id", None)
        st.session_state["mode"] = None
        return

    pool_ids = sim.get("pool_ids") or []
    idx = int(sim.get("idx") or 0)
    acertos = int(sim.get("acertos") or 0)
    total = int(sim.get("total") or len(pool_ids) or 0)

    if not pool_ids or total == 0:
        st.warning("Este simulado não tem questões.")
        st.session_state["mode"] = None
        return

    # Mapa de respostas já registradas (última resposta por questão)
    last_answer = {}
    for r in sim.get("respostas") or []:
        last_answer[str(r.get("questao_id"))] = {
            "correto": bool(r.get("correto", 0)),
            "resposta": r.get("resposta"),
            "respondido_em": r.get("respondido_em"),
        }

    # Finalização
    if idx >= len(pool_ids) or sim.get("status") == "finished":
        perc = (acertos / total) * 100 if total else 0
        st.success(f"✅ Fim do simulado! Acertos: {acertos}/{total} ({perc:.1f}%).")
        update_simulado_progress(sim_id, status="finished")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Voltar aos simulados", type="primary"):
                st.session_state["mode"] = None
                st.session_state["go_to"] = "Simulados"
                st.rerun()
        with c2:
            if st.button("Abrir lista de simulados salvos"):
                st.session_state["mode"] = None
                st.session_state["go_to"] = "Simulados"
                st.rerun()
        return

    # Questão atual
    questao_id = str(pool_ids[idx])
    q = get_questao_by_id(questao_id)
    if not q:
        st.error("Questão não encontrada (pode ter sido removida). Pulando para a próxima…")
        update_simulado_progress(sim_id, idx=idx + 1, acertos=acertos)
        st.rerun()
        return

    st.info(f"Questão {idx+1} de {len(pool_ids)}")
    st.markdown(f"**{q['texto']}**")

    # Chaves de UI por simulado
    answered_key = f"answered_sim_{sim_id}_{questao_id}"
    result_key = f"result_sim_{sim_id}_{questao_id}"

    # Se já existe resposta persistida, trava a UI e mostra feedback
    already = last_answer.get(questao_id)
    if already and answered_key not in st.session_state:
        st.session_state[answered_key] = True
        st.session_state[result_key] = bool(already.get("correto"))

    tipo = q["tipo"]

    if tipo == "VF":
        vf_options = ["— Selecione —", "Verdadeiro", "Falso"]
        disabled = bool(st.session_state.get(answered_key))
        escolha = st.radio("Sua resposta", vf_options, key=f"vf_sim_{sim_id}_{questao_id}", index=0, disabled=disabled)
        if (not st.session_state.get(answered_key)) and escolha != "— Selecione —":
            gabarito = (q["correta_text"] == "V")
            user = (escolha == "Verdadeiro")
            is_correct = (gabarito == user)

            add_simulado_resposta(sim_id, questao_id, is_correct, escolha)
            acertos = acertos + (1 if is_correct else 0)
            update_simulado_progress(sim_id, acertos=acertos)

            st.session_state[answered_key] = True
            st.session_state[result_key] = is_correct

    else:
        alternativas = [q["op_a"], q["op_b"], q["op_c"], q["op_d"], q["op_e"]]
        letras = ["A", "B", "C", "D", "E"]
        opts = [(letras[i], alt) for i, alt in enumerate(alternativas) if alt]
        labels = ["— Selecione —"] + [f"{letra}) {alt}" for letra, alt in opts]

        disabled = bool(st.session_state.get(answered_key))
        escolha = st.radio("Escolha uma alternativa", labels, key=f"mc_sim_{sim_id}_{questao_id}", index=0, disabled=disabled)

        if (not st.session_state.get(answered_key)) and escolha != "— Selecione —":
            letra_escolhida = escolha.split(")")[0]
            is_correct = (letra_escolhida == q["correta_text"])

            add_simulado_resposta(sim_id, questao_id, is_correct, letra_escolhida)
            acertos = acertos + (1 if is_correct else 0)
            update_simulado_progress(sim_id, acertos=acertos)

            st.session_state[answered_key] = True
            st.session_state[result_key] = is_correct

    # Feedback + explicação
    if st.session_state.get(answered_key):
        if st.session_state.get(result_key):
            st.success("✅ Correto!")
        else:
            st.error("❌ Incorreto.")

        exp_txt = (q.get("explicacao") or "").strip()
        if exp_txt:
            st.markdown(
                "<div style='background-color:#fff8c4; padding:14px; border-radius:6px; border:1px solid #e6d97a; margin-top:10px;'>"
                "<strong>Explicação:</strong><br><br>"
                f"{exp_txt}"
                "</div>",
                unsafe_allow_html=True,
            )

        with st.expander("Ver explicação / editar"):
            exp_key = f"exp_sim_{sim_id}_{questao_id}"
            new_exp = st.text_area("Texto da explicação (salvo no banco):", value=q.get("explicacao", ""), key=exp_key, height=160)
            if st.button("Salvar explicação", key=f"save_exp_sim_{sim_id}_{questao_id}"):
                update_questao_explicacao(questao_id, new_exp)
                st.toast("Explicação atualizada.")

    # Próxima
    if st.button("Próxima ▶", type="primary"):
        new_idx = idx + 1
        new_status = "finished" if new_idx >= len(pool_ids) else "in_progress"
        update_simulado_progress(sim_id, idx=new_idx, acertos=acertos, status=new_status)

        for k in [answered_key, result_key, f"vf_sim_{sim_id}_{questao_id}", f"mc_sim_{sim_id}_{questao_id}"]:
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
