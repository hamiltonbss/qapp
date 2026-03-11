import os
import random
import calendar
from datetime import datetime, timezone, date
from functools import lru_cache

import streamlit as st
import pandas as pd

from pymongo import MongoClient, ASCENDING, DESCENDING
from bson import ObjectId

try:
    import bcrypt
    _BCRYPT_OK = True
except ImportError:
    _BCRYPT_OK = False

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


def _sim_last_correct_map(sim_doc):
    """Mapeia questao_id -> bool (última resposta no simulado)."""
    last = {}
    respostas = sim_doc.get("respostas") or []
    for r in sorted(respostas, key=lambda x: x.get("respondido_em", "")):
        qid = str(r.get("questao_id"))
        if qid:
            last[qid] = bool(r.get("correto", 0))
    return last


def simulado_stats_by_disciplina(sim_doc):
    """Tabela de desempenho por disciplina dentro de um simulado."""
    db = get_db()
    pool_ids = [str(x) for x in (sim_doc.get("pool_ids") or [])]
    if not pool_ids:
        return []

    # Questão -> questionário (lote)
    try:
        quest_docs = list(db.questoes.find(
            {"_id": {"$in": [ObjectId(x) for x in pool_ids]}},
            {"_id": 1, "questionario_id": 1},
        ))
    except Exception:
        quest_docs = []

    qid_by_quest = {str(d["_id"]): str(d.get("questionario_id")) for d in quest_docs if d.get("questionario_id")}

    # Questionário -> disciplina (lote)
    qids = list({ObjectId(qid) for qid in qid_by_quest.values() if qid})
    disc_by_q = {}
    if qids:
        for d in db.questionarios.find({"_id": {"$in": qids}}, {"_id": 1, "disciplina": 1}):
            disc_by_q[str(d["_id"])] = d.get("disciplina") or "Sem Disciplina"

    last_map = _sim_last_correct_map(sim_doc)

    agg = {}
    for quest_id in pool_ids:
        qid = qid_by_quest.get(str(quest_id))
        disc = disc_by_q.get(str(qid), "Sem Disciplina")
        a = agg.setdefault(disc, {"Disciplina": disc, "Total": 0, "Acertos": 0})
        a["Total"] += 1
        if last_map.get(str(quest_id)) is True:
            a["Acertos"] += 1

    rows = []
    for disc, a in sorted(agg.items(), key=lambda x: x[0]):
        total = int(a["Total"])
        ac = int(a["Acertos"])
        perc = (ac / total) * 100 if total else 0.0
        rows.append({"Disciplina": disc, "Total": total, "Acertos": ac, "Aproveitamento (%)": round(perc, 1)})
    return rows


def simulado_overall_stats(sim_doc):
    total = int(sim_doc.get("total") or len(sim_doc.get("pool_ids") or []) or 0)
    acertos = int(sim_doc.get("acertos") or 0)
    perc = (acertos / total) * 100 if total else 0.0
    return total, acertos, perc



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

            # Edição de gabarito (direto no Gerenciar)
            st.markdown("**Editar gabarito:**")
            if r["tipo"] == "MC":
                alts = [r.get("op_a"), r.get("op_b"), r.get("op_c"), r.get("op_d"), r.get("op_e")]
                letras = ["A", "B", "C", "D", "E"]
                letras_validas = [letras[i] for i, a in enumerate(alts) if a]
                if not letras_validas:
                    letras_validas = ["A", "B", "C", "D", "E"]
                idx_sel = letras_validas.index(r.get("correta_text")) if r.get("correta_text") in letras_validas else 0
                novo_gab = st.selectbox("Letra correta", letras_validas, index=idx_sel, key=f"m_gab_{r['id']}")
                if st.button("Salvar gabarito", key=f"m_save_gab_{r['id']}"):
                    update_questao_gabarito(r["id"], novo_gab)
                    st.toast("Gabarito atualizado.")
                    st.rerun()
            else:
                opts = ["Verdadeiro", "Falso"]
                idx_sel = 0 if r.get("correta_text") == "V" else 1
                novo_gab_vf = st.radio("Gabarito", opts, index=idx_sel, key=f"m_gab_{r['id']}", horizontal=True)
                nova_correta = "V" if novo_gab_vf == "Verdadeiro" else "F"
                if st.button("Salvar gabarito", key=f"m_save_gab_{r['id']}"):
                    update_questao_gabarito(r["id"], nova_correta)
                    st.toast("Gabarito atualizado.")
                    st.rerun()

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

            # Desempenho visível para simulados finalizados (do simulado selecionado)
            if sel_sim.get("status") == "finished":
                sim_doc = get_simulado(sel_sim["id"])
                if sim_doc:
                    total_s, acertos_s, perc_s = simulado_overall_stats(sim_doc)

                    st.markdown("### 📊 Desempenho do simulado finalizado")
                    cA, cB, cC = st.columns([1, 1, 2])
                    with cA:
                        st.metric("Total", total_s)
                    with cB:
                        st.metric("Acertos", acertos_s)
                    with cC:
                        st.progress(int(perc_s), text=f"Aproveitamento: {perc_s:.1f}%")

                    rows_disc = simulado_stats_by_disciplina(sim_doc)
                    if rows_disc:
                        st.markdown("#### Desempenho por disciplina (neste simulado)")
                        st.dataframe(rows_disc, use_container_width=True, hide_index=True)
        else:
            st.caption("Nenhum simulado salvo ainda. Crie um novo abaixo.")

    # Resumo geral dos simulados finalizados (tabela por disciplina)
    sims_finished = [s for s in (sims or []) if s.get("status") == "finished"]
    if sims_finished:
        st.markdown("### 📈 Resumo dos simulados finalizados (por disciplina)")
        agg = {}
        for s in sims_finished:
            sim_doc = get_simulado(s["id"])
            if not sim_doc:
                continue
            rows = simulado_stats_by_disciplina(sim_doc)
            for r in rows:
                disc = r["Disciplina"]
                a = agg.setdefault(disc, {"Disciplina": disc, "Total": 0, "Acertos": 0})
                a["Total"] += int(r.get("Total", 0))
                a["Acertos"] += int(r.get("Acertos", 0))

        out_rows = []
        for disc, a in sorted(agg.items(), key=lambda x: x[0]):
            total = int(a["Total"])
            ac = int(a["Acertos"])
            perc = (ac / total) * 100 if total else 0.0
            out_rows.append({"Disciplina": disc, "Total": total, "Acertos": ac, "Aproveitamento (%)": round(perc, 1)})

        if out_rows:
            st.dataframe(out_rows, use_container_width=True, hide_index=True)
        else:
            st.caption("Ainda não há dados suficientes para o resumo por disciplina.")
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
        rows_disc = simulado_stats_by_disciplina(sim)
        if rows_disc:
            st.markdown("#### Desempenho por disciplina (neste simulado)")
            st.dataframe(rows_disc, use_container_width=True, hide_index=True)

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
# AUTH — helpers de senha
# =============================
ADMIN_LOGIN = "hamiltonbss"

def _hash_senha(senha: str) -> str:
    if _BCRYPT_OK:
        return bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
    import hashlib
    return hashlib.sha256(senha.encode()).hexdigest()

def _verificar_senha(senha: str, senha_hash: str) -> bool:
    if _BCRYPT_OK:
        try:
            return bcrypt.checkpw(senha.encode(), senha_hash.encode())
        except Exception:
            return False
    import hashlib
    return hashlib.sha256(senha.encode()).hexdigest() == senha_hash

# =============================
# AUTH — banco de dados de usuários
# =============================
def init_usuarios(db):
    """Cria índices e garante que o admin inicial existe."""
    try:
        db.usuarios.create_index([("login", ASCENDING)], name="uq_login", unique=True, background=True)
    except Exception:
        pass
    if db.usuarios.count_documents({"login": ADMIN_LOGIN}, limit=1) == 0:
        db.usuarios.insert_one({
            "nome": "Administrador",
            "login": ADMIN_LOGIN,
            "senha_hash": None,
            "perfil": "admin",
            "ativo": True,
            "primeiro_acesso": True,
            "data_criacao": datetime.now(timezone.utc).isoformat(),
            "data_ultima_atualizacao": datetime.now(timezone.utc).isoformat(),
        })

def get_usuario_por_login(login: str):
    db = get_db()
    return db.usuarios.find_one({"login": login.strip().lower()})

def criar_usuario(nome, login, perfil="usuario", criado_por="sistema"):
    db = get_db()
    login = login.strip().lower()
    if db.usuarios.find_one({"login": login}):
        raise ValueError(f"Login '{login}' já existe.")
    db.usuarios.insert_one({
        "nome": nome.strip(),
        "login": login,
        "senha_hash": None,
        "perfil": perfil,
        "ativo": True,
        "primeiro_acesso": True,
        "criado_por": criado_por,
        "data_criacao": datetime.now(timezone.utc).isoformat(),
        "data_ultima_atualizacao": datetime.now(timezone.utc).isoformat(),
    })

def atualizar_senha_usuario(login: str, nova_senha: str, primeiro_acesso=False):
    db = get_db()
    db.usuarios.update_one(
        {"login": login},
        {"$set": {
            "senha_hash": _hash_senha(nova_senha),
            "primeiro_acesso": primeiro_acesso,
            "data_ultima_atualizacao": datetime.now(timezone.utc).isoformat(),
        }}
    )

def listar_usuarios():
    db = get_db()
    return list(db.usuarios.find({}, {"_id": 0, "senha_hash": 0}).sort("data_criacao", ASCENDING))

def atualizar_usuario(login, campos: dict):
    db = get_db()
    campos["data_ultima_atualizacao"] = datetime.now(timezone.utc).isoformat()
    db.usuarios.update_one({"login": login}, {"$set": campos})

# =============================
# AUTH — telas de login / primeiro acesso
# =============================
def tela_login():
    st.title("🔐 Acesso ao Sistema")
    col, _ = st.columns([1, 2])
    with col:
        login_input = st.text_input("Usuário", key="login_input").strip().lower()
        senha_input = st.text_input("Senha", type="password", key="senha_input")
        entrar = st.button("Entrar", type="primary")

    if entrar:
        if not login_input:
            st.warning("Informe o usuário.")
            return
        usuario = get_usuario_por_login(login_input)
        if not usuario:
            st.error("Usuário não encontrado.")
            return
        if not usuario.get("ativo", True):
            st.error("Usuário inativo. Contate o administrador.")
            return
        if usuario.get("primeiro_acesso") or not usuario.get("senha_hash"):
            st.session_state["_auth_primeiro_acesso_login"] = login_input
            st.rerun()
            return
        if not _verificar_senha(senha_input, usuario["senha_hash"]):
            st.error("Senha incorreta.")
            return
        # Login OK
        st.session_state["_auth_usuario"] = {
            "login": usuario["login"],
            "nome": usuario.get("nome", ""),
            "perfil": usuario.get("perfil", "usuario"),
        }
        st.rerun()

def tela_primeiro_acesso(login: str):
    st.title("🔑 Primeiro Acesso — Defina sua senha")
    st.info(f"Bem-vindo(a), **{login}**! Por segurança, defina uma senha para continuar.")
    col, _ = st.columns([1, 2])
    with col:
        s1 = st.text_input("Nova senha", type="password", key="pa_s1")
        s2 = st.text_input("Confirmar senha", type="password", key="pa_s2")
        salvar = st.button("Definir senha", type="primary")
    if salvar:
        if len(s1) < 6:
            st.warning("A senha deve ter pelo menos 6 caracteres.")
            return
        if s1 != s2:
            st.error("As senhas não coincidem.")
            return
        atualizar_senha_usuario(login, s1, primeiro_acesso=False)
        st.success("Senha definida com sucesso! Faça login agora.")
        del st.session_state["_auth_primeiro_acesso_login"]
        st.rerun()

def checar_autenticacao():
    """
    Retorna True se o usuário está autenticado.
    Gerencia o fluxo de login / primeiro acesso.
    """
    db = get_db()
    init_usuarios(db)

    if "_auth_primeiro_acesso_login" in st.session_state:
        tela_primeiro_acesso(st.session_state["_auth_primeiro_acesso_login"])
        return False

    if "_auth_usuario" not in st.session_state:
        tela_login()
        return False

    return True

def auth_sidebar():
    """Exibe info do usuário e botão logout na sidebar."""
    u = st.session_state.get("_auth_usuario", {})
    with st.sidebar:
        st.caption(f"👤 {u.get('nome', u.get('login', ''))} ({u.get('perfil', '')})")
        if st.button("Sair", key="btn_logout"):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()

def is_admin():
    return st.session_state.get("_auth_usuario", {}).get("perfil") == "admin"

def login_atual():
    return st.session_state.get("_auth_usuario", {}).get("login", "")

# =============================
# GESTÃO DE USUÁRIOS — página admin
# =============================
def page_usuarios():
    if not is_admin():
        st.error("Acesso restrito a administradores.")
        return
    st.title("👥 Gestão de Usuários")

    # ---- Listagem ----
    usuarios = listar_usuarios()
    if usuarios:
        rows = []
        for u in usuarios:
            rows.append({
                "Login": u.get("login",""),
                "Nome": u.get("nome",""),
                "Perfil": u.get("perfil",""),
                "Ativo": "✅" if u.get("ativo") else "❌",
                "1º Acesso": "⏳" if u.get("primeiro_acesso") else "—",
                "Criado em": (u.get("data_criacao") or "")[:10],
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("Nenhum usuário cadastrado.")

    st.divider()

    # ---- Criar novo usuário ----
    with st.expander("➕ Criar novo usuário", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            novo_nome = st.text_input("Nome completo", key="nu_nome")
        with c2:
            novo_login = st.text_input("Login", key="nu_login")
        with c3:
            novo_perfil = st.selectbox("Perfil", ["usuario", "admin"], key="nu_perfil")
        if st.button("Criar usuário", key="btn_criar_usuario"):
            if not novo_nome or not novo_login:
                st.warning("Preencha nome e login.")
            else:
                try:
                    criar_usuario(novo_nome, novo_login, novo_perfil, criado_por=login_atual())
                    st.success(f"Usuário '{novo_login}' criado. O usuário definirá a senha no primeiro acesso.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

    # ---- Editar / Gerenciar ----
    if usuarios:
        st.subheader("Editar usuário")
        logins = [u["login"] for u in usuarios]
        sel_login = st.selectbox("Selecionar", logins, key="edit_u_sel")
        u_sel = next((u for u in usuarios if u["login"] == sel_login), None)
        if u_sel:
            c1, c2, c3 = st.columns(3)
            with c1:
                ed_nome = st.text_input("Nome", value=u_sel.get("nome",""), key="ed_nome")
            with c2:
                ed_perfil = st.selectbox("Perfil", ["usuario", "admin"],
                    index=0 if u_sel.get("perfil","usuario")=="usuario" else 1, key="ed_perfil")
            with c3:
                ed_ativo = st.checkbox("Ativo", value=bool(u_sel.get("ativo", True)), key="ed_ativo")

            b1, b2, b3 = st.columns(3)
            with b1:
                if st.button("Salvar alterações", key="btn_salvar_usuario"):
                    atualizar_usuario(sel_login, {"nome": ed_nome, "perfil": ed_perfil, "ativo": ed_ativo})
                    st.success("Usuário atualizado.")
                    st.rerun()
            with b2:
                if st.button("Resetar senha (novo 1º acesso)", key="btn_reset_pw"):
                    atualizar_usuario(sel_login, {"senha_hash": None, "primeiro_acesso": True})
                    st.success("Senha resetada. O usuário definirá nova senha no próximo acesso.")
                    st.rerun()
            with b3:
                if sel_login != ADMIN_LOGIN:
                    nova_pw = st.text_input("Definir senha manualmente", type="password", key="ed_pw")
                    if st.button("Definir senha", key="btn_def_pw"):
                        if len(nova_pw) < 6:
                            st.warning("Mínimo 6 caracteres.")
                        else:
                            atualizar_senha_usuario(sel_login, nova_pw, primeiro_acesso=False)
                            st.success("Senha definida.")

# =============================
# MÓDULO DE ESTUDOS — banco de dados
# =============================
def init_estudos(db):
    try:
        db.est_planos.create_index([("usuario_login", ASCENDING), ("nome", ASCENDING)], background=True)
        db.est_disciplinas.create_index([("plano_id", ASCENDING), ("nome", ASCENDING)], background=True)
        db.est_assuntos.create_index([("disciplina_id", ASCENDING)], background=True)
        db.est_planejamento.create_index([("plano_id", ASCENDING), ("data", ASCENDING)], background=True)
    except Exception:
        pass

# --- Planos ---
def est_listar_planos(usuario_login):
    db = get_db()
    return list(db.est_planos.find({"usuario_login": usuario_login}).sort("data_criacao", ASCENDING))

def est_criar_plano(usuario_login, nome):
    db = get_db()
    nome = nome.strip()
    if not nome:
        return None
    if db.est_planos.find_one({"usuario_login": usuario_login, "nome": nome}):
        return None
    res = db.est_planos.insert_one({
        "usuario_login": usuario_login,
        "nome": nome,
        "data_criacao": datetime.now(timezone.utc).isoformat(),
    })
    return str(res.inserted_id)

def est_excluir_plano(plano_id):
    db = get_db()
    oid = ObjectId(plano_id)
    discs = list(db.est_disciplinas.find({"plano_id": oid}, {"_id": 1}))
    for d in discs:
        db.est_assuntos.delete_many({"disciplina_id": d["_id"]})
    db.est_disciplinas.delete_many({"plano_id": oid})
    db.est_planejamento.delete_many({"plano_id": oid})
    db.est_planos.delete_one({"_id": oid})

def est_renomear_plano(plano_id, novo_nome):
    db = get_db()
    db.est_planos.update_one({"_id": ObjectId(plano_id)}, {"$set": {"nome": novo_nome.strip()}})

# --- Disciplinas ---
def est_listar_disciplinas(plano_id):
    db = get_db()
    return list(db.est_disciplinas.find({"plano_id": ObjectId(plano_id)}).sort("nome", ASCENDING))

def est_criar_disciplina(plano_id, nome):
    db = get_db()
    nome = nome.strip()
    if not nome:
        return None
    if db.est_disciplinas.find_one({"plano_id": ObjectId(plano_id), "nome": nome}):
        return None
    res = db.est_disciplinas.insert_one({
        "plano_id": ObjectId(plano_id),
        "nome": nome,
        "data_criacao": datetime.now(timezone.utc).isoformat(),
    })
    return str(res.inserted_id)

def est_excluir_disciplina(disc_id):
    db = get_db()
    oid = ObjectId(disc_id)
    db.est_assuntos.delete_many({"disciplina_id": oid})
    db.est_planejamento.delete_many({"disciplina_id": oid})
    db.est_disciplinas.delete_one({"_id": oid})

# --- Assuntos ---
def est_listar_assuntos(disc_id):
    """Retorna assuntos preservando ordem de inserção (campo 'ordem')."""
    db = get_db()
    return list(db.est_assuntos.find({"disciplina_id": ObjectId(disc_id)}).sort([("ordem", ASCENDING), ("_id", ASCENDING)]))

def est_importar_assuntos(disc_id, texto_colado):
    db = get_db()
    oid = ObjectId(disc_id)
    linhas = [l.strip() for l in texto_colado.splitlines() if l.strip()]
    inseridos = 0
    # Próximo índice de ordem pelo maior valor existente
    ultimo = db.est_assuntos.find_one({"disciplina_id": oid}, sort=[("ordem", DESCENDING)])
    proximo_ordem = (ultimo["ordem"] + 1) if ultimo and "ordem" in ultimo else 0
    nomes_existentes = {d["nome"] for d in db.est_assuntos.find({"disciplina_id": oid}, {"nome": 1})}
    for linha in linhas:
        if linha not in nomes_existentes:
            db.est_assuntos.insert_one({
                "disciplina_id": oid,
                "nome": linha,
                "ordem": proximo_ordem,
                "data_criacao": datetime.now(timezone.utc).isoformat(),
            })
            proximo_ordem += 1
            nomes_existentes.add(linha)
            inseridos += 1
    return inseridos

def est_editar_assunto(assunto_id, novo_nome):
    db = get_db()
    db.est_assuntos.update_one({"_id": ObjectId(assunto_id)}, {"$set": {"nome": novo_nome.strip()}})

def est_excluir_assunto(assunto_id):
    db = get_db()
    db.est_assuntos.delete_one({"_id": ObjectId(assunto_id)})
    db.est_planejamento.delete_many({"assunto_id": ObjectId(assunto_id)})

# --- Planejamento ---
def est_alocar_assunto(plano_id, data_str, assunto_id, disciplina_id, disciplina_nome, assunto_nome):
    db = get_db()
    existe = db.est_planejamento.find_one({
        "plano_id": ObjectId(plano_id),
        "data": data_str,
        "assunto_id": ObjectId(assunto_id),
    })
    if existe:
        return False
    db.est_planejamento.insert_one({
        "plano_id": ObjectId(plano_id),
        "data": data_str,
        "assunto_id": ObjectId(assunto_id),
        "disciplina_id": ObjectId(disciplina_id),
        "disciplina_nome": disciplina_nome,
        "assunto_nome": assunto_nome,
        "tipo": "assunto",          # assunto | atividade | revisao
        "status": "pendente",
        "links": [],
        "data_criacao": datetime.now(timezone.utc).isoformat(),
    })
    return True

def est_realocar_assunto(item_id, nova_data_str):
    """Move um item planejado para outra data."""
    db = get_db()
    db.est_planejamento.update_one(
        {"_id": ObjectId(item_id)},
        {"$set": {"data": nova_data_str, "data_atualizacao": datetime.now(timezone.utc).isoformat()}}
    )

def est_adicionar_atividade(plano_id, data_str, titulo, descricao=""):
    """Adiciona atividade manual (não vinculada a assunto/disciplina)."""
    db = get_db()
    db.est_planejamento.insert_one({
        "plano_id": ObjectId(plano_id),
        "data": data_str,
        "assunto_id": None,
        "disciplina_id": None,
        "disciplina_nome": "",
        "assunto_nome": titulo.strip(),
        "descricao": descricao.strip(),
        "tipo": "atividade",
        "status": "pendente",
        "links": [],
        "data_criacao": datetime.now(timezone.utc).isoformat(),
    })

def est_agendar_revisoes(plano_id, item_id, intervalos=None):
    """
    Agenda revisões espaçadas a partir do item original.
    intervalos: lista de dias após a data do item original. Ex: [1, 7, 30]
    """
    from datetime import timedelta
    if intervalos is None:
        intervalos = [1, 7, 30]
    db = get_db()
    origem = db.est_planejamento.find_one({"_id": ObjectId(item_id)})
    if not origem:
        return 0
    data_base = datetime.strptime(origem["data"], "%Y-%m-%d").date()
    agendadas = 0
    for dias in intervalos:
        data_rev = data_base + timedelta(days=dias)
        data_str = data_rev.strftime("%Y-%m-%d")
        # Evita duplicar revisão do mesmo assunto no mesmo dia
        ja_existe = db.est_planejamento.find_one({
            "plano_id": ObjectId(plano_id),
            "data": data_str,
            "assunto_id": origem.get("assunto_id"),
            "tipo": "revisao",
        })
        if not ja_existe:
            db.est_planejamento.insert_one({
                "plano_id": ObjectId(plano_id),
                "data": data_str,
                "assunto_id": origem.get("assunto_id"),
                "disciplina_id": origem.get("disciplina_id"),
                "disciplina_nome": origem.get("disciplina_nome", ""),
                "assunto_nome": origem.get("assunto_nome", ""),
                "tipo": "revisao",
                "status": "pendente",
                "links": [],
                "revisao_origem_id": item_id,
                "revisao_dias": dias,
                "data_criacao": datetime.now(timezone.utc).isoformat(),
            })
            agendadas += 1
    return agendadas

def est_remover_planejamento(item_id):
    db = get_db()
    db.est_planejamento.delete_one({"_id": ObjectId(item_id)})

def est_marcar_status(item_id, status, plano_id=None, agendar_revisoes_auto=False, intervalos_revisao=None):
    db = get_db()
    db.est_planejamento.update_one(
        {"_id": ObjectId(item_id)},
        {"$set": {"status": status, "data_atualizacao": datetime.now(timezone.utc).isoformat()}}
    )
    # Se marcou como estudado e revisão automática está ativa
    if status == "estudado" and agendar_revisoes_auto and plano_id:
        est_agendar_revisoes(plano_id, item_id, intervalos_revisao)

def est_buscar_planejamento_periodo(plano_id, data_inicio, data_fim):
    db = get_db()
    d_ini = data_inicio.strftime("%Y-%m-%d")
    d_fim = data_fim.strftime("%Y-%m-%d")
    docs = list(db.est_planejamento.find({
        "plano_id": ObjectId(plano_id),
        "data": {"$gte": d_ini, "$lte": d_fim}
    }))
    por_data = {}
    for d in docs:
        key = d["data"]
        por_data.setdefault(key, []).append({
            "id": str(d["_id"]),
            "assunto_id": str(d.get("assunto_id", "")) if d.get("assunto_id") else "",
            "disciplina_nome": d.get("disciplina_nome", ""),
            "assunto_nome": d.get("assunto_nome", ""),
            "descricao": d.get("descricao", ""),
            "tipo": d.get("tipo", "assunto"),
            "status": d.get("status", "pendente"),
            "links": d.get("links", []),
            "disciplina_id": str(d.get("disciplina_id", "")) if d.get("disciplina_id") else "",
            "questionarios_vinculados": d.get("questionarios_vinculados", []),
        })
    return por_data

def est_distribuir_disciplina(plano_id, disc_id, disc_nome, data_inicio, data_fim,
                               dias_semana_ativos=None, intervalo=1):
    """
    Distribui assuntos preservando ordem de importação.
    intervalo=1 → consecutivo | 2 → um dia sim, um não | N → pula N-1 dias entre alocações.
    """
    from datetime import timedelta
    assuntos = est_listar_assuntos(disc_id)   # já ordenado por 'ordem'
    if not assuntos:
        return 0, 0

    if dias_semana_ativos is None:
        dias_semana_ativos = list(range(7))

    dias_base = []
    d = data_inicio
    while d <= data_fim:
        if d.weekday() in dias_semana_ativos:
            dias_base.append(d)
        d += timedelta(days=1)

    if not dias_base:
        return 0, 0

    intervalo = max(1, int(intervalo))
    dias_disponiveis = dias_base[::intervalo]

    alocados, ja_existiam = 0, 0
    for i, assunto in enumerate(assuntos):
        dia_alvo = dias_disponiveis[i % len(dias_disponiveis)]
        data_str = dia_alvo.strftime("%Y-%m-%d")
        ok = est_alocar_assunto(
            plano_id, data_str,
            str(assunto["_id"]), disc_id,
            disc_nome, assunto["nome"]
        )
        if ok:
            alocados += 1
        else:
            ja_existiam += 1

    return alocados, ja_existiam

def est_adicionar_link(item_id, titulo, url):
    db = get_db()
    db.est_planejamento.update_one(
        {"_id": ObjectId(item_id)},
        {"$push": {"links": {"titulo": titulo, "url": url}}}
    )

def est_remover_link(item_id, idx_link):
    db = get_db()
    doc = db.est_planejamento.find_one({"_id": ObjectId(item_id)})
    if doc:
        links = doc.get("links", [])
        if 0 <= idx_link < len(links):
            links.pop(idx_link)
            db.est_planejamento.update_one({"_id": ObjectId(item_id)}, {"$set": {"links": links}})

def est_vincular_questionario(item_id, questionario_id, questionario_nome, disciplina_nome):
    """Vincula um questionário existente a um item do planejamento."""
    db = get_db()
    doc = db.est_planejamento.find_one({"_id": ObjectId(item_id)})
    if not doc:
        return False
    vinculados = doc.get("questionarios_vinculados", [])
    # Evita duplicatas
    if any(v["questionario_id"] == questionario_id for v in vinculados):
        return False
    vinculados.append({
        "questionario_id": questionario_id,
        "questionario_nome": questionario_nome,
        "disciplina_nome": disciplina_nome,
    })
    db.est_planejamento.update_one(
        {"_id": ObjectId(item_id)},
        {"$set": {"questionarios_vinculados": vinculados}}
    )
    return True

def est_desvincular_questionario(item_id, questionario_id):
    """Remove vínculo de um questionário de um item do planejamento."""
    db = get_db()
    doc = db.est_planejamento.find_one({"_id": ObjectId(item_id)})
    if not doc:
        return
    vinculados = [v for v in doc.get("questionarios_vinculados", []) if v["questionario_id"] != questionario_id]
    db.est_planejamento.update_one(
        {"_id": ObjectId(item_id)},
        {"$set": {"questionarios_vinculados": vinculados}}
    )

# =============================
# MÓDULO DE ESTUDOS — helpers
# =============================
def _semana_inicio_fim(ref):
    from datetime import timedelta
    segunda = ref - timedelta(days=ref.weekday())
    domingo = segunda + timedelta(days=6)
    return segunda, domingo

def _badge(tipo):
    return {"assunto": "", "atividade": "🔧", "revisao": "🔁"}.get(tipo, "")

def _cor_tipo(tipo):
    return {"assunto": "#19747E", "atividade": "#6f42c1", "revisao": "#0d6efd"}.get(tipo, "#19747E")

# =============================
# MÓDULO DE ESTUDOS — tela inicial (lista de planos)
# =============================
def page_estudos():
    st.title("📅 Plano de Estudos")
    usuario = login_atual()
    db = get_db()
    init_estudos(db)

    if st.session_state.get("est_plano_aberto_id"):
        _page_estudos_plano(st.session_state["est_plano_aberto_id"])
        return

    st.subheader("Seus planos de estudo")
    planos = est_listar_planos(usuario)

    if not planos:
        st.info("Nenhum plano criado ainda. Crie o primeiro abaixo.")
    else:
        for p in planos:
            pid = str(p["_id"])
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([5, 2, 2, 1])
                with c1:
                    st.markdown(f"### 📋 {p['nome']}")
                    criado = (p.get("data_criacao") or "")[:10]
                    if criado:
                        st.caption(f"Criado em {criado}")
                with c2:
                    if st.button("📖 Abrir", key=f"est_abrir_{pid}", use_container_width=True):
                        st.session_state["est_plano_aberto_id"] = pid
                        st.session_state["est_semana_ref"] = date.today()
                        st.rerun()
                with c3:
                    novo_nome_p = st.text_input("", value=p["nome"], key=f"est_rename_{pid}",
                                                label_visibility="collapsed", placeholder="Renomear...")
                    if st.button("✏️ Renomear", key=f"est_btn_rename_{pid}", use_container_width=True):
                        if novo_nome_p.strip():
                            est_renomear_plano(pid, novo_nome_p)
                            st.rerun()
                with c4:
                    if st.button("🗑️", key=f"est_del_plano_{pid}", help="Excluir plano"):
                        st.session_state[f"est_confirm_del_plano_{pid}"] = True
                        st.rerun()

                if st.session_state.get(f"est_confirm_del_plano_{pid}"):
                    st.warning(f"Confirma exclusão do plano **{p['nome']}** e todos os seus dados?")
                    cc1, cc2 = st.columns(2)
                    with cc1:
                        if st.button("Sim, excluir", key=f"est_confirma_del_{pid}", type="primary"):
                            est_excluir_plano(pid)
                            st.session_state.pop(f"est_confirm_del_plano_{pid}", None)
                            st.rerun()
                    with cc2:
                        if st.button("Cancelar", key=f"est_cancela_del_{pid}"):
                            st.session_state.pop(f"est_confirm_del_plano_{pid}", None)
                            st.rerun()

    st.divider()
    st.subheader("➕ Criar novo plano")
    c1, c2 = st.columns([3, 1])
    with c1:
        novo_plano_nome = st.text_input("Nome do plano", key="est_novo_plano_nome",
                                        placeholder="Ex: Concurso TJDF 2025")
    with c2:
        st.write("")
        st.write("")
        if st.button("Criar plano", key="est_btn_criar_plano", type="primary"):
            if novo_plano_nome.strip():
                pid = est_criar_plano(usuario, novo_plano_nome.strip())
                if pid:
                    st.session_state["est_plano_aberto_id"] = pid
                    st.session_state["est_semana_ref"] = date.today()
                    st.rerun()
                else:
                    st.warning("Já existe um plano com esse nome.")
            else:
                st.warning("Informe um nome para o plano.")


# =============================
# MÓDULO DE ESTUDOS — página interna do plano
# =============================
def _page_estudos_plano(plano_id):
    from datetime import timedelta
    db = get_db()
    plano_doc = db.est_planos.find_one({"_id": ObjectId(plano_id)})
    if not plano_doc:
        st.error("Plano não encontrado.")
        st.session_state.pop("est_plano_aberto_id", None)
        st.rerun()
        return

    plano_nome = plano_doc.get("nome", "Plano")
    usuario = login_atual()
    hoje = date.today()

    # Cabeçalho
    col_back, col_title = st.columns([1, 7])
    with col_back:
        if st.button("← Voltar", key="est_voltar_planos"):
            st.session_state.pop("est_plano_aberto_id", None)
            st.rerun()
    with col_title:
        st.title(f"📋 {plano_nome}")

    # Configurações de revisão (na sidebar do plano)
    INTERVALOS_REVISAO = [1, 7, 30]  # fixo: +1, +7, +30 dias
    with st.sidebar:
        st.divider()
        st.markdown("**⚙️ Revisão automática**")
        rev_auto = st.checkbox("Agendar revisões ao marcar estudado",
                               value=st.session_state.get("est_rev_auto", False),
                               key="est_rev_auto")
        if rev_auto:
            st.caption("Ao marcar um assunto como estudado, revisões serão agendadas automaticamente em **+1, +7 e +30 dias**.")
    intervalos_rev = INTERVALOS_REVISAO if rev_auto else []

    # Navegação semanal
    if "est_semana_ref" not in st.session_state:
        st.session_state["est_semana_ref"] = hoje

    ref = st.session_state["est_semana_ref"]
    segunda, domingo = _semana_inicio_fim(ref)

    meses_pt = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"]
    nav1, nav2, nav3, nav4 = st.columns([1, 1, 3, 1])
    with nav1:
        if st.button("◀ Anterior", key="est_sem_ant"):
            st.session_state["est_semana_ref"] = segunda - timedelta(days=7)
            st.rerun()
    with nav2:
        if st.button("📌 Hoje", key="est_hoje"):
            st.session_state["est_semana_ref"] = hoje
            st.rerun()
    with nav3:
        l_ini = f"{segunda.day}/{meses_pt[segunda.month-1]}/{segunda.year}"
        l_fim = f"{domingo.day}/{meses_pt[domingo.month-1]}/{domingo.year}"
        st.markdown(
            f"<h3 style='text-align:center;margin:4px 0'>Semana: {l_ini} → {l_fim}</h3>",
            unsafe_allow_html=True
        )
    with nav4:
        if st.button("Próxima ▶", key="est_sem_prox"):
            st.session_state["est_semana_ref"] = segunda + timedelta(days=7)
            st.rerun()

    st.divider()

    col_agenda, col_painel = st.columns([3, 1])

    # ========== PAINEL LATERAL ==========
    with col_painel:
        st.subheader("📚 Disciplinas")
        disciplinas = est_listar_disciplinas(plano_id)

        with st.expander("➕ Nova disciplina"):
            nd = st.text_input("Nome", key="est_nd_nome")
            if st.button("Criar", key="est_btn_criar_disc"):
                if nd.strip():
                    res = est_criar_disciplina(plano_id, nd.strip())
                    if res:
                        st.success("Criada!")
                        st.rerun()
                    else:
                        st.warning("Já existe ou nome inválido.")

        if disciplinas:
            disc_nomes = {str(d["_id"]): d["nome"] for d in disciplinas}
            disc_sel_id = st.selectbox(
                "Disciplina ativa",
                list(disc_nomes.keys()),
                format_func=lambda x: disc_nomes[x],
                key="est_disc_sel"
            )
            disc_sel_nome = disc_nomes.get(disc_sel_id, "")

            with st.expander("📥 Importar assuntos (um por linha)"):
                texto_col = st.text_area("Cole aqui:", height=110, key="est_import_txt")
                if st.button("Importar", key="est_btn_import"):
                    if texto_col.strip():
                        n = est_importar_assuntos(disc_sel_id, texto_col)
                        st.success(f"{n} assunto(s) importado(s).")
                        st.rerun()

            with st.expander("🗓️ Distribuir todos os assuntos"):
                st.caption("Distribui na ordem de importação, com intervalo configurável.")
                d_ini = st.date_input("Data início", value=segunda, key="est_dist_ini")
                d_fim = st.date_input("Data fim", value=segunda + timedelta(days=29), key="est_dist_fim")
                intervalo_val = st.number_input(
                    "Intervalo entre alocações (dias)",
                    min_value=1, max_value=30, value=1, step=1, key="est_dist_intervalo",
                    help="1=consecutivo | 2=um dia sim, um não | 3=aloca, pula 2, aloca…"
                )
                dias_labels = ["Seg","Ter","Qua","Qui","Sex","Sáb","Dom"]
                dias_sel = st.multiselect(
                    "Dias da semana permitidos",
                    options=list(range(7)), default=list(range(5)),
                    format_func=lambda x: dias_labels[x], key="est_dist_dias"
                )
                assuntos_preview = est_listar_assuntos(disc_sel_id)
                st.caption(f"{len(assuntos_preview)} assunto(s) serão distribuídos na ordem de importação.")
                if st.button("Distribuir", key="est_btn_distribuir", type="primary"):
                    if not dias_sel:
                        st.warning("Selecione ao menos um dia.")
                    elif d_fim < d_ini:
                        st.warning("Data fim deve ser maior que data início.")
                    else:
                        alocados, ja_ex = est_distribuir_disciplina(
                            plano_id, disc_sel_id, disc_sel_nome,
                            d_ini, d_fim, dias_sel, intervalo_val
                        )
                        if alocados:
                            st.success(f"✅ {alocados} assunto(s) distribuído(s).")
                        if ja_ex:
                            st.info(f"{ja_ex} já estavam alocados (ignorados).")
                        if alocados:
                            st.rerun()

            # Assuntos em dropdown
            assuntos = est_listar_assuntos(disc_sel_id)
            st.caption(f"**{len(assuntos)} assunto(s)**")

            if assuntos:
                assunto_opcoes = {str(a["_id"]): a["nome"] for a in assuntos}
                assunto_sel_id = st.selectbox(
                    "Assunto",
                    list(assunto_opcoes.keys()),
                    format_func=lambda x: assunto_opcoes[x],
                    key="est_assunto_sel"
                )
                assunto_sel_nome = assunto_opcoes.get(assunto_sel_id, "")

                with st.expander("✏️ Editar assunto selecionado"):
                    novo_nome_a = st.text_input("Nome", value=assunto_sel_nome, key="est_edit_assunto_nome")
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("💾 Salvar", key="est_salvar_assunto"):
                            if novo_nome_a.strip():
                                est_editar_assunto(assunto_sel_id, novo_nome_a)
                                st.success("Salvo.")
                                st.rerun()
                    with c2:
                        if st.button("🗑️ Excluir", key="est_del_assunto"):
                            est_excluir_assunto(assunto_sel_id)
                            st.rerun()

                st.markdown("**Alocar assunto no dia:**")
                dia_sel = st.date_input("", value=hoje, key="est_dia_sel", label_visibility="collapsed")
                if st.button("📌 Alocar", key="est_btn_alocar", use_container_width=True):
                    ok = est_alocar_assunto(
                        plano_id, dia_sel.strftime("%Y-%m-%d"),
                        assunto_sel_id, disc_sel_id, disc_sel_nome, assunto_sel_nome
                    )
                    if ok:
                        st.success("Alocado!")
                        st.rerun()
                    else:
                        st.warning("Já alocado neste dia.")

            with st.expander("🗑️ Excluir disciplina"):
                st.warning("Remove a disciplina, assuntos e planejamentos vinculados.")
                confirma = st.checkbox(f'Confirmo exclusão de "{disc_sel_nome}"', key="est_conf_del_disc")
                if st.button("Excluir disciplina", key="est_btn_del_disc", disabled=not confirma):
                    est_excluir_disciplina(disc_sel_id)
                    st.success("Disciplina excluída.")
                    st.rerun()
        else:
            st.info("Nenhuma disciplina ainda.")



    # ========== AGENDA SEMANAL ==========
    with col_agenda:
        planejamento = est_buscar_planejamento_periodo(plano_id, segunda, domingo)
        dias_semana_nomes = ["Segunda-feira","Terça-feira","Quarta-feira",
                             "Quinta-feira","Sexta-feira","Sábado","Domingo"]

        for offset in range(7):
            dia_date = segunda + timedelta(days=offset)
            data_str = dia_date.strftime("%Y-%m-%d")
            itens = planejamento.get(data_str, [])
            eh_hoje = (dia_date == hoje)

            n_ok   = sum(1 for i in itens if i["status"] == "estudado")
            n_pend = len(itens) - n_ok

            if itens:
                bg_header = "#d4edda" if n_pend == 0 else "#fff3cd"
                status_dia = "concluído" if n_pend == 0 else (f"{n_ok}/{len(itens)} feitos" if n_ok > 0 else "pendente")
            else:
                bg_header = "#f0f2f6"
                status_dia = "livre"

            borda_cor = "#19747E" if eh_hoje else "#dee2e6"
            borda_esp = "3px" if eh_hoje else "1px"
            hoje_tag  = " · <b style='color:#19747E'>HOJE</b>" if eh_hoje else ""
            n_tag     = f" · {len(itens)} item(ns)" if itens else ""

            st.markdown(
                f"<div style='background:{bg_header};border:{borda_esp} solid {borda_cor};"
                f"border-radius:8px;padding:7px 14px;margin-bottom:4px'>"
                f"<span style='font-size:14px;font-weight:700;color:#19747E'>"
                f"{dias_semana_nomes[offset]}, {dia_date.day:02d}/{dia_date.month:02d}"
                f"{hoje_tag}</span>"
                f"<span style='font-size:11px;color:#666'>{n_tag} &nbsp;·&nbsp; {status_dia}</span>"
                f"</div>",
                unsafe_allow_html=True
            )

            # -- Itens do dia --
            for item in itens:
                tipo        = item.get("tipo", "assunto")
                feito       = item["status"] == "estudado"
                cor_tipo    = _cor_tipo(tipo)
                cor_item    = "#28a745" if feito else cor_tipo
                tipo_badge  = {"atividade": "Atividade", "revisao": "Revisão"}.get(tipo, "")
                novo_status = "pendente" if feito else "estudado"
                label_feito = "Desfazer" if feito else "Marcar feito"

                with st.container(border=True):
                    # -- Título (largura total) --
                    disc_part = (
                        f"<span style='color:{cor_item};font-weight:600'>"
                        f"{item['disciplina_nome']}</span> — "
                    ) if item["disciplina_nome"] else ""
                    badge_html = (
                        f"<span style='font-size:10px;background:{cor_tipo};color:#fff;"
                        f"border-radius:3px;padding:1px 6px;margin-right:6px'>{tipo_badge}</span>"
                    ) if tipo_badge else ""
                    feito_html = (
                        "<span style='font-size:10px;background:#28a745;color:#fff;"
                        "border-radius:3px;padding:1px 6px;margin-right:6px'>✓ Feito</span>"
                    ) if feito else ""
                    st.markdown(
                        f"{feito_html}{badge_html}{disc_part}{item['assunto_nome']}",
                        unsafe_allow_html=True
                    )
                    if item.get("descricao"):
                        st.caption(item["descricao"])

                    # -- Botões de ação (linha fina abaixo do título) --
                    ba, bb, bc, _esp = st.columns([2, 1, 1, 4])
                    with ba:
                        if st.button(label_feito, key=f"est_mk_{item['id']}"):
                            est_marcar_status(
                                item["id"], novo_status,
                                plano_id=plano_id,
                                agendar_revisoes_auto=rev_auto,
                                intervalos_revisao=intervalos_rev if rev_auto else []
                            )
                            st.rerun()
                    with bb:
                        if st.button("Mover", key=f"est_realoc_btn_{item['id']}"):
                            if st.session_state.get("est_realocando_id") == item["id"]:
                                st.session_state.pop("est_realocando_id", None)
                            else:
                                st.session_state["est_realocando_id"] = item["id"]
                            st.rerun()
                    with bc:
                        if st.button("Excluir", key=f"est_rm_{item['id']}"):
                            est_remover_planejamento(item["id"])
                            st.session_state.pop("est_realocando_id", None)
                            st.rerun()

                    # -- Painel mover --
                    if st.session_state.get("est_realocando_id") == item["id"]:
                        with st.container():
                            st.caption("Mover para:")
                            rc1, rc2, rc3 = st.columns([3, 1, 1])
                            with rc1:
                                nova_data = st.date_input(
                                    "", value=dia_date, label_visibility="collapsed",
                                    key=f"est_nova_data_{item['id']}"
                                )
                            with rc2:
                                if st.button("Confirmar", key=f"est_confirmar_realoc_{item['id']}",
                                             type="primary", use_container_width=True):
                                    est_realocar_assunto(item["id"], nova_data.strftime("%Y-%m-%d"))
                                    st.session_state.pop("est_realocando_id", None)
                                    st.rerun()
                            with rc3:
                                if st.button("Cancelar", key=f"est_cancelar_realoc_{item['id']}",
                                             use_container_width=True):
                                    st.session_state.pop("est_realocando_id", None)
                                    st.rerun()

                    # -- Links externos --
                    lnks = item.get("links", [])
                    if lnks:
                        for li, lnk in enumerate(lnks):
                            lc1, lc2 = st.columns([8, 1])
                            with lc1:
                                st.markdown(
                                    f"<span style='font-size:12px'>🔗 <a href='{lnk['url']}' target='_blank'>{lnk['titulo']}</a></span>",
                                    unsafe_allow_html=True
                                )
                            with lc2:
                                if st.button("✕", key=f"est_rl_{item['id']}_{li}",
                                             help="Remover link"):
                                    est_remover_link(item["id"], li)
                                    st.rerun()

                    # -- Questionários vinculados --
                    qvs = item.get("questionarios_vinculados", [])
                    if qvs:
                        st.caption("Questionários vinculados:")
                        for qv in qvs:
                            qvc1, qvc2 = st.columns([8, 1])
                            with qvc1:
                                if st.button(
                                    f"▶  Praticar: {qv['questionario_nome']}",
                                    key=f"est_pratico_{item['id']}_{qv['questionario_id']}",
                                    use_container_width=True
                                ):
                                    st.session_state["current_qid"] = qv["questionario_id"]
                                    st.session_state["go_to"] = "Praticar"
                                    st.rerun()
                            with qvc2:
                                if st.button("✕", key=f"est_desvq_{item['id']}_{qv['questionario_id']}",
                                             help="Desvincular questionário"):
                                    est_desvincular_questionario(item["id"], qv["questionario_id"])
                                    st.rerun()

                    # -- Expanders de ação --
                    exp1, exp2 = st.columns(2)
                    with exp1:
                        with st.expander("Vincular questionário"):
                            todos_qs = get_questionarios()
                            qs_uteis = [q for q in todos_qs if q.get("disciplina") != "— Sistema —"]
                            if not qs_uteis:
                                st.caption("Nenhum questionário disponível.")
                            else:
                                discs_qs = sorted({(q.get("disciplina") or "Sem Disciplina") for q in qs_uteis
                                                   if q.get("disciplina") not in ("— Sistema —", None)})
                                disc_filt = st.selectbox(
                                    "Disciplina", ["Todas"] + discs_qs,
                                    key=f"est_qfilt_disc_{item['id']}"
                                )
                                qs_filtrados = qs_uteis if disc_filt == "Todas" else [
                                    q for q in qs_uteis
                                    if (q.get("disciplina") or "Sem Disciplina") == disc_filt
                                ]
                                busca_q = st.text_input(
                                    "Buscar", key=f"est_qbusca_{item['id']}",
                                    placeholder="Nome do questionário..."
                                )
                                if busca_q:
                                    qs_filtrados = [q for q in qs_filtrados
                                                    if busca_q.lower() in q["nome"].lower()]
                                if qs_filtrados:
                                    q_opcoes = {q["id"]: q["nome"] for q in qs_filtrados}
                                    q_sel_id = st.selectbox(
                                        "Questionário", list(q_opcoes.keys()),
                                        format_func=lambda x: q_opcoes[x],
                                        key=f"est_qsel_{item['id']}"
                                    )
                                    q_sel = next((q for q in qs_filtrados if q["id"] == q_sel_id), None)
                                    if st.button("Vincular", key=f"est_qvincular_{item['id']}",
                                                 type="primary", use_container_width=True):
                                        if q_sel:
                                            ok = est_vincular_questionario(
                                                item["id"], q_sel["id"],
                                                q_sel["nome"], q_sel.get("disciplina", "")
                                            )
                                            if ok:
                                                st.success("Vinculado!")
                                                st.rerun()
                                            else:
                                                st.info("Já vinculado.")
                                else:
                                    st.caption("Nenhum questionário encontrado.")
                    with exp2:
                        with st.expander("Adicionar link externo"):
                            with st.form(key=f"est_add_link_{item['id']}"):
                                lt = st.text_input("Título", key=f"est_lt_{item['id']}")
                                lu = st.text_input("URL", key=f"est_lu_{item['id']}")
                                if st.form_submit_button("Adicionar"):
                                    if lt and lu:
                                        est_adicionar_link(item["id"], lt, lu)
                                        st.rerun()

            # -- Adicionar atividade manual --
            with st.expander(f"Adicionar atividade — {dias_semana_nomes[offset].split('-')[0]}, {dia_date.day:02d}/{dia_date.month:02d}"):
                with st.form(key=f"est_ativ_{data_str}"):
                    ativ_titulo = st.text_input("Título", key=f"est_ativ_titulo_{data_str}")
                    ativ_desc   = st.text_area("Descrição (opcional)", height=50,
                                               key=f"est_ativ_desc_{data_str}")
                    if st.form_submit_button("Adicionar"):
                        if ativ_titulo.strip():
                            est_adicionar_atividade(plano_id, data_str, ativ_titulo, ativ_desc)
                            st.rerun()
                        else:
                            st.warning("Informe um título.")

            st.markdown("<div style='margin-bottom:4px'></div>", unsafe_allow_html=True)



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

    # ---- Autenticação ----
    if not checar_autenticacao():
        st.stop()

    auth_sidebar()

    st.session_state.setdefault("nav_choice", "Painel")
    if "go_to" in st.session_state:
        st.session_state["nav_choice"] = st.session_state.pop("go_to")

    nav_pages = ["Painel", "Plano de Estudos", "Praticar", "Gerenciar", "Importar CSV", "Simulados"]
    if is_admin():
        nav_pages.append("Usuários")

    with st.sidebar:
        st.header("Navegação")
        choice = st.radio("Ir para", nav_pages, key="nav_choice")

    if choice == "Painel":
        page_dashboard()
    elif choice == "Plano de Estudos":
        page_estudos()
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
    elif choice == "Usuários":
        page_usuarios()

if __name__ == "__main__":
    main()
