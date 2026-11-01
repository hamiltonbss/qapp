import os
import io
import csv
import re
import json
import html
import random
import hashlib
import secrets
from datetime import datetime, timezone, date, timedelta

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd

from pymongo import MongoClient, ASCENDING, DESCENDING
from bson import ObjectId

try:
    import bcrypt
    _BCRYPT_OK = True
except ImportError:
    _BCRYPT_OK = False

try:
    from zoneinfo import ZoneInfo
    FUSO = ZoneInfo("America/Sao_Paulo")
except Exception:
    FUSO = timezone.utc


# =============================
# Config & Globals
# =============================
def _secret(nome, padrao=""):
    """Lê de st.secrets e, na falta, de variável de ambiente."""
    try:
        v = st.secrets.get(nome)
    except Exception:
        v = None
    if v in (None, ""):
        return os.environ.get(nome, padrao)
    return v


MONGO_URI = _secret("MONGO_URI", "")
MONGO_DB_NAME = _secret("MONGO_DB_NAME", "quiz_app")

ADMIN_LOGIN = _secret("ADMIN_LOGIN", "").strip().lower()
SISTEMA = "— Sistema —"
LETRAS = "ABCDE"

# Cadernos especiais (um par por usuário)
ESPECIAIS = {"favoritos": "Favoritos", "erros": "Caderno de Erros"}
ESPECIAIS_DESC = {
    "favoritos": "Questões salvas como favoritas.",
    "erros": "Questões respondidas incorretamente.",
}
# Acertos seguidos, dentro do Caderno de Erros, para a questão sair dele
ACERTOS_PARA_SAIR_DO_CADERNO = 2

# Simulados: opções de questões por página (0 = todas, página única)
POR_PAGINA_OPCOES = [0, 1, 5, 10, 20]

st.set_page_config(page_title="Estudos | Questionários & Simulados", layout="wide")


# =============================
# Utilitários
# =============================
def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _oid(x):
    try:
        return x if isinstance(x, ObjectId) else ObjectId(str(x))
    except Exception:
        return None


def _parse_iso(s):
    try:
        dt = datetime.fromisoformat(str(s))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _fmt_dt(s):
    dt = _parse_iso(s)
    return dt.astimezone(FUSO).strftime("%d/%m/%Y %H:%M") if dt else ""


def _fmt_duracao(seg):
    if seg is None:
        return "—"
    seg = int(seg)
    h, m, s = seg // 3600, (seg % 3600) // 60, seg % 60
    if h:
        return f"{h}h {m:02d}min"
    if m:
        return f"{m}min {s:02d}s"
    return f"{s}s"


def _fmt_pp(v):
    return "Todas (página única)" if v == 0 else f"{v} por página"


def esc(s):
    """Escapa texto para uso dentro de HTML."""
    return html.escape(str(s if s is not None else ""))


_TAGS_PERMITIDAS = "b|i|u|strong|em|br|p|ul|ol|li|sub|sup|code"


def esc_rich(s):
    """Escapa HTML preservando tags simples de formatação e quebras de linha."""
    t = esc(s)
    t = re.sub(rf"&lt;(/?)({_TAGS_PERMITIDAS})\s*/?&gt;", r"<\1\2>", t, flags=re.I)
    return t.replace("\n", "<br>")


def safe_url(u):
    """Aceita apenas links http(s) (bloqueia javascript: e afins)."""
    u = str(u or "").strip()
    return u if u.lower().startswith(("http://", "https://")) else ""


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
        label, [data-testid="stWidgetLabel"], .stMarkdown {
            color: #222222;
        }

        /* Cards/containers com borda suave */
        [data-testid="stVerticalBlockBorderWrapper"] {
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

        div[role="radiogroup"] label, div[role="checkbox"] label {
            color: #222222 !important;
        }

        /* Caixa de explicação */
        .exp-box {
            background-color:#fff8c4; padding:14px; border-radius:6px;
            border:1px solid #e6d97a; margin-top:10px;
        }

        /* ---------- Plano de estudos: cards da agenda ---------- */
        .tf-card {
            background: #ffffff; border-radius: 12px;
            padding: 20px 24px 16px 24px; margin-bottom: 10px;
            border-left: 4px solid #19747E; box-shadow: 0 1px 6px rgba(0,0,0,0.07);
        }
        .tf-card.feito     { border-left-color: #28a745; background: #f6fbf7; }
        .tf-card.revisao   { border-left-color: #3a86ff; }
        .tf-card.atividade { border-left-color: #8b5cf6; }
        .tf-label {
            font-size: 11px; font-weight: 700; letter-spacing: 0.09em;
            text-transform: uppercase; color: #19747E; margin-bottom: 6px;
        }
        .tf-label.feito { color: #28a745; }
        .tf-title { font-size: 18px; font-weight: 600; color: #1a1a1a; line-height: 1.45; margin-bottom: 4px; }
        .tf-title.feito { color: #888; text-decoration: line-through; }
        .tf-desc  { font-size: 13px; color: #666; margin-top: 4px; }
        .tf-links { margin-top: 8px; }
        .tf-link  { font-size: 13px; color: #19747E; display: block; margin-bottom: 3px; }
        .tf-fc    { font-size:12px; color:#f59e0b; font-weight:600; margin:4px 0 2px 0; }
        .tf-qv-label {
            font-size: 11px; font-weight: 700; color: #999; text-transform: uppercase;
            letter-spacing: 0.07em; margin-top: 12px; margin-bottom: 6px;
        }
        div[data-testid="stHorizontalBlock"] button[data-testid="stBaseButton-primary"] {
            background: #1a1a1a !important; color: #fff !important;
            border-radius: 6px !important; font-weight: 600 !important; border: none !important;
        }
        div[data-testid="stHorizontalBlock"] button[data-testid="stBaseButton-secondary"] {
            background: #f2f2f2 !important; color: #333 !important;
            border-radius: 6px !important; border: 1px solid #ddd !important; font-weight: 500 !important;
        }

        /* ---------- Plano de estudos: progresso ---------- */
        .prog-bar-wrap { background:#e9ecef; border-radius:8px; height:14px; margin:4px 0 12px 0; }
        .prog-bar-fill { height:14px; border-radius:8px; background:linear-gradient(90deg,#19747E,#2aab78); }
        .prog-assunto-row { font-size:13px; padding:3px 0; border-bottom:1px solid #f0f0f0; }
        .prog-feito  { color:#28a745; }
        .prog-pend   { color:#aaa; }
        .rev-card { background:#fff; border-radius:8px; padding:10px 14px; margin-bottom:6px;
                    border-left:3px solid #3a86ff; box-shadow:0 1px 3px rgba(0,0,0,0.06); font-size:13px; }
        .rev-card.feita { border-left-color:#28a745; }
        .rev-card.atrasada { border-left-color:#dc3545; }
        .rev-date { font-size:11px; color:#888; }

        /* ---------- Simulados (estilo Moodle) ---------- */
        .sim-q-head {
            font-size: 12px; font-weight: 700; color: #19747E; text-transform: uppercase;
            letter-spacing: .06em; margin: 18px 0 6px 0; scroll-margin-top: 80px;
        }
        .sim-info { background:#fff; border-radius:10px; padding:10px 14px;
                    box-shadow:0 1px 4px rgba(0,0,0,.07); margin-bottom:12px; }
        .sim-info table { width:100%; border-collapse:collapse; font-size:14px; }
        .sim-info th { text-align:left; color:#555; font-weight:600; padding:5px 8px; width:38%;
                       border-bottom:1px solid #f0f0f0; }
        .sim-info td { padding:5px 8px; border-bottom:1px solid #f0f0f0; }
        .sim-rev { background:#fff; border-radius:10px; padding:14px 18px; margin:10px 0;
                   border-left:5px solid #adb5bd; box-shadow:0 1px 4px rgba(0,0,0,.07);
                   scroll-margin-top: 80px; }
        .sim-rev.certa  { border-left-color:#28a745; }
        .sim-rev.errada { border-left-color:#dc3545; }
        .sim-rev-head { font-size:12px; font-weight:700; text-transform:uppercase;
                        letter-spacing:.06em; color:#555; margin-bottom:6px; }
        .sim-rev-txt { font-weight:600; margin-bottom:8px; }
        .sim-alt { padding:4px 8px; border-radius:6px; margin:2px 0; font-size:14px; }
        .sim-alt.ok  { background:#e6f4ea; color:#1e7e34; font-weight:600; }
        .sim-alt.bad { background:#fdecea; color:#b02a37; }
        .sim-rev-foot { font-size:13px; color:#555; margin-top:8px; }
        .st-key-simnav button[data-testid="stBaseButton-secondary"] {
            background:#ffffff !important; color:#19747E !important; border:1px solid #A9D6E5 !important;
        }
        .st-key-simnav button { padding: 0.2rem 0.3rem !important; border-radius: 6px !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


apply_custom_style()


# =============================
# Connection Management
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
# Inicialização (uma vez por processo)
# =============================
@st.cache_resource
def _init_db_once():
    db = get_db()

    def _safe(fn):
        try:
            fn()
        except Exception as e:  # cada passo é independente
            print(f"[init_db] {e}")

    passos = [
        lambda: db.questionarios.create_index([("nome", ASCENDING)], name="uq_nome", unique=True),
        lambda: db.questionarios.create_index([("disciplina", ASCENDING), ("nome", ASCENDING)], name="idx_disciplina_nome"),
        lambda: db.questionarios.create_index([("especial", ASCENDING), ("dono", ASCENDING)], name="idx_especial_dono"),
        lambda: db.questoes.create_index([("questionario_id", ASCENDING)]),
        lambda: db.questoes.create_index([("questionario_id", ASCENDING), ("origem_id", ASCENDING)], name="idx_q_origem"),
        lambda: db.respostas.create_index([("questionario_id", ASCENDING)]),
        lambda: db.respostas.create_index([("questao_id", ASCENDING)]),
        lambda: db.respostas.create_index(
            [("usuario_login", ASCENDING), ("questionario_id", ASCENDING),
             ("questao_id", ASCENDING), ("respondido_em", ASCENDING)],
            name="idx_resp_user_q"),
        lambda: db.progresso.create_index(
            [("usuario_login", ASCENDING), ("questionario_id", ASCENDING)], name="uq_progresso", unique=True),
        lambda: db.simulados.create_index([("updated_at", DESCENDING)], name="idx_sim_updated_at"),
        lambda: db.simulados.create_index([("nome", ASCENDING)], name="idx_sim_nome"),
        lambda: db.simulados.create_index([("usuario_login", ASCENDING), ("updated_at", DESCENDING)], name="idx_sim_user"),
        lambda: db.usuarios.create_index([("login", ASCENDING)], name="uq_login", unique=True),
        lambda: db.est_planos.create_index([("usuario_login", ASCENDING), ("nome", ASCENDING)]),
        lambda: db.est_disciplinas.create_index([("plano_id", ASCENDING), ("nome", ASCENDING)]),
        lambda: db.est_assuntos.create_index([("disciplina_id", ASCENDING)]),
        lambda: db.est_planejamento.create_index([("plano_id", ASCENDING), ("data", ASCENDING)]),
        lambda: db.flashcards.create_index([("item_id", ASCENDING)]),
        # Cadernos especiais antigos (globais) passam a pertencer ao admin
        lambda: db.questionarios.update_one(
            {"nome": "Favoritos", "especial": {"$exists": False}},
            {"$set": {"especial": "favoritos", "dono": ADMIN_LOGIN}}),
        lambda: db.questionarios.update_one(
            {"nome": "Caderno de Erros", "especial": {"$exists": False}},
            {"$set": {"especial": "erros", "dono": ADMIN_LOGIN}}),
        lambda: db.questionarios.update_many(
            {"disciplina": {"$exists": False}}, {"$set": {"disciplina": "Sem Disciplina"}}),
        # Histórico antigo (sem usuário) pertence ao admin
        lambda: db.respostas.update_many(
            {"usuario_login": {"$exists": False}}, {"$set": {"usuario_login": ADMIN_LOGIN}}),
        lambda: db.simulados.update_many(
            {"usuario_login": {"$exists": False}}, {"$set": {"usuario_login": ADMIN_LOGIN}}),
        lambda: init_usuarios(db),
    ]
    for p in passos:
        _safe(p)
    return True


def init_db():
    _init_db_once()


# =============================
# Questionários
# =============================
def _doc_to_row_q(q):
    """Converte questionário Mongo -> dict (id:str)."""
    return {
        "id": str(q["_id"]),
        "nome": q.get("nome", ""),
        "descricao": q.get("descricao", ""),
        "disciplina": q.get("disciplina", "Sem Disciplina"),
        "especial": q.get("especial"),
        "dono": q.get("dono"),
    }


def _doc_to_row_questao(d):
    return {
        "id": str(d["_id"]),
        "questionario_id": str(d["questionario_id"]),
        "tipo": d.get("tipo", "VF"),
        "texto": d.get("texto", ""),
        "explicacao": d.get("explicacao", ""),
        "correta_text": d.get("correta_text", ""),
        "op_a": d.get("op_a"),
        "op_b": d.get("op_b"),
        "op_c": d.get("op_c"),
        "op_d": d.get("op_d"),
        "op_e": d.get("op_e"),
        "origem_id": str(d["origem_id"]) if d.get("origem_id") else None,
        "created_at": d.get("created_at"),
    }


@st.cache_data(ttl=10)
def get_questionarios():
    db = get_db()
    try:
        cur = db.questionarios.find({}).sort([("disciplina", ASCENDING), ("nome", ASCENDING)])
        return [_doc_to_row_q(x) for x in cur]
    except Exception as e:
        st.error(f"[get_questionarios] erro: {e}")
        return []


def questionarios_visiveis(login=None, incluir_especiais=True):
    """Questionários comuns + cadernos especiais do próprio usuário."""
    login = login or login_atual()
    out = []
    for q in get_questionarios():
        if q.get("especial"):
            if incluir_especiais and q.get("dono") == login:
                out.append(q)
        else:
            out.append(q)
    return out


def get_caderno_especial(tipo, login=None):
    """Retorna (criando se preciso) o caderno especial do usuário."""
    login = login or login_atual()
    db = get_db()
    doc = db.questionarios.find_one({"especial": tipo, "dono": login})
    if doc:
        return doc
    nome = ESPECIAIS[tipo] if login == ADMIN_LOGIN else f"{ESPECIAIS[tipo]} ({login})"
    try:
        db.questionarios.insert_one({
            "nome": nome,
            "descricao": ESPECIAIS_DESC[tipo],
            "disciplina": SISTEMA,
            "especial": tipo,
            "dono": login,
            "created_at": _now_iso(),
        })
    except Exception:
        # Nome já existe (ex.: documento antigo sem marcação) → adota
        db.questionarios.update_one(
            {"nome": nome}, {"$set": {"especial": tipo, "dono": login, "disciplina": SISTEMA}})
    get_questionarios.clear()
    return db.questionarios.find_one({"especial": tipo, "dono": login})


def garantir_cadernos(login=None):
    for tipo in ESPECIAIS:
        get_caderno_especial(tipo, login)


def get_all_disciplinas():
    """Lista de disciplinas existentes (ordenadas)"""
    db = get_db()
    try:
        vals = [v or "Sem Disciplina" for v in db.questionarios.distinct("disciplina")]
        base = sorted(v for v in set(vals) if v != SISTEMA)
        if SISTEMA in vals:
            base.append(SISTEMA)
        return base or ["Sem Disciplina"]
    except Exception:
        return ["Sem Disciplina"]


def get_questionario_by_name(name):
    q = get_db().questionarios.find_one({"nome": name})
    return _doc_to_row_q(q) if q else None


def add_questionario(nome, descricao="", disciplina="Sem Disciplina"):
    res = get_db().questionarios.insert_one({
        "nome": nome,
        "descricao": descricao,
        "disciplina": disciplina or "Sem Disciplina",
        "created_at": _now_iso(),
    })
    get_questionarios.clear()
    return str(res.inserted_id)


def update_questionario_disciplina(qid, disciplina):
    get_db().questionarios.update_one(
        {"_id": ObjectId(qid)}, {"$set": {"disciplina": disciplina or "Sem Disciplina"}})
    get_questionarios.clear()


def update_questionario_descricao(qid, descricao):
    get_db().questionarios.update_one({"_id": ObjectId(qid)}, {"$set": {"descricao": descricao or ""}})
    get_questionarios.clear()


def delete_questionario(qid):
    db = get_db()
    oid = ObjectId(qid)
    db.questoes.delete_many({"questionario_id": oid})
    db.respostas.delete_many({"questionario_id": oid})
    db.progresso.delete_many({"questionario_id": oid})
    # Remove vínculos no plano de estudos
    db.est_planejamento.update_many(
        {"questionarios_vinculados.questionario_id": qid},
        {"$pull": {"questionarios_vinculados": {"questionario_id": qid}}})
    db.questionarios.delete_one({"_id": oid})
    get_questionarios.clear()
    contar_questoes_por_questionario.clear()


def resetar_resolucoes(qid, login=None):
    """Remove o histórico de respostas do usuário neste questionário e reinicia a ordem."""
    login = login or login_atual()
    db = get_db()
    oid = ObjectId(qid)
    db.respostas.delete_many({"questionario_id": oid, "usuario_login": login})
    db.progresso.delete_one({"usuario_login": login, "questionario_id": oid})
    if login == ADMIN_LOGIN:  # progresso legado gravado no próprio questionário
        db.questionarios.update_one(
            {"_id": oid}, {"$unset": {"progress_pool": "", "progress_idx": "", "progress_updated_at": ""}})

    # Limpa somente o estado de tela das questões deste questionário
    ids = [str(d["_id"]) for d in db.questoes.find({"questionario_id": oid}, {"_id": 1})]
    alvo = {f"pool_{qid}", f"idx_{qid}"}
    for i in ids:
        alvo.update(f"{p}{i}" for p in ("answered_", "result_", "vf_", "answer_letter_"))
        alvo.update(f"strike_{i}_{l}" for l in LETRAS)
    for k in alvo & set(st.session_state.keys()):
        del st.session_state[k]
    toast_depois("Resoluções resetadas para este questionário.")


# =============================
# Questões
# =============================
def montar_questao_vf(questionario_id, texto, correta, explicacao=""):
    return {
        "questionario_id": _oid(questionario_id),
        "tipo": "VF",
        "texto": texto,
        "explicacao": explicacao,
        "correta_text": "V" if bool(correta) else "F",
        "created_at": _now_iso(),
    }


def montar_questao_mc(questionario_id, texto, alternativas, correta_letra, explicacao=""):
    op = alternativas + [None] * (5 - len(alternativas))
    correta_letra = str(correta_letra).upper().strip()
    letras_validas = list(LETRAS)[:len(alternativas)]
    if correta_letra not in letras_validas:
        idx = None
        for i, alt in enumerate(alternativas):
            if alt and str(alt).strip().lower() == correta_letra.lower():
                idx = i
                break
        if idx is None:
            raise ValueError("Resposta correta inválida para questão MC.")
        correta_letra = LETRAS[idx]
    return {
        "questionario_id": _oid(questionario_id),
        "tipo": "MC",
        "texto": texto,
        "explicacao": explicacao,
        "correta_text": correta_letra,
        "op_a": op[0], "op_b": op[1], "op_c": op[2], "op_d": op[3], "op_e": op[4],
        "created_at": _now_iso(),
    }


def add_questao_vf(questionario_id, texto, correta, explicacao=""):
    get_db().questoes.insert_one(montar_questao_vf(questionario_id, texto, correta, explicacao))
    contar_questoes_por_questionario.clear()


def add_questao_mc(questionario_id, texto, alternativas, correta_letra, explicacao=""):
    get_db().questoes.insert_one(montar_questao_mc(questionario_id, texto, alternativas, correta_letra, explicacao))
    contar_questoes_por_questionario.clear()


def get_questoes(questionario_id):
    qid = ObjectId(questionario_id)
    return [_doc_to_row_questao(x) for x in get_db().questoes.find({"questionario_id": qid}).sort("_id", ASCENDING)]


def get_questao_by_id(questao_id):
    oid = _oid(questao_id)
    d = get_db().questoes.find_one({"_id": oid}) if oid else None
    return _doc_to_row_questao(d) if d else None


def get_questoes_by_ids(ids):
    """dict id(str) -> questão, em uma única consulta."""
    oids = [o for o in (_oid(i) for i in ids) if o]
    if not oids:
        return {}
    return {str(d["_id"]): _doc_to_row_questao(d) for d in get_db().questoes.find({"_id": {"$in": oids}})}


@st.cache_data(ttl=10)
def contar_questoes_por_questionario():
    """dict questionario_id(str) -> nº de questões (uma agregação)."""
    pipe = [{"$group": {"_id": "$questionario_id", "n": {"$sum": 1}}}]
    return {str(d["_id"]): int(d["n"]) for d in get_db().questoes.aggregate(pipe)}


def contar_questoes(questionario_ids):
    m = contar_questoes_por_questionario()
    return sum(m.get(str(q), 0) for q in questionario_ids)


DISTRIBUICOES = {
    "igual": "Equilibrada (mesma quantidade de cada questionário)",
    "proporcional": "Proporcional ao tamanho do questionário",
}


def alocar_cotas(counts, n, modo="igual", pesos=None):
    """Divide n questões entre questionários sem ultrapassar o disponível em cada um.
    - igual: todos recebem a mesma cota; quem tem poucas questões entrega todas e a sobra
      vai para os demais (um questionário grande não "engole" o simulado).
    - proporcional: cota proporcional ao tamanho.
    - pesos: {qid: peso} (ex.: pontos fracos) — sobrepõe o modo.
    Determinística (desempate por id), para a prévia bater com o sorteio.
    """
    disp = {str(q): int(c) for q, c in counts.items() if int(c or 0) > 0}
    alloc = {q: 0 for q in disp}
    rest = min(int(n or 0), sum(disp.values()))
    if rest <= 0:
        return alloc
    if pesos is not None:
        w = {q: max(float(pesos.get(q) or 0), 1e-6) for q in disp}
    elif modo == "proporcional":
        w = {q: float(c) for q, c in disp.items()}
    else:
        w = {q: 1.0 for q in disp}

    # "enchimento": quem não comporta a cota ideal entrega tudo e sai da divisão
    abertos = set(disp)
    while abertos:
        soma = sum(w[q] for q in abertos)
        cheios = {q for q in abertos if rest * w[q] / soma >= disp[q]}
        if not cheios:
            break
        for q in cheios:
            alloc[q] = disp[q]
            rest -= disp[q]
        abertos -= cheios

    if abertos and rest > 0:
        soma = sum(w[q] for q in abertos)
        ideal = {q: rest * w[q] / soma for q in abertos}
        for q in abertos:
            alloc[q] = int(ideal[q])
        sobra = rest - sum(alloc[q] for q in abertos)
        for q in sorted(abertos, key=lambda q: (-(ideal[q] - int(ideal[q])), q))[:sobra]:
            alloc[q] += 1
    return alloc


def sortear_por_cotas(alloc, excluir_ids=None):
    """Sorteia k questões de cada questionário ({qid: k}); retorna ids embaralhados."""
    db = get_db()
    excl = [o for o in (_oid(x) for x in (excluir_ids or [])) if o]
    out = []
    for qid_str, k in alloc.items():
        if k <= 0 or not _oid(qid_str):
            continue
        match = {"questionario_id": ObjectId(qid_str)}
        if excl:
            match["_id"] = {"$nin": excl}
        pipe = [{"$match": match}, {"$sample": {"size": int(k)}}, {"$project": {"_id": 1}}]
        out.extend(str(d["_id"]) for d in db.questoes.aggregate(pipe))
    random.shuffle(out)
    return out


def tabela_distribuicao(alloc, nomes=None):
    """Linhas para a prévia: questionário, disponíveis e quantas entram no simulado."""
    m = contar_questoes_por_questionario()
    nomes = nomes or {q["id"]: q["nome"] for q in get_questionarios()}
    total = sum(alloc.values()) or 1
    return [{"Questionário": nomes.get(q, q), "Disponíveis": m.get(q, 0), "No simulado": k,
             "% do simulado": round(k / total * 100, 1)}
            for q, k in sorted(alloc.items(), key=lambda kv: (-kv[1], nomes.get(kv[0], "")))]


def get_questionarios_por_disciplina(disciplinas):
    """Questionários comuns (sem cadernos especiais) das disciplinas informadas."""
    if not disciplinas:
        return []
    disciplinas_set = set(disciplinas)
    return [q for q in questionarios_visiveis(incluir_especiais=False)
            if (q.get("disciplina") or "Sem Disciplina") in disciplinas_set]


def save_resposta(questionario_id, questao_id, correto, login=None):
    get_db().respostas.insert_one({
        "usuario_login": login or login_atual(),
        "questionario_id": ObjectId(questionario_id),
        "questao_id": ObjectId(questao_id),
        "correto": 1 if correto else 0,
        "respondido_em": _now_iso(),
    })


def update_questao_explicacao(questao_id, texto_exp):
    get_db().questoes.update_one({"_id": ObjectId(questao_id)}, {"$set": {"explicacao": texto_exp}})


def update_questao_texto(questao_id, novo_texto):
    get_db().questoes.update_one({"_id": ObjectId(questao_id)}, {"$set": {"texto": novo_texto}})


def update_questao_gabarito(questao_id, correta_text):
    """Atualiza o campo 'correta_text' (VF: 'V'/'F'; MC: 'A'..'E')."""
    get_db().questoes.update_one({"_id": ObjectId(questao_id)}, {"$set": {"correta_text": correta_text}})


# =============================
# Favoritos / Caderno de Erros
# =============================
_CAMPOS_QUESTAO = ("tipo", "texto", "explicacao", "correta_text", "op_a", "op_b", "op_c", "op_d", "op_e")


def _copia_questao_doc(d, destino_oid):
    novo = {k: d.get(k) for k in _CAMPOS_QUESTAO}
    novo["explicacao"] = d.get("explicacao", "")
    novo.update(questionario_id=destino_oid, origem_id=d.get("origem_id") or d["_id"], created_at=_now_iso())
    return novo


def _copiar_questao(d, destino_oid):
    """Copia a questão para um caderno, evitando duplicatas (por origem ou texto)."""
    origem = d.get("origem_id") or d["_id"]
    db = get_db()
    existe = db.questoes.find_one(
        {"questionario_id": destino_oid, "$or": [{"origem_id": origem}, {"texto": d.get("texto")}]},
        {"_id": 1})
    if existe:
        return False
    db.questoes.insert_one(_copia_questao_doc(d, destino_oid))
    contar_questoes_por_questionario.clear()
    return True


def duplicar_questao_para_favoritos(questao_id, login=None):
    d = get_db().questoes.find_one({"_id": ObjectId(questao_id)})
    if not d:
        return False
    fav = get_caderno_especial("favoritos", login)
    return _copiar_questao(d, fav["_id"])


def duplicar_questao_para_erros(questao_id, login=None):
    d = get_db().questoes.find_one({"_id": ObjectId(questao_id)})
    if not d:
        return False
    erros = get_caderno_especial("erros", login)
    if d["questionario_id"] == erros["_id"]:
        return False
    return _copiar_questao(d, erros["_id"])


def checar_saida_caderno_erros(questao_id, login=None):
    """No Caderno de Erros, remove a questão após N acertos seguidos."""
    login = login or login_atual()
    db = get_db()
    oid = ObjectId(questao_id)
    ultimas = list(db.respostas.find(
        {"usuario_login": login, "questao_id": oid}, {"correto": 1}
    ).sort("respondido_em", DESCENDING).limit(ACERTOS_PARA_SAIR_DO_CADERNO))
    if len(ultimas) >= ACERTOS_PARA_SAIR_DO_CADERNO and all(r.get("correto") for r in ultimas):
        db.questoes.delete_one({"_id": oid})
        db.respostas.delete_many({"questao_id": oid})
        contar_questoes_por_questionario.clear()
        return True
    return False


def popular_caderno_erros(login=None):
    """Popula o Caderno de Erros com questões cuja última resposta foi incorreta."""
    login = login or login_atual()
    db = get_db()
    erros = get_caderno_especial("erros", login)
    pipe = [
        {"$match": {"usuario_login": login, "questionario_id": {"$ne": erros["_id"]}}},
        {"$sort": {"respondido_em": 1}},
        {"$group": {"_id": "$questao_id", "last": {"$last": "$correto"}}},
        {"$match": {"last": 0}},
    ]
    ids = [d["_id"] for d in db.respostas.aggregate(pipe)]
    if not ids:
        return 0
    ja = list(db.questoes.find({"questionario_id": erros["_id"]}, {"origem_id": 1, "texto": 1}))
    origens = {d.get("origem_id") for d in ja}
    textos = {d.get("texto") for d in ja}
    novos = []
    for q in db.questoes.find({"_id": {"$in": ids}}):
        origem = q.get("origem_id") or q["_id"]
        if origem in origens or q.get("texto") in textos:
            continue
        novos.append(_copia_questao_doc(q, erros["_id"]))
        origens.add(origem)
        textos.add(q.get("texto"))
    if novos:
        db.questoes.insert_many(novos)
        contar_questoes_por_questionario.clear()
    return len(novos)


# =============================
# Estatísticas (em lote)
# =============================
def bulk_stats(questionario_ids, login=None):
    """qid(str) -> {total, respondidas, corretas, perc} em duas agregações."""
    login = login or login_atual()
    ids = [str(q) for q in questionario_ids if _oid(q)]
    if not ids:
        return {}
    db = get_db()
    oids = [ObjectId(q) for q in ids]

    totals = {q: 0 for q in ids}
    try:
        pipe_tot = [
            {"$match": {"questionario_id": {"$in": oids}}},
            {"$group": {"_id": "$questionario_id", "total": {"$sum": 1}}},
        ]
        for d in db.questoes.aggregate(pipe_tot):
            totals[str(d["_id"])] = int(d.get("total", 0))
    except Exception:
        pass

    resp = {}
    try:
        pipe_resp = [
            {"$match": {"usuario_login": login, "questionario_id": {"$in": oids}}},
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
            resp[str(d["_id"])] = (int(d.get("respondidas", 0)), int(d.get("corretas", 0)))
    except Exception:
        pass

    out = {}
    for q in ids:
        total = totals.get(q, 0)
        respondidas, corretas = resp.get(q, (0, 0))
        perc = (corretas / total) * 100 if total > 0 else 0.0
        out[q] = {"total": total, "respondidas": respondidas, "corretas": corretas, "perc": perc}
    return out


_STATS_VAZIO = {"total": 0, "respondidas": 0, "corretas": 0, "perc": 0.0}


# =============================
# Progresso do Praticar (por usuário)
# =============================
def get_questionario_progress(questionario_id, login=None):
    """Carrega progresso salvo do questionário (pool e índice atual)."""
    login = login or login_atual()
    db = get_db()
    oid = ObjectId(questionario_id)
    doc = db.progresso.find_one({"usuario_login": login, "questionario_id": oid})
    if not doc and login == ADMIN_LOGIN:
        legado = db.questionarios.find_one({"_id": oid}, {"progress_pool": 1, "progress_idx": 1}) or {}
        doc = {"pool": legado.get("progress_pool"), "idx": legado.get("progress_idx")}
    doc = doc or {}
    pool = doc.get("pool") or []
    if not isinstance(pool, list):
        pool = []
    return [str(x) for x in pool], max(0, int(doc.get("idx") or 0))


def set_questionario_progress(questionario_id, pool, idx, login=None):
    """Salva progresso (para retomar de onde parou)."""
    get_db().progresso.update_one(
        {"usuario_login": login or login_atual(), "questionario_id": ObjectId(questionario_id)},
        {"$set": {"pool": list(pool), "idx": int(idx), "updated_at": _now_iso()}},
        upsert=True,
    )


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

Questões com o mesmo enunciado já existentes no questionário são ignoradas (não duplicam).
"""


def normalize_bool(val):
    if isinstance(val, (bool, int)):
        return bool(val)
    s = str(val).strip().lower()
    return s in ("v", "true", "t", "1", "sim", "s", "verdadeiro")


def parse_alternativas(val):
    if val is None:
        return []
    parts = [p.strip() for p in str(val).strip().split("@@") if p.strip()]
    return parts[:5]


def ensure_questionario(nome, disciplina="Sem Disciplina"):
    nome = str(nome).strip() or "Sem Título"
    q = get_questionario_by_name(nome)
    if q:
        # Se já existe, não reclassifica (isso é feito pela UI)
        return q["id"]
    return add_questionario(nome, "", disciplina=disciplina)


def processar_texto(texto):
    """Converte \\n em quebras de linha reais"""
    if texto:
        return str(texto).replace('\\n', '\n')
    return texto


def import_csv_to_db(filelike_or_str):
    """Retorna (importadas, erros, duplicadas_ignoradas, por_questionario).
    por_questionario: {qid: {"nome", "novas", "duplicadas", "criado"}}."""
    if hasattr(filelike_or_str, "read"):
        content = filelike_or_str.read()
        if isinstance(content, bytes):
            try:
                txt = content.decode("utf-8-sig")  # aceita BOM do Excel
            except Exception:
                txt = content.decode("latin-1")
        else:
            txt = str(content)
    else:
        txt = str(filelike_or_str)
    txt = txt.lstrip("﻿")

    sample = txt[:2048]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=";,").delimiter
    except Exception:
        delimiter = ";" if ";" in sample else ","

    reader = csv.DictReader(io.StringIO(txt), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("CSV vazio ou sem cabeçalho.")
    reader.fieldnames = [(f or "").strip().lstrip("﻿").lower() for f in reader.fieldnames]
    required = {"tipo", "questionario", "texto", "correta"}
    missing = [r for r in required if r not in reader.fieldnames]
    if missing:
        raise ValueError(f"CSV sem colunas obrigatórias: {missing}. Cabeçalho encontrado: {reader.fieldnames}")

    db = get_db()
    ids_por_nome = {}
    textos_existentes = {}
    por_q = {}
    docs, erros, duplicadas = [], [], 0
    for i, row in enumerate(reader, start=2):
        try:
            tipo = (row.get("tipo") or "").strip().upper()
            questionario = (row.get("questionario") or "").strip() or "Sem Título"
            disciplina_csv = (row.get("disciplina") or "").strip() or "Sem Disciplina"
            texto = processar_texto((row.get("texto") or "").strip())
            correta = (row.get("correta") or "").strip()
            explicacao = processar_texto(row.get("explicacao") or "")

            if not texto:
                raise ValueError("Texto da questão vazio.")

            if questionario not in ids_por_nome:
                criado = get_questionario_by_name(questionario) is None
                ids_por_nome[questionario] = ensure_questionario(questionario, disciplina_csv)
                por_q[ids_por_nome[questionario]] = {"nome": questionario, "novas": 0,
                                                     "duplicadas": 0, "criado": criado}
            qid = ids_por_nome[questionario]
            if qid not in textos_existentes:
                textos_existentes[qid] = set(db.questoes.distinct("texto", {"questionario_id": ObjectId(qid)}))

            if tipo == "VF":
                doc = montar_questao_vf(qid, texto, normalize_bool(correta), explicacao)
            elif tipo == "MC":
                alternativas = [processar_texto(a) for a in parse_alternativas(row.get("alternativas"))]
                if len(alternativas) < 2:
                    raise ValueError("Questão MC requer ao menos 2 alternativas.")
                doc = montar_questao_mc(qid, texto, alternativas, correta, explicacao)
            else:
                raise ValueError("tipo deve ser 'VF' ou 'MC'.")

            if texto in textos_existentes[qid]:
                duplicadas += 1
                por_q[qid]["duplicadas"] += 1
                continue
            textos_existentes[qid].add(texto)
            docs.append(doc)
            por_q[qid]["novas"] += 1
        except Exception as e:
            erros.append(f"Linha {i}: {e}")

    if docs:
        db.questoes.insert_many(docs, ordered=False)
    get_questionarios.clear()
    contar_questoes_por_questionario.clear()
    return len(docs), erros, duplicadas, por_q


# =============================
# UI Helpers
# =============================
def toast_depois(msg):
    """Toast que sobrevive a um st.rerun() (exibido no início da próxima execução)."""
    st.session_state.setdefault("_toasts", []).append(msg)


def botao_confirmado(label, key, mensagem, **kwargs):
    """Botão em duas etapas: retorna True somente após a confirmação."""
    flag = f"_confirm_{key}"
    if st.session_state.get(flag):
        st.warning(mensagem)
        if st.button("Sim, confirmar", key=f"{key}_sim", type="primary"):
            st.session_state.pop(flag, None)
            return True
        if st.button("Cancelar", key=f"{key}_nao"):
            st.session_state.pop(flag, None)
            st.rerun()
        return False
    if st.button(label, key=key, **kwargs):
        st.session_state[flag] = True
        st.rerun()
    return False


def selecionar_questionario(qs, label="Selecione um questionário"):
    """Selectbox de questionário que respeita o 'current_qid' da sessão."""
    ids = [q["id"] for q in qs]
    nomes = {q["id"]: q["nome"] for q in qs}
    atual = st.session_state.get("current_qid")
    qid = st.selectbox(label, ids, index=ids.index(atual) if atual in ids else 0,
                       format_func=lambda x: nomes.get(x, x))
    st.session_state["current_qid"] = qid
    return qid


def editor_gabarito(q_row, key, label_vf="Gabarito:", label_mc="Gabarito (letra correta):"):
    """Widget de edição de gabarito; retorna o novo 'correta_text'."""
    if q_row["tipo"] == "VF":
        r = st.radio(label_vf, ["Verdadeiro", "Falso"],
                     index=0 if q_row.get("correta_text") == "V" else 1, key=key, horizontal=True)
        return "V" if r == "Verdadeiro" else "F"
    alts = [q_row.get(f"op_{l.lower()}") for l in LETRAS]
    validas = [LETRAS[i] for i, a in enumerate(alts) if a] or list(LETRAS)
    atual = q_row.get("correta_text")
    return st.selectbox(label_mc, validas, index=validas.index(atual) if atual in validas else 0, key=key)


def caixa_explicacao(texto):
    texto = (texto or "").strip()
    if texto:
        st.markdown(
            f"<div class='exp-box'><strong>Explicação:</strong><br><br>{esc_rich(texto)}</div>",
            unsafe_allow_html=True,
        )


def _bloco_metricas(s, sufixo="", show_respondidas=True, label_aprov="Aproveitamento"):
    if show_respondidas:
        c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
        c2.metric(f"Respondidas{sufixo}", s["respondidas"])
    else:
        c1, c3, c4 = st.columns([1, 1, 2])
    c1.metric(f"Total{sufixo}", s["total"])
    c3.metric(f"Corretas{sufixo}", s["corretas"])
    c4.progress(min(100, int(s["perc"])), text=f"{label_aprov}: {s['perc']:.1f}%")


def show_desempenho_block(qid, show_respondidas=False):
    s = bulk_stats([qid]).get(qid, _STATS_VAZIO)
    _bloco_metricas(s, show_respondidas=show_respondidas)


def _registrar_resposta_praticar(parent_qid, qid, is_correct, is_caderno_erros):
    save_resposta(parent_qid, qid, is_correct)
    if not is_correct and not is_caderno_erros:
        duplicar_questao_para_erros(qid)
    elif is_correct and is_caderno_erros and checar_saida_caderno_erros(qid):
        toast_depois(f"🎉 Questão removida do Caderno de Erros após {ACERTOS_PARA_SAIR_DO_CADERNO} acertos seguidos.")


def render_questao(q_row, parent_qid, questao_numero=None, is_caderno_erros=False):
    """
    Renderiza uma questão individual na página Praticar.

    - VF: radio padrão (travado depois de respondido).
    - MC: cada alternativa em UMA linha com
        [checkbox para riscar] [botão tipo radio] [texto da alternativa].
      O checkbox é só rascunho visual; a primeira letra escolhida é gravada e travada.
    """
    qid = q_row["id"]
    tipo = q_row["tipo"]
    answered_key = f"answered_{qid}"
    result_key = f"result_{qid}"
    answer_letter_key = f"answer_letter_{qid}"

    if questao_numero:
        st.markdown(f"#### Questão {questao_numero}")
    st.markdown(f"**{q_row['texto']}**")

    # ======================
    # EDIÇÃO RÁPIDA (enunciado e gabarito)
    # ======================
    with st.expander("✏️ Editar enunciado / gabarito (nesta questão)", expanded=False):
        novo_texto = st.text_area("Enunciado:", value=q_row.get("texto", ""), key=f"edit_texto_{qid}", height=120)
        nova_correta_text = editor_gabarito(q_row, key=f"edit_gab_{qid}")
        if st.button("Salvar enunciado + gabarito", key=f"save_edit_{qid}"):
            if (novo_texto or "").strip() != (q_row.get("texto") or "").strip():
                update_questao_texto(qid, novo_texto)
            if (nova_correta_text or "").strip() != (q_row.get("correta_text") or "").strip():
                update_questao_gabarito(qid, nova_correta_text)
            toast_depois("Questão atualizada.")
            st.rerun()

    # ======================
    # VERDADEIRO/FALSO
    # ======================
    if tipo == "VF":
        vf_options = ["— Selecione —", "Verdadeiro", "Falso"]
        escolha = st.radio("Sua resposta", vf_options, key=f"vf_{qid}", index=0,
                           disabled=answered_key in st.session_state)
        if answered_key not in st.session_state and escolha != "— Selecione —":
            is_correct = (q_row["correta_text"] == "V") == (escolha == "Verdadeiro")
            st.session_state[answered_key] = True
            st.session_state[result_key] = is_correct
            _registrar_resposta_praticar(parent_qid, qid, is_correct, is_caderno_erros)
            st.rerun()  # trava o radio imediatamente

    # ======================
    # MÚLTIPLA ESCOLHA
    # ======================
    else:
        alternativas = [q_row.get(f"op_{l.lower()}") for l in LETRAS]
        opts = [(LETRAS[i], alt) for i, alt in enumerate(alternativas) if alt]

        st.caption("Clique para riscar mentalmente alternativas e escolher a resposta:")
        current_letter = st.session_state.get(answer_letter_key)
        respondida = answered_key in st.session_state

        for letra, alt in opts:
            strike_key = f"strike_{qid}_{letra}"
            st.session_state.setdefault(strike_key, False)
            col_cb, col_radio, col_txt = st.columns([0.06, 0.06, 0.88])
            with col_cb:
                st.checkbox(f"Riscar {letra}", key=strike_key, label_visibility="collapsed")
            with col_radio:
                simbolo = "🔘" if current_letter == letra else "⚪"
                if st.button(simbolo, key=f"ansbtn_{qid}_{letra}", disabled=respondida) and not respondida:
                    st.session_state[answer_letter_key] = letra
                    current_letter = letra
            with col_txt:
                if st.session_state.get(strike_key, False):
                    st.markdown(
                        f"<span style='text-decoration: line-through; color: #6b7280;'>{letra}) {esc(alt)}</span>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(f"{letra}) {alt}")

        sel_txt = st.session_state.get(answer_letter_key)
        st.caption(f"Resposta selecionada: **{sel_txt if sel_txt else 'nenhuma'}**")

        if answered_key not in st.session_state and st.session_state.get(answer_letter_key) is not None:
            is_correct = st.session_state[answer_letter_key] == q_row["correta_text"]
            st.session_state[answered_key] = True
            st.session_state[result_key] = is_correct
            _registrar_resposta_praticar(parent_qid, qid, is_correct, is_caderno_erros)
            st.rerun()  # desabilita os botões imediatamente

    # ======================
    # FEEDBACK (com explicação)
    # ======================
    if st.session_state.get(answered_key):
        if st.session_state.get(result_key):
            st.success("✅ Você acertou esta questão.")
        else:
            st.error(f"❌ Você errou esta questão. Gabarito: {q_row['correta_text']}")
        caixa_explicacao(q_row.get("explicacao"))

    # ======================
    # EXPLICAÇÃO (edição)
    # ======================
    with st.expander("Ver explicação / editar", expanded=False):
        new_exp = st.text_area("Texto da explicação:", value=q_row.get("explicacao", ""),
                               key=f"exp_{qid}", height=180)
        if st.button("Salvar explicação", key=f"save_exp_{qid}"):
            update_questao_explicacao(qid, new_exp)
            st.toast("Explicação atualizada.")

    if st.button("⭐ Salvar nos Favoritos", key=f"fav_{qid}"):
        st.toast("Adicionada em 'Favoritos'." if duplicar_questao_para_favoritos(qid) else "Já está nos Favoritos.")

    st.divider()


# =============================
# Páginas
# =============================
def page_dashboard():
    st.title("📚 Painel de Questionários (rápido)")
    login = login_atual()

    if st.button("📔 Atualizar Caderno de Erros com histórico"):
        with st.spinner("Analisando respostas anteriores..."):
            n = popular_caderno_erros(login)
        if n > 0:
            st.success(f"✅ {n} questões erradas adicionadas ao Caderno de Erros!")
        else:
            st.info("Nenhuma questão nova para adicionar.")

    st.divider()

    caderno_erros = _doc_to_row_q(get_caderno_especial("erros", login))
    stats_erros = bulk_stats([caderno_erros["id"]], login).get(caderno_erros["id"], _STATS_VAZIO)
    with st.container(border=True):
        st.subheader("🧨 Caderno de Erros (fixado)")
        _bloco_metricas(stats_erros)
        st.caption(f"A questão sai do caderno após {ACERTOS_PARA_SAIR_DO_CADERNO} acertos seguidos nele.")
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

    qs = questionarios_visiveis(login, incluir_especiais=False)
    if not qs:
        st.info("Nenhum questionário cadastrado ainda. Vá em **Importar CSV** para começar.")
        return

    filtro_global = st.text_input("🔎 Buscar por nome do questionário", key="dash_busca")
    disciplinas = sorted({(q.get("disciplina") or "Sem Disciplina") for q in qs})
    escolha_disc = st.selectbox("📦 Disciplina", ["Todas (resumo)"] + disciplinas, key="dash_disciplina")

    def _filtra(lista):
        if not filtro_global:
            return lista
        return [q for q in lista if filtro_global.lower() in q["nome"].lower()]

    # -------------------------
    # Modo 1: Todas (resumo leve)
    # -------------------------
    if escolha_disc == "Todas (resumo)":
        qs_filtrados = _filtra(qs)
        if not qs_filtrados:
            st.caption("Nenhum questionário corresponde ao filtro.")
            return

        stats = bulk_stats([q["id"] for q in qs_filtrados], login)
        agg = {}
        for q in qs_filtrados:
            disc = q.get("disciplina") or "Sem Disciplina"
            s = stats.get(q["id"], _STATS_VAZIO)
            a = agg.setdefault(disc, {"questionarios": 0, "total": 0, "respondidas": 0, "corretas": 0})
            a["questionarios"] += 1
            a["total"] += s["total"]
            a["respondidas"] += s["respondidas"]
            a["corretas"] += s["corretas"]

        rows = []
        for disc, a in sorted(agg.items()):
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
        st.dataframe(rows, width="stretch", hide_index=True)
        st.caption("Dica: selecione uma disciplina acima para ver e acessar os questionários sem carregar o painel inteiro.")
        return

    # -------------------------
    # Modo 2: Uma disciplina
    # -------------------------
    qs_disc = _filtra([q for q in qs if (q.get("disciplina") or "Sem Disciplina") == escolha_disc])
    if not qs_disc:
        st.caption("Nenhum questionário nesta disciplina corresponde ao filtro.")
        return

    qids_disc = [q["id"] for q in qs_disc]
    stats_disc = bulk_stats(qids_disc, login)
    tot = {k: sum(stats_disc[q][k] for q in qids_disc) for k in ("total", "respondidas", "corretas")}
    tot["perc"] = (tot["corretas"] / tot["total"]) * 100 if tot["total"] else 0.0

    with st.container(border=True):
        st.subheader(f"📦 {escolha_disc}")
        _bloco_metricas(tot, sufixo=" (disciplina)", label_aprov="Aproveitamento da disciplina")
        st.markdown("---")

        nomes = {q["id"]: q["nome"] for q in qs_disc}
        sel_id = st.selectbox("Selecione um questionário", list(nomes), format_func=lambda x: nomes[x], key="dash_sel_q")
        escolhido = next((x for x in qs_disc if x["id"] == sel_id), None)

        if escolhido:
            _bloco_metricas(stats_disc.get(escolhido["id"], _STATS_VAZIO))
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
                if botao_confirmado("Resetar resoluções", key=f"reset_{escolhido['id']}",
                                    mensagem="Apagar seu histórico de respostas deste questionário?"):
                    resetar_resolucoes(escolhido["id"])
                    st.rerun()
            with b4:
                if botao_confirmado("Excluir", key=f"del_{escolhido['id']}",
                                    mensagem=f"Excluir '{escolhido['nome']}' com todas as questões e respostas? Não dá para desfazer."):
                    delete_questionario(escolhido["id"])
                    toast_depois(f"Questionário '{escolhido['nome']}' excluído.")
                    st.rerun()


def _praticar_ir(qid, pool, novo_idx):
    st.session_state[f"idx_{qid}"] = novo_idx
    set_questionario_progress(qid, pool, novo_idx)


def _praticar_carregar_pool(qid):
    """Monta o pool de navegação: retoma o salvo e acrescenta questões novas."""
    current_ids = [r["id"] for r in get_questoes(qid)]
    saved_pool, saved_idx = get_questionario_progress(qid)
    cur_set = set(current_ids)
    saved_pool = [x for x in saved_pool if x in cur_set]
    if saved_pool:
        ja = set(saved_pool)
        novas = [x for x in current_ids if x not in ja]
        random.shuffle(novas)
        pool = saved_pool + novas
        idx = min(saved_idx, len(pool) - 1)
    else:
        pool = current_ids
        random.shuffle(pool)
        idx = 0
    st.session_state[f"pool_{qid}"] = pool
    st.session_state[f"idx_{qid}"] = idx
    set_questionario_progress(qid, pool, idx)


def page_praticar():
    st.title("🎯 Praticar")
    qs = questionarios_visiveis()
    if not qs:
        st.info("Nenhum questionário cadastrado.")
        return

    qid = selecionar_questionario(qs)
    qinfo = next((q for q in qs if q["id"] == qid), {})
    is_erros = qinfo.get("especial") == "erros"

    c1, c2 = st.columns(2)
    with c1:
        if botao_confirmado("🔄 Resetar resoluções deste questionário", key=f"pr_reset_{qid}",
                            mensagem="Apagar seu histórico de respostas deste questionário e reiniciar a ordem?"):
            resetar_resolucoes(qid)
            st.rerun()
    with c2:
        st.caption("O reset remove apenas o seu histórico de respostas. As questões permanecem.")

    key_pool, key_idx = f"pool_{qid}", f"idx_{qid}"
    if key_pool not in st.session_state:
        _praticar_carregar_pool(qid)

    pool = st.session_state[key_pool]
    if not pool:
        st.info("Acabaram as questões! Você pode **resetar resoluções** para reiniciar.")
        return

    idx = max(0, min(int(st.session_state.get(key_idx, 0)), len(pool) - 1))
    st.session_state[key_idx] = idx

    row = get_questao_by_id(pool[idx])
    if row is None:  # removida (ex.: saiu do Caderno de Erros)
        pool.pop(idx)
        _praticar_ir(qid, pool, max(0, min(idx, len(pool) - 1)))
        st.rerun()

    total_questoes = len(pool)
    nav_col1, nav_col2, nav_col3 = st.columns([1, 1, 2])
    with nav_col1:
        st.button("◀ Questão anterior", key="prev_top", disabled=(idx == 0),
                  on_click=_praticar_ir, args=(qid, pool, idx - 1))
    with nav_col2:
        st.button("Próxima questão ▶", key="next_top", disabled=(idx >= total_questoes - 1),
                  on_click=_praticar_ir, args=(qid, pool, idx + 1))
    with nav_col3:
        st.markdown(f"**Questão {idx + 1} de {total_questoes}**")

    render_questao(row, parent_qid=qid, questao_numero=idx + 1, is_caderno_erros=is_erros)

    st.subheader("Desempenho")
    show_desempenho_block(qid, show_respondidas=True)

    st.button("Próxima questão ▶", key="next_bottom", disabled=(idx >= total_questoes - 1),
              on_click=_praticar_ir, args=(qid, pool, idx + 1))


def page_gerenciar():
    st.title("🧰 Gerenciar Questionário")
    qs = questionarios_visiveis()
    if not qs:
        st.info("Nenhum questionário cadastrado.")
        return
    qid = selecionar_questionario(qs)
    qinfo = next((q for q in qs if q["id"] == qid), None)

    if qinfo:
        st.markdown("### Metadados")
        col1, col2, col3 = st.columns([2, 2, 1])
        with col1:
            existentes = [d for d in get_all_disciplinas() if d != SISTEMA]
            opcoes = ["(Sem Disciplina)"] + existentes + ["+ Nova disciplina..."]
            escolha_disc = st.selectbox(
                "Disciplina", opcoes,
                index=(opcoes.index(qinfo["disciplina"]) if qinfo["disciplina"] in opcoes else 0))
        with col2:
            nova_disc = ""
            if escolha_disc == "+ Nova disciplina...":
                nova_disc = st.text_input("Nome da nova disciplina", value="")
        with col3:
            if st.button("Salvar disciplina", width="stretch"):
                if escolha_disc == "+ Nova disciplina...":
                    final_disc = nova_disc.strip()
                else:
                    final_disc = None if escolha_disc == "(Sem Disciplina)" else escolha_disc
                update_questionario_disciplina(qid, final_disc or "Sem Disciplina")
                st.success("Disciplina atualizada.")
                st.rerun()

        desc = st.text_area("Descrição (opcional)", value=qinfo.get("descricao", ""), height=80)
        if st.button("Salvar descrição"):
            update_questionario_descricao(qid, desc)
            st.toast("Descrição atualizada.")

        st.divider()
        c1, _ = st.columns([1, 3])
        with c1:
            if botao_confirmado("🔄 Resetar resoluções deste questionário", key=f"ger_reset_{qid}",
                                mensagem="Apagar seu histórico de respostas deste questionário?"):
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
                st.write("**Alternativas:**")
                for l in LETRAS:
                    a = r.get(f"op_{l.lower()}")
                    if a:
                        st.write(f"{'✅' if l == r['correta_text'] else '▫️'} {l}) {a}")
            else:
                st.write(f"**Gabarito:** {'Verdadeiro' if r['correta_text'] == 'V' else 'Falso'}")

            st.markdown("**Editar gabarito:**")
            novo_gab = editor_gabarito(r, key=f"m_gab_{r['id']}", label_vf="Gabarito", label_mc="Letra correta")
            if st.button("Salvar gabarito", key=f"m_save_gab_{r['id']}"):
                update_questao_gabarito(r["id"], novo_gab)
                toast_depois("Gabarito atualizado.")
                st.rerun()

            st.write("**Explicação (edite abaixo):**")
            new_exp = st.text_area("Explicação", value=r.get("explicacao", ""), key=f"m_exp_{r['id']}",
                                   height=120, label_visibility="collapsed")
            if st.button("Salvar explicação", key=f"m_save_{r['id']}"):
                update_questao_explicacao(r["id"], new_exp)
                st.toast("Explicação atualizada.")
            if st.button("⭐ Favoritar", key=f"m_fav_{r['id']}"):
                st.toast("Adicionada em 'Favoritos'." if duplicar_questao_para_favoritos(r["id"]) else "Já está nos Favoritos.")


def page_importar():
    st.title("📥 Importar questões via CSV")
    st.markdown("Faça upload de um CSV **com cabeçalho**. Veja o modelo abaixo.")

    with st.expander("📄 Ver modelo de CSV suportado"):
        st.code(TEMPLATE_DOC, language="text")

    up = st.file_uploader("Enviar arquivo CSV", type=["csv"])
    txt = st.text_area("... ou cole aqui o conteúdo do CSV", height=180,
                       placeholder="tipo,questionario,disciplina,texto,correta,explicacao,alternativas\n...")

    if st.button("Importar", type="primary"):
        if up is None and not txt.strip():
            st.warning("Envie um arquivo ou cole o conteúdo do CSV.")
            return
        with st.spinner("Importando questões..."):
            try:
                ok, erros, dup, por_q = import_csv_to_db(up if up is not None else txt)
                # guardado na sessão para o resultado (e os links) sobreviverem aos cliques
                st.session_state["_import_result"] = {"ok": ok, "erros": erros, "dup": dup, "por_q": por_q}
            except Exception as e:
                st.session_state.pop("_import_result", None)
                st.error(f"❌ Falha na importação: {e}")

    res = st.session_state.get("_import_result")
    if res:
        _importar_resultado(res)


def link_questionario(qid):
    """URL que abre o questionário direto no Praticar (?q=<id>)."""
    base = ""
    try:
        base = (st.context.url or "").split("?")[0]
    except Exception:
        pass
    return f"{base}?q={qid}"


def _abrir_questionario(qid, pagina="Praticar"):
    st.session_state["current_qid"] = qid
    st.session_state["go_to"] = pagina


def _importar_resultado(res):
    ok, erros, dup, por_q = res["ok"], res["erros"], res["dup"], res["por_q"]
    if ok > 0:
        st.success(f"✅ {ok} questões importadas com sucesso!")
    else:
        st.warning("Nenhuma questão nova foi importada.")
    if dup:
        st.info(f"{dup} questão(ões) já existiam no questionário e foram ignoradas.")
    if erros:
        with st.expander(f"⚠️ {len(erros)} erro(s) encontrado(s)"):
            for e in erros[:100]:
                st.write("- ", e)

    existentes = {q["id"] for q in get_questionarios()}
    itens = [(qid, info) for qid, info in por_q.items() if qid in existentes]
    if not itens:
        return
    st.markdown("### 🔗 Questionários desta importação")
    st.caption("Abra direto no Praticar ou no Gerenciar, ou copie o link para acessar depois "
               "(quem abrir o link precisa estar logado).")
    for qid, info in itens:
        with st.container(border=True):
            selo = "🆕 novo" if info.get("criado") else "atualizado"
            st.markdown(f"**{esc(info['nome'])}** · {selo} · {info['novas']} nova(s)"
                        + (f" · {info['duplicadas']} repetida(s)" if info["duplicadas"] else ""))
            c1, c2, c3 = st.columns([1, 1, 3])
            c1.button("🎯 Praticar", key=f"imp_pr_{qid}", on_click=_abrir_questionario, args=(qid, "Praticar"))
            c2.button("🧰 Gerenciar", key=f"imp_ge_{qid}", on_click=_abrir_questionario, args=(qid, "Gerenciar"))
            c3.code(link_questionario(qid), language=None)
    if st.button("Limpar resultado", key="imp_limpar"):
        st.session_state.pop("_import_result", None)
        st.rerun()


# =============================
# Simulados — persistência
# =============================
def create_simulado(nome, pool_ids, meta=None, login=None):
    """Cria um simulado persistido no MongoDB e retorna o id (str)."""
    agora = _now_iso()
    doc = {
        "usuario_login": login or login_atual(),
        "nome": (nome or "").strip() or f"Simulado {datetime.now(FUSO).strftime('%d/%m/%Y %H:%M')}",
        "pool_ids": [str(x) for x in (pool_ids or [])],
        "total": len(pool_ids or []),
        "acertos": 0,
        "status": "in_progress",  # in_progress | finished
        "meta": meta or {},
        "rascunho": {},           # questao_id -> resposta ('V'/'F' ou 'A'..'E'; None = em branco)
        "marcadas": [],           # questões marcadas para revisão
        "respostas": [],          # legado (versões antigas)
        "started_at": agora,
        "created_at": agora,
        "updated_at": agora,
    }
    res = get_db().simulados.insert_one(doc)
    list_simulados.clear()
    return str(res.inserted_id)


@st.cache_data(ttl=10)
def list_simulados(login):
    """Lista os simulados do usuário (mais recentes primeiro)."""
    cur = get_db().simulados.find(
        {"usuario_login": login},
        {"nome": 1, "status": 1, "total": 1, "acertos": 1, "created_at": 1, "updated_at": 1,
         "started_at": 1, "finished_at": 1, "duracao_seg": 1, "rascunho": 1, "respostas.questao_id": 1},
    ).sort([("updated_at", DESCENDING)])
    out = []
    for d in cur:
        rasc = d.get("rascunho") or {}
        respondidas = sum(1 for v in rasc.values() if v) or len({str(r.get("questao_id")) for r in d.get("respostas") or []})
        out.append({
            "id": str(d["_id"]),
            "nome": d.get("nome", ""),
            "status": d.get("status", "in_progress"),
            "total": int(d.get("total") or 0),
            "acertos": int(d.get("acertos") or 0),
            "respondidas": respondidas,
            "started_at": d.get("started_at") or d.get("created_at"),
            "finished_at": d.get("finished_at"),
            "duracao_seg": d.get("duracao_seg"),
            "updated_at": d.get("updated_at") or "",
        })
    return out


def get_simulado(sim_id, login=None):
    oid = _oid(sim_id)
    if not oid:
        return None
    d = get_db().simulados.find_one({"_id": oid, "usuario_login": login or login_atual()})
    if not d:
        return None
    d["id"] = str(d["_id"])
    return d


def update_simulado_nome(sim_id, novo_nome):
    get_db().simulados.update_one(
        {"_id": ObjectId(sim_id), "usuario_login": login_atual()},
        {"$set": {"nome": (novo_nome or "").strip(), "updated_at": _now_iso()}})
    list_simulados.clear()


def delete_simulado(sim_id):
    get_db().simulados.delete_one({"_id": ObjectId(sim_id), "usuario_login": login_atual()})
    list_simulados.clear()


def salvar_rascunho(sim_id, questao_id, resposta):
    """Autosave de uma resposta (None = em branco)."""
    get_db().simulados.update_one(
        {"_id": ObjectId(sim_id), "status": "in_progress"},
        {"$set": {f"rascunho.{questao_id}": resposta, "updated_at": _now_iso()}})
    list_simulados.clear()


def marcar_questao_simulado(sim_id, questao_id, marcada):
    op = "$addToSet" if marcada else "$pull"
    get_db().simulados.update_one(
        {"_id": ObjectId(sim_id)}, {op: {"marcadas": questao_id}, "$set": {"updated_at": _now_iso()}})


def _normaliza_resposta(r):
    s = str(r or "").strip()
    if not s:
        return None
    if s.lower() in ("verdadeiro", "v", "true"):
        return "V"
    if s.lower() in ("falso", "f", "false"):
        return "F"
    s = s[0].upper()
    return s if s in LETRAS else None


def rascunho_do_simulado(sim):
    """Respostas atuais: legado (respostas antigas) sobreposto pelo rascunho."""
    out = {}
    for r in sorted(sim.get("respostas") or [], key=lambda x: x.get("respondido_em", "")):
        out[str(r.get("questao_id"))] = _normaliza_resposta(r.get("resposta"))
    out.update(sim.get("rascunho") or {})
    return out


def _linha_resultado(qid, q, qinfo, resposta, acertou=None):
    if q is None:
        return {"questao_id": qid, "questionario_id": "", "questionario_nome": "(questão removida)",
                "disciplina": "Sem Disciplina", "tipo": "", "resposta": resposta, "correta": "",
                "acertou": bool(acertou), "em_branco": resposta is None}
    info = qinfo.get(q["questionario_id"], {})
    correta = q["correta_text"]
    if acertou is None:
        acertou = resposta is not None and resposta == correta
    return {
        "questao_id": qid,
        "questionario_id": q["questionario_id"],
        "questionario_nome": info.get("nome", ""),
        "disciplina": info.get("disciplina") or "Sem Disciplina",
        "tipo": q["tipo"],
        "resposta": resposta,
        "correta": correta,
        "acertou": bool(acertou),
        "em_branco": resposta is None,
    }


def finalizar_simulado(sim):
    """Corrige a tentativa e grava um retrato do resultado (como no Moodle)."""
    pool = [str(x) for x in sim.get("pool_ids") or []]
    qmap = get_questoes_by_ids(pool)
    qinfo = {q["id"]: q for q in get_questionarios()}
    rasc = rascunho_do_simulado(sim)

    resultado = [_linha_resultado(qid, qmap.get(qid), qinfo, rasc.get(qid)) for qid in pool]
    acertos = sum(1 for r in resultado if r["acertou"])
    agora = datetime.now(timezone.utc)
    inicio = _parse_iso(sim.get("started_at") or sim.get("created_at")) or agora
    get_db().simulados.update_one(
        {"_id": sim["_id"]},
        {"$set": {
            "status": "finished",
            "acertos": acertos,
            "resultado": resultado,
            "finished_at": agora.isoformat(),
            "duracao_seg": int((agora - inicio).total_seconds()),
            "updated_at": agora.isoformat(),
        }})

    if (sim.get("meta") or {}).get("enviar_erros_caderno", True):
        erros = get_caderno_especial("erros", sim.get("usuario_login"))
        for r in resultado:
            q = qmap.get(r["questao_id"])
            if q and not r["acertou"] and q["questionario_id"] != str(erros["_id"]):
                d = get_db().questoes.find_one({"_id": ObjectId(q["id"])})
                if d:
                    _copiar_questao(d, erros["_id"])
    list_simulados.clear()


def garantir_resultado(sim):
    """Simulados antigos finalizados: monta o retrato a partir das respostas legadas."""
    if sim.get("resultado") or sim.get("status") != "finished":
        return sim
    pool = [str(x) for x in sim.get("pool_ids") or []]
    qmap = get_questoes_by_ids(pool)
    qinfo = {q["id"]: q for q in get_questionarios()}
    ultimas = {}
    for r in sorted(sim.get("respostas") or [], key=lambda x: x.get("respondido_em", "")):
        ultimas[str(r.get("questao_id"))] = r
    resultado = []
    for qid in pool:
        r = ultimas.get(qid)
        resp = _normaliza_resposta(r.get("resposta")) if r else None
        resultado.append(_linha_resultado(qid, qmap.get(qid), qinfo, resp, acertou=bool(r and r.get("correto"))))
    get_db().simulados.update_one({"_id": sim["_id"]}, {"$set": {"resultado": resultado}})
    sim["resultado"] = resultado
    return sim


def resumo_resultado(res):
    total = len(res)
    acertos = sum(1 for r in res if r.get("acertou"))
    branco = sum(1 for r in res if r.get("em_branco") and not r.get("acertou"))
    perc = (acertos / total) * 100 if total else 0.0
    return {"total": total, "acertos": acertos, "erros": total - acertos - branco,
            "em_branco": branco, "perc": perc, "nota": perc / 10}


def stats_resultado(res, chave, rotulo):
    """Tabela de desempenho agrupada por 'chave' (disciplina, questionario_nome, tipo)."""
    agg = {}
    for r in res:
        k = r.get(chave) or "—"
        a = agg.setdefault(k, {"Total": 0, "Acertos": 0, "Erros": 0, "Em branco": 0})
        a["Total"] += 1
        if r.get("acertou"):
            a["Acertos"] += 1
        elif r.get("em_branco"):
            a["Em branco"] += 1
        else:
            a["Erros"] += 1
    rows = []
    for k, a in sorted(agg.items()):
        perc = (a["Acertos"] / a["Total"]) * 100 if a["Total"] else 0.0
        rows.append({rotulo: k, **a, "Aproveitamento (%)": round(perc, 1)})
    return rows


@st.cache_data(ttl=300, show_spinner=False)
def estatisticas_simulados(login, versao):
    """Consolida todos os simulados finalizados do usuário. 'versao' invalida o cache."""
    docs = list(get_db().simulados.find({"usuario_login": login, "status": "finished"}))
    hist, todos, por_questao = [], [], {}
    for d in docs:
        d["id"] = str(d["_id"])
        res = garantir_resultado(d).get("resultado") or []
        if not res:
            continue
        r = resumo_resultado(res)
        hist.append({"id": d["id"], "nome": d.get("nome", ""),
                     "data": d.get("finished_at") or d.get("updated_at") or "",
                     "perc": round(r["perc"], 1), "total": r["total"], "acertos": r["acertos"]})
        todos.extend(res)
        for x in res:
            e = por_questao.setdefault(x["questao_id"], {"feitas": 0, "erros": 0})
            e["feitas"] += 1
            if not x.get("acertou"):
                e["erros"] += 1
    hist.sort(key=lambda h: h["data"])

    top = sorted(((k, v) for k, v in por_questao.items() if v["erros"] >= 2),
                 key=lambda kv: (-kv[1]["erros"], -kv[1]["feitas"]))[:10]
    textos = get_questoes_by_ids([k for k, _ in top])
    mais_erradas = [{
        "Questão": ((textos.get(k) or {}).get("texto") or "(removida)")[:100],
        "Vezes no simulado": v["feitas"],
        "Erros": v["erros"],
    } for k, v in top]

    return {
        "historico": hist,
        "por_disciplina": stats_resultado(todos, "disciplina", "Disciplina"),
        "por_questionario": stats_resultado(todos, "questionario_nome", "Questionário"),
        "mais_erradas": mais_erradas,
    }


def _versao_simulados(sims):
    return f"{len(sims)}|{max((s['updated_at'] for s in sims), default='')}"


def refazer_simulado(sim, somente_erradas=False):
    res = sim.get("resultado") or []
    if somente_erradas:
        ids = [r["questao_id"] for r in res if not r.get("acertou")]
    else:
        ids = [str(x) for x in sim.get("pool_ids") or []]
    existentes = get_questoes_by_ids(ids)
    ids = [i for i in ids if i in existentes]
    if not ids:
        return None
    random.shuffle(ids)
    meta = dict(sim.get("meta") or {})
    meta["tentativa_de"] = sim["id"]
    sufixo = "erradas" if somente_erradas else "nova tentativa"
    return create_simulado(f"{sim.get('nome', 'Simulado')} — {sufixo}", ids, meta)


def _sim_tempo(sim):
    """(prazo_final, esgotado) para simulados com tempo limite."""
    limite = int((sim.get("meta") or {}).get("tempo_limite_min") or 0)
    if not limite:
        return None, False
    inicio = _parse_iso(sim.get("started_at") or sim.get("created_at"))
    if not inicio:
        return None, False
    prazo = inicio + timedelta(minutes=limite)
    return prazo, datetime.now(timezone.utc) > prazo + timedelta(seconds=5)


# =============================
# Simulados — páginas
# =============================
def _abrir_simulado(sim_id):
    st.session_state["current_simulado_id"] = sim_id
    st.session_state["mode"] = "run_simulado"
    st.session_state["go_to"] = "Simulados"


def page_simulado():
    st.title("📝 Simulados")
    msg = st.session_state.pop("_sim_msg", None)
    if msg:
        st.info(msg)

    login = login_atual()
    sims = list_simulados(login)
    tab_lista, tab_novo, tab_stats = st.tabs(["📚 Meus simulados", "➕ Novo simulado", "📈 Estatísticas"])
    with tab_lista:
        _sim_aba_lista(sims)
    with tab_novo:
        _sim_aba_novo()
    with tab_stats:
        _sim_aba_estatisticas(login, sims)


def _sim_aba_lista(sims):
    if not sims:
        st.caption("Nenhum simulado salvo ainda. Crie um na aba **Novo simulado**.")
        return

    rows = []
    for s in sims:
        fin = s["status"] == "finished"
        perc = (s["acertos"] / s["total"]) * 100 if (fin and s["total"]) else None
        rows.append({
            "Nome": s["nome"],
            "Situação": "✅ Finalizado" if fin else "⏳ Em andamento",
            "Questões": s["total"],
            "Respondidas": s["total"] if fin else s["respondidas"],
            "Acertos": s["acertos"] if fin else None,
            "Aproveitamento (%)": round(perc, 1) if perc is not None else None,
            "Nota (0-10)": round(perc / 10, 1) if perc is not None else None,
            "Iniciado em": _fmt_dt(s["started_at"]),
            "Duração": _fmt_duracao(s["duracao_seg"]) if fin else "—",
        })
    st.dataframe(rows, width="stretch", hide_index=True)

    ids = [s["id"] for s in sims]
    por_id = {s["id"]: s for s in sims}

    def _label(i):
        s = por_id[i]
        st_txt = "✅ finalizado" if s["status"] == "finished" else "⏳ em andamento"
        return f"{s['nome']} • {st_txt} • {_fmt_dt(s['started_at'])}"

    sel_id = st.selectbox("Selecione um simulado", ids, format_func=_label, key="sel_simulado_salvo")
    sel = por_id[sel_id]
    c1, c2, c3 = st.columns([1, 2, 1])
    with c1:
        rotulo = "🔍 Ver revisão" if sel["status"] == "finished" else "▶ Continuar"
        if st.button(rotulo, key="btn_open_sim", type="primary", width="stretch"):
            _abrir_simulado(sel_id)
            st.rerun()
    with c2:
        novo_nome = st.text_input("Renomear", value=sel["nome"], key=f"rename_sim_{sel_id}",
                                  label_visibility="collapsed")
        if st.button("Salvar nome", key="btn_rename_sim"):
            update_simulado_nome(sel_id, novo_nome)
            toast_depois("Nome atualizado.")
            st.rerun()
    with c3:
        if botao_confirmado("🗑️ Excluir", key=f"del_sim_{sel_id}",
                            mensagem=f"Excluir o simulado '{sel['nome']}'?"):
            delete_simulado(sel_id)
            if st.session_state.get("current_simulado_id") == sel_id:
                st.session_state.pop("current_simulado_id", None)
                st.session_state["mode"] = None
            st.rerun()


def _sim_aba_novo():
    qs_all = [q for q in questionarios_visiveis() if q.get("especial") != "favoritos"]
    if not qs_all:
        st.info("Crie ou importe questionários primeiro.")
        return

    st.caption("Monte o simulado por **disciplinas**, selecionando **questionários** diretamente "
               "ou a partir dos seus **pontos fracos**. As questões aparecem numa página só, "
               "com navegação lateral, e a correção sai no envio — como no Moodle.")

    modo = st.radio("Modo de seleção", ["Por disciplina", "Por questionário", "🎯 Pontos fracos"],
                    horizontal=True, key="sim_modo")
    sim_nome = st.text_input("Nome do simulado (opcional)", value="", placeholder="Ex.: Simulado Constitucional - 20/01")

    with st.expander("⚙️ Opções do simulado", expanded=False):
        o1, o2, o3 = st.columns(3)
        tempo = o1.number_input("Tempo limite (min, 0 = sem limite)", min_value=0, max_value=600,
                                value=0, step=5, key="sim_opt_tempo")
        por_pagina = o2.selectbox("Questões por página", POR_PAGINA_OPCOES, index=0,
                                  format_func=_fmt_pp, key="sim_opt_pp")
        enviar_erros = o3.checkbox("Enviar erros ao Caderno de Erros", value=True, key="sim_opt_erros")
    opcoes = {"tempo_limite_min": int(tempo), "por_pagina": int(por_pagina), "enviar_erros_caderno": bool(enviar_erros)}

    if modo == "🎯 Pontos fracos":
        _sim_novo_pontos_fracos(sim_nome, opcoes)
        return

    distrib = st.radio(
        "Distribuição entre questionários", list(DISTRIBUICOES), format_func=DISTRIBUICOES.get,
        horizontal=True, key="sim_distrib",
        help="Equilibrada: cada questionário contribui com a mesma quantidade; se um tiver poucas "
             "questões, entra com todas e a diferença vai para os demais. Proporcional: questionários "
             "maiores contribuem com mais questões.")

    if modo == "Por disciplina":
        disciplinas = [d for d in get_all_disciplinas() if d != SISTEMA]
        if not disciplinas:
            st.info("Nenhuma disciplina encontrada. Classifique questionários em **Gerenciar** ou importe via CSV com a coluna 'disciplina'.")
            return

        sel_disc = st.multiselect("Selecione 1 ou mais disciplinas", disciplinas)
        if not sel_disc:
            st.info("Selecione ao menos uma disciplina para montar o simulado.")
            return

        qs_por_disc = get_questionarios_por_disciplina(sel_disc)
        if not qs_por_disc:
            st.warning("Não encontrei questionários nas disciplinas selecionadas (ou eles estão vazios).")
            return

        grupos = {}
        for q in qs_por_disc:
            grupos.setdefault(q.get("disciplina") or "Sem Disciplina", []).append(q["id"])

        st.markdown("### Quantidade de questões por disciplina")
        n_por_disc = {}
        for disc in sel_disc:
            qids_disc = grupos.get(disc, [])
            total_disp_disc = contar_questoes(qids_disc)
            col1, col2 = st.columns([2, 1])
            with col1:
                st.write(f"**{disc}**")
                st.caption(f"Questionários: {len(qids_disc)} • Questões disponíveis: {total_disp_disc}")
            with col2:
                n_val = st.number_input(
                    f"Qtd ({disc})", min_value=0,
                    value=min(10, total_disp_disc), max_value=total_disp_disc,
                    step=1, key=f"n_disc_{disc}", disabled=(total_disp_disc == 0))
            n_por_disc[disc] = int(n_val)
        contagem = contar_questoes_por_questionario()
        alloc = {}
        for disc in sel_disc:
            qids_disc = grupos.get(disc, [])
            alloc.update(alocar_cotas({q: contagem.get(q, 0) for q in qids_disc},
                                      n_por_disc.get(disc, 0), distrib))
        total_planejado = sum(alloc.values())

        st.divider()
        st.metric("Total de questões no simulado", total_planejado)
        _sim_previa_distribuicao(alloc)

        if st.button("Iniciar Simulado", type="primary", disabled=(total_planejado <= 0)):
            pool_ids = sortear_por_cotas(alloc)
            if not pool_ids:
                st.warning("Não foi possível montar o simulado com as escolhas atuais.")
                return
            sim_id = create_simulado(
                sim_nome, pool_ids,
                meta={"modo": "Por disciplina", "disciplinas": list(sel_disc), "n_por_disc": n_por_disc,
                      "distribuicao": distrib, **opcoes})
            _abrir_simulado(sim_id)
            st.rerun()

    else:
        options = {q["nome"]: q["id"] for q in qs_all}
        escolha = st.multiselect("Selecione um ou mais questionários", list(options.keys()))
        qids = [options[k] for k in escolha]
        total_disp = contar_questoes(qids) if qids else 0

        n = st.number_input(
            "Número de questões no simulado", min_value=1,
            value=min(10, max(1, total_disp)), max_value=max(1, total_disp),
            step=1, disabled=(total_disp == 0))

        contagem = contar_questoes_por_questionario()
        alloc = alocar_cotas({q: contagem.get(q, 0) for q in qids}, n, distrib) if qids else {}
        if len(qids) > 1:
            _sim_previa_distribuicao(alloc)

        if st.button("Iniciar Simulado", type="primary", disabled=(not qids or total_disp == 0)):
            pool_ids = sortear_por_cotas(alloc)
            sim_id = create_simulado(
                sim_nome, pool_ids,
                meta={"modo": "Por questionário", "questionarios": list(qids), "n_total": int(n),
                      "distribuicao": distrib, **opcoes})
            _abrir_simulado(sim_id)
            st.rerun()


def _sim_previa_distribuicao(alloc):
    if not alloc:
        return
    with st.expander("📊 Prévia da distribuição por questionário", expanded=False):
        st.dataframe(tabela_distribuicao(alloc), width="stretch", hide_index=True)


# ---- Simulado a partir dos pontos fracos ----
def mapa_desempenho_questoes(login):
    """Por questão (id original): tentativas, erros e se a última tentativa foi erro.
    Junta o Praticar (respostas) e os simulados finalizados. Cópias dos cadernos
    especiais são atribuídas à questão de origem."""
    db = get_db()
    eventos = []  # (quando, questao_id, acertou)
    for d in db.respostas.find({"usuario_login": login},
                               {"questao_id": 1, "correto": 1, "respondido_em": 1}):
        eventos.append((d.get("respondido_em") or "", str(d.get("questao_id")), bool(d.get("correto"))))
    for s in db.simulados.find({"usuario_login": login, "status": "finished"},
                               {"resultado": 1, "finished_at": 1}):
        quando = s.get("finished_at") or ""
        for r in s.get("resultado") or []:  # em branco conta como erro, como na nota
            eventos.append((quando, str(r.get("questao_id")), bool(r.get("acertou"))))

    # cópias (Favoritos/Caderno de Erros) -> questão original
    ids = list({e[1] for e in eventos})
    origem = {}
    for d in db.questoes.find({"_id": {"$in": [o for o in (_oid(i) for i in ids) if o]},
                               "origem_id": {"$exists": True}}, {"origem_id": 1}):
        origem[str(d["_id"])] = str(d["origem_id"])

    mapa = {}
    for quando, qid, acertou in sorted(eventos):
        e = mapa.setdefault(origem.get(qid, qid), {"tentativas": 0, "erros": 0, "ultima_errada": False})
        e["tentativas"] += 1
        e["erros"] += 0 if acertou else 1
        e["ultima_errada"] = not acertou
    return mapa


def diagnostico_pontos_fracos(login, min_tentativas=3):
    """Questões a revisar e questionários fracos (aproveitamento < 70%), do pior para o melhor."""
    mapa = mapa_desempenho_questoes(login)
    if not mapa:
        return {"erradas": [], "fracos": []}
    qmap = get_questoes_by_ids(list(mapa))
    comuns = {q["id"]: q for q in questionarios_visiveis(login, incluir_especiais=False)}

    erradas, por_q = [], {}
    for qid, e in mapa.items():
        q = qmap.get(qid)
        if not q or q["questionario_id"] not in comuns:
            continue
        a = por_q.setdefault(q["questionario_id"], {"tentativas": 0, "acertos": 0, "questoes": 0})
        a["tentativas"] += e["tentativas"]
        a["acertos"] += e["tentativas"] - e["erros"]
        a["questoes"] += 1
        if e["erros"]:
            # prioridade: última tentativa errada > nº de erros > taxa de erro
            erradas.append((qid, (e["ultima_errada"], e["erros"], e["erros"] / e["tentativas"])))
    erradas.sort(key=lambda x: x[1], reverse=True)

    contagem = contar_questoes_por_questionario()
    fracos = []
    for qid, a in por_q.items():
        if a["tentativas"] < min_tentativas:
            continue
        perc = a["acertos"] / a["tentativas"] * 100
        if perc < 70:
            info = comuns[qid]
            fracos.append({"id": qid, "Questionário": info["nome"],
                           "Disciplina": info.get("disciplina") or "Sem Disciplina",
                           "Tentativas": a["tentativas"], "Aproveitamento (%)": round(perc, 1),
                           "Questões vistas": a["questoes"], "Questões no banco": contagem.get(qid, 0)})
    fracos.sort(key=lambda r: (r["Aproveitamento (%)"], -r["Tentativas"]))
    return {"erradas": [qid for qid, _ in erradas], "fracos": fracos}


def montar_simulado_pontos_fracos(login, n, perc_erradas=60, max_fracos=5):
    """ids do simulado: parte com questões que você já errou, o resto sorteado nos
    questionários fracos com peso maior para os piores (sem repetir questões)."""
    diag = diagnostico_pontos_fracos(login)
    n = int(n)
    n_err = min(len(diag["erradas"]), round(n * perc_erradas / 100))
    escolhidas = diag["erradas"][:n_err]

    fracos = diag["fracos"][:max_fracos]
    falta = n - len(escolhidas)
    if falta > 0 and fracos:
        contagem = contar_questoes_por_questionario()
        ja = set(escolhidas)
        # disponível = banco menos as já escolhidas daquele questionário
        qmap = get_questoes_by_ids(escolhidas)
        usadas = {}
        for qid in escolhidas:
            q = qmap.get(qid)
            if q:
                usadas[q["questionario_id"]] = usadas.get(q["questionario_id"], 0) + 1
        disp = {f["id"]: max(0, contagem.get(f["id"], 0) - usadas.get(f["id"], 0)) for f in fracos}
        pesos = {f["id"]: 100 - f["Aproveitamento (%)"] for f in fracos}
        alloc = alocar_cotas(disp, falta, pesos=pesos)
        escolhidas += [x for x in sortear_por_cotas(alloc, excluir_ids=ja) if x not in ja]

    # sem questionários fracos suficientes: completa com outras questões já erradas
    if len(escolhidas) < n:
        ja = set(escolhidas)
        escolhidas += [x for x in diag["erradas"] if x not in ja][:n - len(escolhidas)]
    random.shuffle(escolhidas)
    return escolhidas, diag


def _sim_novo_pontos_fracos(sim_nome, opcoes, prefixo="pf"):
    login = login_atual()
    diag = diagnostico_pontos_fracos(login)
    if not diag["erradas"] and not diag["fracos"]:
        st.info("Ainda não há erros registrados. Resolva questões no **Praticar** ou finalize "
                "simulados para que o sistema identifique seus pontos fracos.")
        return

    c1, c2 = st.columns(2)
    c1.metric("Questões que você já errou", len(diag["erradas"]))
    c2.metric("Questionários com aproveitamento < 70%", len(diag["fracos"]))
    if diag["fracos"]:
        st.markdown("**Questionários fracos** (pelo menos 3 tentativas)")
        st.dataframe([{k: v for k, v in f.items() if k != "id"} for f in diag["fracos"]],
                     width="stretch", hide_index=True)

    a, b, c = st.columns(3)
    n = a.number_input("Número de questões", min_value=1, max_value=200, value=20, step=1,
                       key=f"{prefixo}_n")
    perc = b.slider("% de questões que você já errou", 0, 100, 60, step=10, key=f"{prefixo}_perc",
                    help="O restante é sorteado nos questionários fracos, com mais questões dos piores.")
    max_fracos = c.number_input("Questionários fracos considerados", min_value=1, max_value=20,
                                value=5, step=1, key=f"{prefixo}_maxf")
    st.caption("Ordem de prioridade das já erradas: errou na última tentativa, mais erros, maior taxa de erro. "
               "Respostas do Praticar e dos simulados entram na conta.")

    if st.button("🎯 Gerar simulado dos pontos fracos", type="primary", key=f"{prefixo}_go"):
        ids, _ = montar_simulado_pontos_fracos(login, n, perc, int(max_fracos))
        if not ids:
            st.warning("Não encontrei questões suficientes para montar o simulado.")
            return
        if len(ids) < n:
            toast_depois(f"Só havia {len(ids)} questões elegíveis; o simulado foi criado com elas.")
        nome = sim_nome or f"Pontos fracos {datetime.now(FUSO).strftime('%d/%m/%Y %H:%M')}"
        sim_id = create_simulado(
            nome, ids, meta={"modo": "Pontos fracos", "perc_erradas": int(perc),
                             "questionarios": [f["id"] for f in diag["fracos"][:int(max_fracos)]], **opcoes})
        _abrir_simulado(sim_id)
        st.rerun()


def _sim_aba_estatisticas(login, sims):
    if not any(s["status"] == "finished" for s in sims):
        st.info("Finalize um simulado para ver as estatísticas consolidadas.")
        return
    est = estatisticas_simulados(login, _versao_simulados(sims))
    hist = est["historico"]
    if not hist:
        st.info("Ainda não há dados suficientes.")
        return

    percs = [h["perc"] for h in hist]
    media = sum(percs) / len(percs)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Simulados finalizados", len(hist))
    m2.metric("Aproveitamento médio", f"{media:.1f}%")
    m3.metric("Melhor resultado", f"{max(percs):.1f}%")
    anteriores = percs[:-1]
    delta = f"{percs[-1] - (sum(anteriores) / len(anteriores)):+.1f} p.p. vs. média anterior" if anteriores else None
    m4.metric("Último simulado", f"{percs[-1]:.1f}%", delta=delta)

    if len(hist) >= 2:
        st.markdown("#### Evolução do aproveitamento")
        df = pd.DataFrame({"Aproveitamento (%)": percs}, index=pd.Index(range(1, len(hist) + 1), name="Simulado nº"))
        st.line_chart(df)

    st.markdown("#### Desempenho por disciplina (todos os simulados)")
    por_disc = est["por_disciplina"]
    st.dataframe(por_disc, width="stretch", hide_index=True)
    if len(por_disc) >= 2:
        st.bar_chart(pd.DataFrame(por_disc).set_index("Disciplina")["Aproveitamento (%)"])

    fracos = sorted((r for r in est["por_questionario"] if r["Total"] >= 3),
                    key=lambda r: r["Aproveitamento (%)"])[:5]
    if fracos:
        st.markdown("#### 🎯 Pontos fracos (questionários com menor aproveitamento)")
        st.dataframe(fracos, width="stretch", hide_index=True)

    if est["mais_erradas"] or fracos:
        if st.button("🎯 Gerar simulado de 20 questões com meus pontos fracos", key="pf_rapido"):
            ids, _ = montar_simulado_pontos_fracos(login, 20)
            if ids:
                sim_id = create_simulado(
                    f"Pontos fracos {datetime.now(FUSO).strftime('%d/%m/%Y %H:%M')}", ids,
                    meta={"modo": "Pontos fracos", "perc_erradas": 60, "por_pagina": 0,
                          "tempo_limite_min": 0, "enviar_erros_caderno": True})
                _abrir_simulado(sim_id)
                st.rerun()
            st.warning("Não encontrei questões suficientes para montar o simulado.")
        st.caption("Para escolher quantidade e proporção, use **Novo simulado → 🎯 Pontos fracos**.")

    if est["mais_erradas"]:
        st.markdown("#### 🔁 Questões que você mais erra")
        st.dataframe(est["mais_erradas"], width="stretch", hide_index=True)


def page_run_simulado():
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

    if not sim.get("pool_ids"):
        st.warning("Este simulado não tem questões.")
        st.session_state["mode"] = None
        return

    _sim_cabecalho(sim)

    if sim.get("status") == "finished":
        _sim_revisao(sim)
        return

    prazo, esgotado = _sim_tempo(sim)
    if esgotado:
        finalizar_simulado(sim)
        st.session_state.pop(f"sim_resumo_{sim_id}", None)
        toast_depois("⏰ Tempo esgotado — tentativa enviada automaticamente.")
        st.rerun()
    if prazo:
        _sim_timer(prazo)

    if st.session_state.get(f"sim_resumo_{sim_id}"):
        _sim_resumo(sim)
    else:
        _sim_tentativa(sim)


def _sim_cabecalho(sim):
    c1, c2 = st.columns([1, 5])
    with c1:
        if st.button("← Simulados", key="sim_voltar", help="Voltar à lista (o progresso fica salvo)"):
            st.session_state["mode"] = None
            st.session_state["go_to"] = "Simulados"
            st.rerun()
    with c2:
        icone = "📊" if sim.get("status") == "finished" else "🧪"
        st.title(f"{icone} {sim.get('nome', 'Simulado')}")


def _sim_timer(prazo):
    fim = prazo.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    components.html(f"""
<div id='t' style="font:600 17px -apple-system,'Segoe UI',sans-serif;color:#19747E"></div>
<script>
const end = new Date("{fim}").getTime();
function tick() {{
  const s = Math.max(0, Math.floor((end - Date.now()) / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  const el = document.getElementById('t');
  if (s > 0) {{
    el.textContent = '⏱️ Tempo restante: ' + (h ? h + 'h ' : '') + String(m).padStart(2, '0') + ':' + String(x).padStart(2, '0');
  }} else {{
    el.textContent = '⏰ Tempo esgotado — a tentativa será enviada na próxima interação.';
  }}
  el.style.color = s < 300 ? '#c0392b' : '#19747E';
}}
tick(); setInterval(tick, 1000);
</script>""", height=34)


def _sim_rolar_para(ancora):
    components.html(f"""<script>
setTimeout(function() {{
  const e = window.parent.document.getElementById("{ancora}");
  if (e) e.scrollIntoView({{behavior: "smooth", block: "start"}});
}}, 350);
</script>""", height=0)


# --- callbacks (rodam antes do rerun; gravam no banco na hora) ---
def _sim_cb_resposta(sim_id, qid, rkey):
    salvar_rascunho(sim_id, qid, st.session_state.get(rkey))


def _sim_cb_limpar(sim_id, qid, rkey):
    st.session_state[rkey] = None
    salvar_rascunho(sim_id, qid, None)


def _sim_cb_marcar(sim_id, qid, fkey):
    marcar_questao_simulado(sim_id, qid, bool(st.session_state.get(fkey)))


def _sim_cb_ir(sim_id, pagina, numero, filtro_key=None):
    st.session_state[f"sim_pag_{sim_id}"] = pagina
    st.session_state[f"sim_scroll_{sim_id}"] = numero
    st.session_state.pop(f"sim_resumo_{sim_id}", None)
    if filtro_key:
        st.session_state[filtro_key] = "Todas"


def _sim_grade(sim_id, pool, rotulos, tipos, pp=None, filtro_key=None, legenda=""):
    """Bloco de navegação do simulado (barra lateral), 5 botões por linha."""
    with st.container(key="simnav"):
        st.markdown("**🧭 Navegação do simulado**")
        for ini in range(0, len(pool), 5):
            cols = st.columns(5)
            for j in range(min(5, len(pool) - ini)):
                i = ini + j
                cols[j].button(
                    rotulos[i], key=f"simnav_{sim_id}_{i}", type=tipos[i], width="stretch",
                    on_click=_sim_cb_ir, args=(sim_id, (i // pp) if pp else 0, i + 1, filtro_key))
        if legenda:
            st.caption(legenda)


def _sim_tentativa(sim):
    sim_id = sim["id"]
    pool = [str(x) for x in sim.get("pool_ids") or []]
    n = len(pool)
    qmap = get_questoes_by_ids(pool)
    rasc = rascunho_do_simulado(sim)
    marcadas = set(sim.get("marcadas") or [])
    meta = sim.get("meta") or {}

    respondidas = sum(1 for q in pool if rasc.get(q))
    top1, top2 = st.columns([3, 1])
    with top1:
        st.progress(int(respondidas / n * 100) if n else 0, text=f"{respondidas} de {n} questões respondidas")
    with top2:
        pp_key = f"sim_pp_{sim_id}"
        if pp_key not in st.session_state:
            padrao = int(meta.get("por_pagina") or 0)
            st.session_state[pp_key] = padrao if padrao in POR_PAGINA_OPCOES else 0
        st.selectbox("Questões por página", POR_PAGINA_OPCOES, key=pp_key, format_func=_fmt_pp,
                     on_change=lambda: st.session_state.update({f"sim_pag_{sim_id}": 0}))
    pp = st.session_state[pp_key] or n
    n_pag = max(1, -(-n // pp))
    pag_key = f"sim_pag_{sim_id}"
    pag = min(max(int(st.session_state.get(pag_key, 0)), 0), n_pag - 1)
    st.session_state[pag_key] = pag

    with st.sidebar:
        st.divider()
        _sim_grade(
            sim_id, pool,
            rotulos=[f"{i + 1}{'⚑' if q in marcadas else ''}" for i, q in enumerate(pool)],
            tipos=["primary" if rasc.get(q) else "secondary" for q in pool],
            pp=pp, legenda="Preenchido = respondida · branco = em branco · ⚑ = marcada")
        if st.button("Finalizar tentativa…", key="sim_fin_side", width="stretch"):
            st.session_state[f"sim_resumo_{sim_id}"] = True
            st.rerun()

    for i in range(pag * pp, min(n, (pag + 1) * pp)):
        qid = pool[i]
        _sim_questao_tentativa(sim_id, i, qid, qmap.get(qid), rasc.get(qid), qid in marcadas)

    if n_pag > 1:
        p1, p2, p3 = st.columns([1, 2, 1])
        p1.button("◀ Página anterior", key="sim_pag_ant", disabled=pag == 0, width="stretch",
                  on_click=_sim_cb_ir, args=(sim_id, pag - 1, (pag - 1) * pp + 1))
        p2.markdown(f"<div style='text-align:center;padding-top:6px'>Página {pag + 1} de {n_pag}</div>",
                    unsafe_allow_html=True)
        p3.button("Próxima página ▶", key="sim_pag_prox", disabled=pag >= n_pag - 1, width="stretch",
                  on_click=_sim_cb_ir, args=(sim_id, pag + 1, (pag + 1) * pp + 1))

    st.divider()
    if st.button("Finalizar tentativa…", key="sim_fin", type="primary"):
        st.session_state[f"sim_resumo_{sim_id}"] = True
        st.rerun()

    alvo = st.session_state.pop(f"sim_scroll_{sim_id}", None)
    if alvo:
        _sim_rolar_para(f"simq-{alvo}")


def _sim_questao_tentativa(sim_id, i, qid, q, resp, marcada):
    status = "✔ Respondida" if resp else "Ainda não respondida"
    flag = " · ⚑ Marcada para revisão" if marcada else ""
    st.markdown(f"<div class='sim-q-head' id='simq-{i + 1}'>Questão {i + 1} · {status}{flag}</div>",
                unsafe_allow_html=True)
    with st.container(border=True):
        if not q:
            st.warning("Esta questão foi removida do banco e será contada como em branco.")
            return
        st.markdown(f"**{q['texto']}**")

        if q["tipo"] == "VF":
            opts = ["V", "F"]
            nomes = {"V": "Verdadeiro", "F": "Falso"}
        else:
            nomes = {l: f"{l}) {q.get('op_' + l.lower())}" for l in LETRAS if q.get("op_" + l.lower())}
            opts = list(nomes)

        rkey = f"simr_{sim_id}_{qid}"
        if rkey not in st.session_state or st.session_state[rkey] not in opts + [None]:
            st.session_state[rkey] = resp if resp in opts else None
        st.radio("Resposta", opts, index=None, key=rkey, format_func=lambda x: nomes.get(x, x),
                 on_change=_sim_cb_resposta, args=(sim_id, qid, rkey), label_visibility="collapsed")

        c1, c2, _ = st.columns([1.3, 1.7, 3])
        c1.button("Limpar minha escolha", key=f"simclr_{sim_id}_{qid}", disabled=not st.session_state.get(rkey),
                  on_click=_sim_cb_limpar, args=(sim_id, qid, rkey))
        fkey = f"simf_{sim_id}_{qid}"
        st.session_state.setdefault(fkey, marcada)
        c2.checkbox("⚑ Marcar para revisão", key=fkey, on_change=_sim_cb_marcar, args=(sim_id, qid, fkey))


def _sim_resumo(sim):
    """Resumo da tentativa antes do envio (como no Moodle)."""
    sim_id = sim["id"]
    pool = [str(x) for x in sim.get("pool_ids") or []]
    rasc = rascunho_do_simulado(sim)
    marcadas = set(sim.get("marcadas") or [])

    st.subheader("Resumo da tentativa")
    rows = [{"Questão": i + 1,
             "Situação": "✔ Respondida" if rasc.get(q) else "— Não respondida",
             "Marcada": "⚑" if q in marcadas else ""} for i, q in enumerate(pool)]
    st.dataframe(rows, width="stretch", hide_index=True, height=min(38 * len(rows) + 40, 420))

    branco = sum(1 for q in pool if not rasc.get(q))
    if branco:
        st.warning(f"{branco} questão(ões) em branco serão contadas como erradas.")
    if marcadas:
        st.info(f"{len(marcadas)} questão(ões) marcadas para revisão.")

    with st.sidebar:
        st.divider()
        _sim_grade(
            sim_id, pool,
            rotulos=[f"{i + 1}{'⚑' if q in marcadas else ''}" for i, q in enumerate(pool)],
            tipos=["primary" if rasc.get(q) else "secondary" for q in pool],
            pp=st.session_state.get(f"sim_pp_{sim_id}") or len(pool),
            legenda="Clique para voltar à questão.")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("◀ Voltar à tentativa", key="sim_voltar_tent"):
            st.session_state.pop(f"sim_resumo_{sim_id}", None)
            st.rerun()
    with c2:
        if botao_confirmado("✅ Enviar tudo e terminar", key=f"sim_enviar_{sim_id}", type="primary",
                            mensagem="Depois de enviar, não será mais possível alterar as respostas. Confirmar?"):
            finalizar_simulado(sim)
            st.session_state.pop(f"sim_resumo_{sim_id}", None)
            st.rerun()


def _sim_alts_html(q, resp, correta):
    if q["tipo"] == "VF":
        itens = [("V", "Verdadeiro"), ("F", "Falso")]
    else:
        itens = [(l, q.get(f"op_{l.lower()}")) for l in LETRAS if q.get(f"op_{l.lower()}")]
    out = []
    for l, txt in itens:
        cls = "ok" if l == correta else ("bad" if l == resp else "")
        marca = "✅" if l == correta else ("❌" if l == resp else "○")
        prefixo = f"{l}) " if q["tipo"] == "MC" else ""
        sua = " <small>(sua resposta)</small>" if l == resp else ""
        out.append(f"<div class='sim-alt {cls}'>{marca} {prefixo}{esc(txt)}{sua}</div>")
    return "".join(out)


def _sim_revisao(sim):
    """Página de revisão do simulado finalizado: notas, estatísticas e gabarito comentado."""
    sim = garantir_resultado(sim)
    sim_id = sim["id"]
    res = sim.get("resultado") or []
    r = resumo_resultado(res)
    marcadas = set(sim.get("marcadas") or [])
    pool = [x["questao_id"] for x in res]
    qmap = get_questoes_by_ids(pool)

    st.markdown(
        "<div class='sim-info'><table>"
        f"<tr><th>Iniciado em</th><td>{esc(_fmt_dt(sim.get('started_at') or sim.get('created_at')))}</td></tr>"
        f"<tr><th>Concluído em</th><td>{esc(_fmt_dt(sim.get('finished_at')) or '—')}</td></tr>"
        f"<tr><th>Tempo empregado</th><td>{esc(_fmt_duracao(sim.get('duracao_seg')))}</td></tr>"
        f"<tr><th>Acertos</th><td>{r['acertos']} de {r['total']}</td></tr>"
        f"<tr><th>Nota</th><td><b>{r['nota']:.1f}</b> de 10,0 ({r['perc']:.1f}%)</td></tr>"
        "</table></div>",
        unsafe_allow_html=True,
    )

    # Comparação com a média dos outros simulados do usuário
    sims = list_simulados(login_atual())
    hist = estatisticas_simulados(login_atual(), _versao_simulados(sims))["historico"]
    outros = [h["perc"] for h in hist if h["id"] != sim_id]
    delta = f"{r['perc'] - sum(outros) / len(outros):+.1f} p.p. vs. sua média" if outros else None

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Questões", r["total"])
    m2.metric("Acertos", r["acertos"])
    m3.metric("Erros", r["erros"])
    m4.metric("Em branco", r["em_branco"])
    m5.metric("Aproveitamento", f"{r['perc']:.1f}%", delta=delta)

    filtro_key = f"simrv_f_{sim_id}"
    with st.sidebar:
        st.divider()
        _sim_grade(
            sim_id, pool,
            rotulos=[f"{'✅' if x['acertou'] else ('⚪' if x['em_branco'] else '❌')}{i + 1}" for i, x in enumerate(res)],
            tipos=["secondary"] * len(res), filtro_key=filtro_key,
            legenda="✅ certa · ❌ errada · ⚪ em branco")

    tab_des, tab_rev = st.tabs(["📊 Desempenho", "📝 Revisão das questões"])

    with tab_des:
        por_disc = stats_resultado(res, "disciplina", "Disciplina")
        st.markdown("#### Por disciplina")
        st.dataframe(por_disc, width="stretch", hide_index=True)
        if len(por_disc) >= 2:
            st.bar_chart(pd.DataFrame(por_disc).set_index("Disciplina")["Aproveitamento (%)"])
        st.markdown("#### Por questionário")
        st.dataframe(stats_resultado(res, "questionario_nome", "Questionário"), width="stretch", hide_index=True)
        st.markdown("#### Por tipo de questão")
        por_tipo = stats_resultado(res, "tipo", "Tipo")
        for linha in por_tipo:
            linha["Tipo"] = {"VF": "Verdadeiro/Falso", "MC": "Múltipla escolha"}.get(linha["Tipo"], linha["Tipo"])
        st.dataframe(por_tipo, width="stretch", hide_index=True)

    with tab_rev:
        st.session_state.setdefault(filtro_key, "Todas")
        filtro = st.radio("Mostrar", ["Todas", "Erradas", "Em branco", "Certas", "Marcadas"],
                          key=filtro_key, horizontal=True)
        mostrou = 0
        for i, x in enumerate(res):
            if filtro == "Erradas" and (x["acertou"] or x["em_branco"]):
                continue
            if filtro == "Em branco" and not (x["em_branco"] and not x["acertou"]):
                continue
            if filtro == "Certas" and not x["acertou"]:
                continue
            if filtro == "Marcadas" and x["questao_id"] not in marcadas:
                continue
            mostrou += 1
            q = qmap.get(x["questao_id"])
            if x["acertou"]:
                cls, status = "certa", "✅ Correta"
            elif x["em_branco"]:
                cls, status = "", "⚪ Não respondida"
            else:
                cls, status = "errada", "❌ Incorreta"
            flag = " · ⚑ marcada" if x["questao_id"] in marcadas else ""
            origem = f" · {esc(x.get('questionario_nome'))}" if x.get("questionario_nome") else ""
            if q:
                corpo = (f"<div class='sim-rev-txt'>{esc_rich(q['texto'])}</div>"
                         + _sim_alts_html(q, x.get("resposta"), x.get("correta"))
                         + f"<div class='sim-rev-foot'>Sua resposta: <b>{esc(x.get('resposta') or '—')}</b>"
                           f" · Resposta correta: <b>{esc(x.get('correta'))}</b></div>")
            else:
                corpo = "<div class='sim-rev-txt'>(questão removida do banco)</div>"
            st.markdown(
                f"<div class='sim-rev {cls}' id='simq-{i + 1}'>"
                f"<div class='sim-rev-head'>Questão {i + 1} · {status}{flag}{origem}</div>{corpo}</div>",
                unsafe_allow_html=True)
            if q:
                caixa_explicacao(q.get("explicacao"))
                if st.button("⭐ Favoritar", key=f"simrv_fav_{sim_id}_{i}"):
                    st.toast("Adicionada em 'Favoritos'." if duplicar_questao_para_favoritos(q["id"]) else "Já está nos Favoritos.")
        if not mostrou:
            st.caption("Nenhuma questão neste filtro.")

    st.divider()
    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("← Voltar aos simulados", key="sim_rev_voltar", width="stretch"):
            st.session_state["mode"] = None
            st.session_state["go_to"] = "Simulados"
            st.rerun()
    with b2:
        if st.button("🔁 Refazer simulado", key="sim_refazer", width="stretch",
                     help="Nova tentativa com as mesmas questões (ordem embaralhada)"):
            novo = refazer_simulado(sim)
            if novo:
                _abrir_simulado(novo)
                st.rerun()
    with b3:
        if st.button("🎯 Refazer só as erradas", key="sim_refazer_err", width="stretch",
                     disabled=r["acertos"] == r["total"]):
            novo = refazer_simulado(sim, somente_erradas=True)
            if novo:
                _abrir_simulado(novo)
                st.rerun()

    alvo = st.session_state.pop(f"sim_scroll_{sim_id}", None)
    if alvo:
        _sim_rolar_para(f"simq-{alvo}")


# =============================
# AUTH — helpers de senha
# =============================
def _hash_senha(senha: str) -> str:
    if not _BCRYPT_OK:
        raise RuntimeError("Pacote 'bcrypt' não instalado.")
    return bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()


def _verificar_senha(senha: str, senha_hash: str) -> bool:
    if not senha_hash:
        return False
    if senha_hash.startswith("$2"):
        try:
            return bcrypt.checkpw((senha or "").encode(), senha_hash.encode())
        except Exception:
            return False
    # Legado: SHA-256 sem salt (versões antigas); migrado para bcrypt no próximo login
    return secrets.compare_digest(hashlib.sha256((senha or "").encode()).hexdigest(), senha_hash)


_ALFABETO_CODIGO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def gerar_codigo_primeiro_acesso(login):
    """Gera código de uso único (7 dias) e coloca o usuário em 1º acesso."""
    codigo = "".join(secrets.choice(_ALFABETO_CODIGO) for _ in range(8))
    atualizar_usuario(login, {
        "senha_hash": None,
        "primeiro_acesso": True,
        "codigo_hash": _hash_senha(codigo),
        "codigo_expira_em": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    })
    return codigo


def validar_codigo_primeiro_acesso(usuario, codigo):
    codigo = (codigo or "").strip().upper()
    if not codigo:
        return False
    if usuario.get("login") == ADMIN_LOGIN:
        setup = str(_secret("ADMIN_SETUP_CODE", "")).strip().upper()
        if setup and secrets.compare_digest(codigo, setup):
            return True
    h = usuario.get("codigo_hash")
    exp = usuario.get("codigo_expira_em")
    if not h or (exp and exp < _now_iso()):
        return False
    return _verificar_senha(codigo, h)


# =============================
# AUTH — banco de dados de usuários
# =============================
def init_usuarios(db):
    """Garante que o admin inicial existe (senha inicial vem dos Secrets)."""
    if db.usuarios.count_documents({"login": ADMIN_LOGIN}, limit=1) == 0:
        senha_ini = str(_secret("ADMIN_INITIAL_PASSWORD", "") or "")
        db.usuarios.insert_one({
            "nome": "Administrador",
            "login": ADMIN_LOGIN,
            "senha_hash": _hash_senha(senha_ini) if senha_ini else None,
            "perfil": "admin",
            "ativo": True,
            "primeiro_acesso": not senha_ini,
            "data_criacao": _now_iso(),
            "data_ultima_atualizacao": _now_iso(),
        })


def get_usuario_por_login(login: str):
    return get_db().usuarios.find_one({"login": login.strip().lower()})


def criar_usuario(nome, login, perfil="usuario", criado_por="sistema"):
    """Cria o usuário e retorna o código de primeiro acesso."""
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
        "data_criacao": _now_iso(),
        "data_ultima_atualizacao": _now_iso(),
    })
    return gerar_codigo_primeiro_acesso(login)


def atualizar_senha_usuario(login: str, nova_senha: str, primeiro_acesso=False):
    get_db().usuarios.update_one(
        {"login": login},
        {"$set": {
            "senha_hash": _hash_senha(nova_senha),
            "primeiro_acesso": primeiro_acesso,
            "data_ultima_atualizacao": _now_iso(),
        },
         "$unset": {"codigo_hash": "", "codigo_expira_em": ""}}
    )


def listar_usuarios():
    return list(get_db().usuarios.find({}, {"_id": 0, "senha_hash": 0, "codigo_hash": 0}).sort("data_criacao", ASCENDING))


def atualizar_usuario(login, campos: dict):
    campos["data_ultima_atualizacao"] = _now_iso()
    get_db().usuarios.update_one({"login": login}, {"$set": campos})


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
        if usuario and usuario.get("ativo", True) and (usuario.get("primeiro_acesso") or not usuario.get("senha_hash")):
            st.session_state["_auth_primeiro_acesso_login"] = login_input
            st.rerun()
        if not usuario or not _verificar_senha(senha_input, usuario.get("senha_hash")):
            st.error("Usuário ou senha inválidos.")
            return
        if not usuario.get("ativo", True):
            st.error("Usuário inativo. Contate o administrador.")
            return
        if not usuario["senha_hash"].startswith("$2"):
            atualizar_senha_usuario(usuario["login"], senha_input)  # migra hash legado
        st.session_state["_auth_usuario"] = {
            "login": usuario["login"],
            "nome": usuario.get("nome", ""),
            "perfil": usuario.get("perfil", "usuario"),
        }
        st.rerun()


def tela_primeiro_acesso(login: str):
    st.title("🔑 Primeiro Acesso — Defina sua senha")
    st.info(f"Bem-vindo(a), **{login}**! Informe o código de primeiro acesso recebido do administrador e defina sua senha.")
    col, _ = st.columns([1, 2])
    with col:
        codigo = st.text_input("Código de primeiro acesso", key="pa_codigo")
        s1 = st.text_input("Nova senha", type="password", key="pa_s1")
        s2 = st.text_input("Confirmar senha", type="password", key="pa_s2")
        salvar = st.button("Definir senha", type="primary")
        if st.button("← Voltar ao login", key="pa_voltar"):
            st.session_state.pop("_auth_primeiro_acesso_login", None)
            st.rerun()
    if salvar:
        usuario = get_usuario_por_login(login)
        if not usuario or not validar_codigo_primeiro_acesso(usuario, codigo):
            st.error("Código inválido ou expirado. Peça um novo código ao administrador.")
            if login == ADMIN_LOGIN:
                st.caption("Administrador principal: defina ADMIN_SETUP_CODE (ou ADMIN_INITIAL_PASSWORD) nos Secrets.")
            return
        if len(s1) < 6:
            st.warning("A senha deve ter pelo menos 6 caracteres.")
            return
        if s1 != s2:
            st.error("As senhas não coincidem.")
            return
        atualizar_senha_usuario(login, s1, primeiro_acesso=False)
        st.success("Senha definida com sucesso! Faça login agora.")
        st.session_state.pop("_auth_primeiro_acesso_login", None)
        st.rerun()


def checar_autenticacao():
    """Retorna True se o usuário está autenticado; senão mostra login / primeiro acesso."""
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

    cod = st.session_state.get("_codigo_gerado")
    if cod:
        st.success(f"Código de primeiro acesso de **{cod[0]}**: `{cod[1]}` — válido por 7 dias. "
                   "Anote e envie ao usuário agora; ele não será exibido novamente.")
        if st.button("Ok, anotei", key="btn_codigo_ok"):
            st.session_state.pop("_codigo_gerado", None)
            st.rerun()

    usuarios = listar_usuarios()
    if usuarios:
        rows = [{
            "Login": u.get("login", ""),
            "Nome": u.get("nome", ""),
            "Perfil": u.get("perfil", ""),
            "Ativo": "✅" if u.get("ativo") else "❌",
            "1º Acesso": "⏳" if u.get("primeiro_acesso") else "—",
            "Criado em": (u.get("data_criacao") or "")[:10],
        } for u in usuarios]
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.info("Nenhum usuário cadastrado.")

    st.divider()

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
                    codigo = criar_usuario(novo_nome, novo_login, novo_perfil, criado_por=login_atual())
                    st.session_state["_codigo_gerado"] = (novo_login.strip().lower(), codigo)
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

    if usuarios:
        st.subheader("Editar usuário")
        logins = [u["login"] for u in usuarios]
        sel_login = st.selectbox("Selecionar", logins, key="edit_u_sel")
        u_sel = next((u for u in usuarios if u["login"] == sel_login), None)
        if u_sel:
            protegido = sel_login == ADMIN_LOGIN
            eu_mesmo = sel_login == login_atual()
            c1, c2, c3 = st.columns(3)
            with c1:
                ed_nome = st.text_input("Nome", value=u_sel.get("nome", ""), key=f"ed_nome_{sel_login}")
            with c2:
                ed_perfil = st.selectbox("Perfil", ["usuario", "admin"],
                                         index=0 if u_sel.get("perfil", "usuario") == "usuario" else 1,
                                         key=f"ed_perfil_{sel_login}", disabled=protegido)
            with c3:
                ed_ativo = st.checkbox("Ativo", value=bool(u_sel.get("ativo", True)),
                                       key=f"ed_ativo_{sel_login}", disabled=protegido or eu_mesmo)
            if protegido:
                st.caption("O administrador principal não pode ser rebaixado nem desativado.")

            b1, b2, b3 = st.columns(3)
            with b1:
                if st.button("Salvar alterações", key="btn_salvar_usuario"):
                    perde_admin = u_sel.get("perfil") == "admin" and (ed_perfil != "admin" or not ed_ativo)
                    outros_admins = sum(1 for u in usuarios if u.get("perfil") == "admin"
                                        and u.get("ativo", True) and u["login"] != sel_login)
                    if perde_admin and outros_admins == 0:
                        st.error("Deve existir ao menos um administrador ativo.")
                    else:
                        atualizar_usuario(sel_login, {"nome": ed_nome, "perfil": ed_perfil, "ativo": ed_ativo})
                        st.success("Usuário atualizado.")
                        st.rerun()
            with b2:
                if botao_confirmado("Resetar senha (gera código)", key=f"btn_reset_pw_{sel_login}",
                                    mensagem=f"Invalidar a senha de '{sel_login}' e gerar um novo código de primeiro acesso?"):
                    st.session_state["_codigo_gerado"] = (sel_login, gerar_codigo_primeiro_acesso(sel_login))
                    st.rerun()
            with b3:
                if not protegido:
                    nova_pw = st.text_input("Definir senha manualmente", type="password", key="ed_pw")
                    if st.button("Definir senha", key="btn_def_pw"):
                        if len(nova_pw) < 6:
                            st.warning("Mínimo 6 caracteres.")
                        else:
                            atualizar_senha_usuario(sel_login, nova_pw, primeiro_acesso=False)
                            st.success("Senha definida.")


# =============================
# MÓDULO DE ESTUDOS — flashcards
# =============================
def _fc_query(item_id):
    """Busca por item_id como string OU ObjectId para compatibilidade com dados antigos."""
    oid = _oid(item_id)
    return {"item_id": {"$in": [str(item_id), oid]}} if oid else {"item_id": str(item_id)}


def fc_listar(item_id):
    return list(get_db().flashcards.find(_fc_query(item_id)).sort("ordem", ASCENDING))


def fc_adicionar(item_id, frente, verso):
    """Adiciona um flashcard ao item. Sempre salva item_id como string."""
    db = get_db()
    item_id_str = str(item_id)
    ultimo = db.flashcards.find_one(_fc_query(item_id_str), sort=[("ordem", DESCENDING)])
    ordem = (ultimo["ordem"] + 1) if ultimo and "ordem" in ultimo else 0
    db.flashcards.insert_one({
        "item_id": item_id_str,
        "frente": frente.strip(),
        "verso": verso.strip(),
        "ordem": ordem,
        "data_criacao": _now_iso(),
    })


def fc_excluir(fc_id):
    get_db().flashcards.delete_one({"_id": ObjectId(fc_id)})


def fc_excluir_todos(item_id):
    get_db().flashcards.delete_many(_fc_query(item_id))


def _fc_excluir_de_itens(item_ids):
    ids = [str(i) for i in item_ids if i]
    if ids:
        oids = [o for o in (_oid(i) for i in ids) if o]
        get_db().flashcards.delete_many({"item_id": {"$in": ids + oids}})


def fc_cards_de_itens(item_ids):
    """Cards de vários itens (próprios + herdados), sem duplicatas, numa consulta."""
    ids = list(dict.fromkeys(str(i) for i in item_ids if i))
    if not ids:
        return []
    oids = [o for o in (_oid(i) for i in ids) if o]
    docs = list(get_db().flashcards.find({"item_id": {"$in": ids + oids}}))
    docs.sort(key=lambda c: (ids.index(str(c["item_id"])) if str(c["item_id"]) in ids else 99, c.get("ordem", 0)))
    return [{"frente": c.get("frente", ""), "verso": c.get("verso", "")} for c in docs]


def fc_render(cards, titulo, height=440):
    """Componente de flashcards (texto inserido via textContent, sem HTML injetável)."""
    if not cards:
        return False
    cards_json = json.dumps(cards, ensure_ascii=False).replace("</", "<\\/")
    components.html(f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f4f8;padding:16px;min-height:100%}}
.wrap{{background:#fff;border-radius:14px;padding:24px 28px;box-shadow:0 4px 24px rgba(0,0,0,.12)}}
.label{{font-size:11px;font-weight:700;color:#19747E;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px}}
.titulo{{font-size:17px;font-weight:600;color:#1a1a1a;margin-bottom:20px;line-height:1.3}}
.fc-card{{width:100%;min-height:160px;perspective:800px;cursor:pointer;margin-bottom:8px}}
.fc-inner{{width:100%;min-height:160px;position:relative;transform-style:preserve-3d;transition:transform .4s ease;border-radius:12px}}
.fc-inner.flipped{{transform:rotateY(180deg)}}
.fc-face{{position:absolute;width:100%;min-height:160px;backface-visibility:hidden;border-radius:12px;display:flex;
  align-items:center;justify-content:center;padding:20px;text-align:center;font-size:15px;line-height:1.6;white-space:pre-wrap}}
.fc-front{{background:#f0f9ff;border:2px solid #19747E;color:#1a1a1a}}
.fc-back{{background:#f6fbf7;border:2px solid #28a745;color:#1a1a1a;transform:rotateY(180deg)}}
.hint{{font-size:11px;color:#aaa;text-align:center;margin-bottom:14px}}
.nav{{display:flex;align-items:center;justify-content:space-between;margin-top:4px}}
.btn{{padding:8px 20px;border-radius:8px;border:1px solid #ddd;background:#f2f2f2;cursor:pointer;font-size:13px;font-weight:600}}
.btn:hover:not(:disabled){{background:#e0e0e0}}
.btn:disabled{{opacity:.3;cursor:default}}
.counter{{font-size:13px;color:#555;font-weight:600}}
.empty{{color:#aaa;text-align:center;padding:30px}}
</style></head><body><div class='wrap'>
<div class='label'>Flashcards</div>
<div class='titulo'>{esc(titulo)}</div>
<div id='body'></div>
</div>
<script>
const cards = {cards_json};
let idx = 0;
function el(tag, cls, txt) {{
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt !== undefined) e.textContent = txt;
  return e;
}}
function render() {{
  const b = document.getElementById('body');
  b.replaceChildren();
  if (!cards.length) {{ b.appendChild(el('p', 'empty', 'Sem cards.')); return; }}
  const c = cards[idx];
  const card = el('div', 'fc-card'), inner = el('div', 'fc-inner');
  inner.appendChild(el('div', 'fc-face fc-front', c.frente));
  inner.appendChild(el('div', 'fc-face fc-back', c.verso));
  card.appendChild(inner);
  card.onclick = () => inner.classList.toggle('flipped');
  b.appendChild(card);
  b.appendChild(el('div', 'hint', 'Clique no card para virar'));
  const nav = el('div', 'nav');
  const prev = el('button', 'btn', '← Anterior');
  prev.disabled = idx === 0;
  prev.onclick = () => {{ idx--; render(); }};
  const next = el('button', 'btn', 'Próximo →');
  next.disabled = idx === cards.length - 1;
  next.onclick = () => {{ idx++; render(); }};
  nav.append(prev, el('span', 'counter', (idx + 1) + ' / ' + cards.length), next);
  b.appendChild(nav);
}}
render();
</script></body></html>""", height=height, scrolling=False)
    return True


# =============================
# MÓDULO DE ESTUDOS — banco de dados
# =============================
# --- Planos ---
def est_listar_planos(usuario_login):
    return list(get_db().est_planos.find({"usuario_login": usuario_login}).sort("data_criacao", ASCENDING))


def est_criar_plano(usuario_login, nome):
    db = get_db()
    nome = nome.strip()
    if not nome or db.est_planos.find_one({"usuario_login": usuario_login, "nome": nome}):
        return None
    res = db.est_planos.insert_one({
        "usuario_login": usuario_login,
        "nome": nome,
        "data_criacao": _now_iso(),
    })
    return str(res.inserted_id)


def est_excluir_plano(plano_id):
    db = get_db()
    oid = ObjectId(plano_id)
    _fc_excluir_de_itens([d["_id"] for d in db.est_planejamento.find({"plano_id": oid}, {"_id": 1})])
    discs = [d["_id"] for d in db.est_disciplinas.find({"plano_id": oid}, {"_id": 1})]
    db.est_assuntos.delete_many({"disciplina_id": {"$in": discs}})
    db.est_disciplinas.delete_many({"plano_id": oid})
    db.est_planejamento.delete_many({"plano_id": oid})
    db.est_planos.delete_one({"_id": oid})


def est_renomear_plano(plano_id, novo_nome):
    get_db().est_planos.update_one({"_id": ObjectId(plano_id)}, {"$set": {"nome": novo_nome.strip()}})


def est_progresso_plano(plano_id):
    """
    Dados consolidados de progresso do plano:
    - por disciplina: total de assuntos, estudados, pendentes
      (um assunto "continuado" em vários dias conta uma vez; está estudado
       quando a parte mais recente estiver estudada)
    - assuntos estudados, ordenados pela última vez que foram vistos
      (estudo ou revisão), para o guia de revisão
    - revisões pendentes (inclui as atrasadas) e histórico de revisões feitas
    """
    db = get_db()
    docs = list(db.est_planejamento.find({"plano_id": ObjectId(plano_id)}))

    grupos = {}
    revisoes_feitas, revisoes_pendentes = [], []
    ultima_rev = {}  # chave do assunto -> data da última revisão concluída

    for d in docs:
        tipo = d.get("tipo", "assunto")
        status = d.get("status", "pendente")
        disc = d.get("disciplina_nome") or "(Atividades)"
        nome = d.get("assunto_nome", "")
        data = d.get("data", "")
        atualizado = d.get("data_atualizacao") or d.get("data_criacao") or ""
        chave = str(d.get("assunto_id") or d["_id"])

        if tipo == "revisao":
            item = {"assunto_nome": nome, "disciplina_nome": disc, "data": data}
            if status == "estudado":
                revisoes_feitas.append({**item, "data_atualizacao": atualizado})
                ultima_rev[chave] = max(ultima_rev.get(chave, ""), (atualizado or data)[:10])
            else:
                revisoes_pendentes.append(item)
            continue
        if tipo == "atividade":
            continue
        grupos.setdefault(chave, []).append(d)

    disc_map = {}
    assuntos_estudados = []
    for chave, partes in grupos.items():
        partes.sort(key=lambda x: (x.get("data", ""), x.get("data_criacao", "")))
        primeira, ultima = partes[0], partes[-1]
        disc = primeira.get("disciplina_nome") or "(Atividades)"
        estudado = ultima.get("status") == "estudado"
        atualizado = ultima.get("data_atualizacao") or ultima.get("data_criacao") or ""
        info = disc_map.setdefault(disc, {"total": 0, "estudados": 0, "assuntos": []})
        info["total"] += 1
        item_ids = [str(p["_id"]) for p in partes]
        if estudado:
            info["estudados"] += 1
            ref = max((atualizado or "")[:10], ultima_rev.get(chave, "")) or ultima.get("data", "")
            assuntos_estudados.append({
                "item_id": item_ids[0],
                "item_ids": item_ids,
                "assunto_nome": primeira.get("assunto_nome", ""),
                "disciplina_nome": disc,
                "data": ultima.get("data", ""),
                "data_atualizacao": atualizado,
                "ultima_revisao": ultima_rev.get(chave),
                "data_referencia": ref,
                "n_flashcards": 0,
            })
        info["assuntos"].append({
            "nome": primeira.get("assunto_nome", ""),
            "status": "estudado" if estudado else "pendente",
            "partes": len(partes),
            "data": ultima.get("data", ""),
            "data_atualizacao": atualizado,
            "ordem_assunto": primeira.get("ordem_assunto", 9999),
        })

    for info in disc_map.values():
        info["assuntos"].sort(key=lambda x: x["ordem_assunto"])
    assuntos_estudados.sort(key=lambda x: x["data_referencia"])
    revisoes_pendentes.sort(key=lambda x: x["data"])
    revisoes_feitas.sort(key=lambda x: x["data"], reverse=True)

    # Contagem de flashcards (todas as partes do assunto)
    if assuntos_estudados:
        ids = [i for a in assuntos_estudados for i in a["item_ids"]]
        oids = [o for o in (_oid(i) for i in ids) if o]
        fc_counts = {}
        for fc in db.flashcards.find({"item_id": {"$in": ids + oids}}, {"item_id": 1}):
            k = str(fc["item_id"])
            fc_counts[k] = fc_counts.get(k, 0) + 1
        for a in assuntos_estudados:
            a["n_flashcards"] = sum(fc_counts.get(i, 0) for i in a["item_ids"])

    return {
        "disciplinas": disc_map,
        "assuntos_estudados": assuntos_estudados,
        "revisoes_pendentes": revisoes_pendentes,
        "revisoes_feitas": revisoes_feitas,
    }


def est_salvar_config_plano(plano_id, **kwargs):
    """Salva configurações do plano (ex: rev_auto=True) no documento do plano."""
    get_db().est_planos.update_one({"_id": ObjectId(plano_id)}, {"$set": kwargs})


def est_carregar_config_plano(plano_id):
    return get_db().est_planos.find_one({"_id": ObjectId(plano_id)}) or {}


# --- Disciplinas ---
def est_listar_disciplinas(plano_id):
    return list(get_db().est_disciplinas.find({"plano_id": ObjectId(plano_id)}).sort("nome", ASCENDING))


def est_criar_disciplina(plano_id, nome):
    db = get_db()
    nome = nome.strip()
    if not nome or db.est_disciplinas.find_one({"plano_id": ObjectId(plano_id), "nome": nome}):
        return None
    res = db.est_disciplinas.insert_one({
        "plano_id": ObjectId(plano_id),
        "nome": nome,
        "data_criacao": _now_iso(),
    })
    return str(res.inserted_id)


def est_excluir_disciplina(disc_id):
    db = get_db()
    oid = ObjectId(disc_id)
    _fc_excluir_de_itens([d["_id"] for d in db.est_planejamento.find({"disciplina_id": oid}, {"_id": 1})])
    db.est_assuntos.delete_many({"disciplina_id": oid})
    db.est_planejamento.delete_many({"disciplina_id": oid})
    db.est_disciplinas.delete_one({"_id": oid})


# --- Assuntos ---
def est_listar_assuntos(disc_id):
    """Retorna assuntos preservando ordem de inserção (campo 'ordem')."""
    return list(get_db().est_assuntos.find({"disciplina_id": ObjectId(disc_id)}).sort([("ordem", ASCENDING), ("_id", ASCENDING)]))


def est_importar_assuntos(disc_id, texto_colado):
    db = get_db()
    oid = ObjectId(disc_id)
    linhas = [l.strip() for l in texto_colado.splitlines() if l.strip()]
    ultimo = db.est_assuntos.find_one({"disciplina_id": oid}, sort=[("ordem", DESCENDING)])
    proximo_ordem = (ultimo["ordem"] + 1) if ultimo and "ordem" in ultimo else 0
    nomes_existentes = {d["nome"] for d in db.est_assuntos.find({"disciplina_id": oid}, {"nome": 1})}
    novos = []
    for linha in linhas:
        if linha not in nomes_existentes:
            novos.append({"disciplina_id": oid, "nome": linha, "ordem": proximo_ordem, "data_criacao": _now_iso()})
            proximo_ordem += 1
            nomes_existentes.add(linha)
    if novos:
        db.est_assuntos.insert_many(novos)
    return len(novos)


def est_editar_assunto(assunto_id, novo_nome):
    get_db().est_assuntos.update_one({"_id": ObjectId(assunto_id)}, {"$set": {"nome": novo_nome.strip()}})


def est_excluir_assunto(assunto_id):
    db = get_db()
    oid = ObjectId(assunto_id)
    _fc_excluir_de_itens([d["_id"] for d in db.est_planejamento.find({"assunto_id": oid}, {"_id": 1})])
    db.est_assuntos.delete_one({"_id": oid})
    db.est_planejamento.delete_many({"assunto_id": oid})


# --- Planejamento ---
def est_alocar_assunto(plano_id, data_str, assunto_id, disciplina_id, disciplina_nome, assunto_nome):
    db = get_db()
    if db.est_planejamento.find_one({
        "plano_id": ObjectId(plano_id), "data": data_str, "assunto_id": ObjectId(assunto_id),
    }):
        return False
    assunto_doc = db.est_assuntos.find_one({"_id": ObjectId(assunto_id)}, {"ordem": 1})
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
        "ordem_assunto": assunto_doc.get("ordem", 9999) if assunto_doc else 9999,
        "data_criacao": _now_iso(),
    })
    return True


def est_realocar_assunto(item_id, nova_data_str):
    """Move um item planejado para outra data."""
    get_db().est_planejamento.update_one(
        {"_id": ObjectId(item_id)},
        {"$set": {"data": nova_data_str, "data_atualizacao": _now_iso()}}
    )


def est_continuar_assunto(plano_id, item_id, nova_data_str, anotacao=""):
    """
    Copia o item para uma nova data mantendo o original.
    A cópia fica com tipo 'assunto', status 'pendente' e a anotação de onde o usuário parou.
    """
    db = get_db()
    origem = db.est_planejamento.find_one({"_id": ObjectId(item_id)})
    if not origem:
        return False
    if db.est_planejamento.find_one({
        "plano_id": ObjectId(plano_id), "data": nova_data_str,
        "assunto_id": origem.get("assunto_id"), "continuacao_de": item_id,
    }):
        return False
    db.est_planejamento.insert_one({
        "plano_id": ObjectId(plano_id),
        "data": nova_data_str,
        "assunto_id": origem.get("assunto_id"),
        "disciplina_id": origem.get("disciplina_id"),
        "disciplina_nome": origem.get("disciplina_nome", ""),
        "assunto_nome": origem.get("assunto_nome", ""),
        "descricao": anotacao.strip(),
        "tipo": "assunto",
        "status": "pendente",
        "links": [],
        "ordem_assunto": origem.get("ordem_assunto", 9999),
        "continuacao_de": item_id,
        "questionarios_vinculados": origem.get("questionarios_vinculados", []),
        "data_criacao": _now_iso(),
    })
    return True


def est_adicionar_atividade(plano_id, data_str, titulo, descricao=""):
    """Adiciona atividade manual (não vinculada a assunto/disciplina)."""
    get_db().est_planejamento.insert_one({
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
        "data_criacao": _now_iso(),
    })


def est_agendar_revisoes(plano_id, item_id, intervalos=None):
    """Agenda revisões espaçadas (dias após a data do item original). Ex: [1, 7, 30]"""
    if intervalos is None:
        intervalos = [1, 7, 30]
    db = get_db()
    origem = db.est_planejamento.find_one({"_id": ObjectId(item_id)})
    if not origem:
        return 0
    data_base = datetime.strptime(origem["data"], "%Y-%m-%d").date()
    agendadas = 0
    for dias in intervalos:
        data_str = (data_base + timedelta(days=dias)).strftime("%Y-%m-%d")
        if db.est_planejamento.find_one({
            "plano_id": ObjectId(plano_id), "data": data_str,
            "assunto_id": origem.get("assunto_id"), "tipo": "revisao",
        }):
            continue
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
            "data_criacao": _now_iso(),
        })
        agendadas += 1
    return agendadas


def est_remover_planejamento(item_id):
    db = get_db()
    # Mantém os flashcards se revisões/continuações ainda os herdam
    herdado = db.est_planejamento.find_one(
        {"$or": [{"revisao_origem_id": item_id}, {"continuacao_de": item_id}]}, {"_id": 1})
    if not herdado:
        _fc_excluir_de_itens([item_id])
    db.est_planejamento.delete_one({"_id": ObjectId(item_id)})


def est_marcar_status(item_id, status, plano_id=None, agendar_revisoes_auto=False, intervalos_revisao=None):
    db = get_db()
    db.est_planejamento.update_one(
        {"_id": ObjectId(item_id)},
        {"$set": {"status": status, "data_atualizacao": _now_iso()}}
    )
    if status == "estudado" and agendar_revisoes_auto and plano_id:
        doc = db.est_planejamento.find_one({"_id": ObjectId(item_id)}, {"tipo": 1})
        if doc and doc.get("tipo", "assunto") == "assunto":
            est_agendar_revisoes(plano_id, item_id, intervalos_revisao)
    elif status == "pendente":
        # Ao desfazer, remove revisões filhas pendentes geradas por este item
        filtro = {"revisao_origem_id": item_id, "status": "pendente"}
        _fc_excluir_de_itens([d["_id"] for d in db.est_planejamento.find(filtro, {"_id": 1})])
        db.est_planejamento.delete_many(filtro)


def est_buscar_planejamento_periodo(plano_id, data_inicio, data_fim):
    db = get_db()
    docs = list(db.est_planejamento.find(
        {"plano_id": ObjectId(plano_id),
         "data": {"$gte": data_inicio.strftime("%Y-%m-%d"), "$lte": data_fim.strftime("%Y-%m-%d")}},
        sort=[("ordem_assunto", ASCENDING), ("data_criacao", ASCENDING)]
    ))
    por_data = {}
    for d in docs:
        por_data.setdefault(d["data"], []).append({
            "id": str(d["_id"]),
            "assunto_id": str(d["assunto_id"]) if d.get("assunto_id") else "",
            "disciplina_nome": d.get("disciplina_nome", ""),
            "assunto_nome": d.get("assunto_nome", ""),
            "descricao": d.get("descricao", ""),
            "tipo": d.get("tipo", "assunto"),
            "status": d.get("status", "pendente"),
            "links": d.get("links", []),
            "disciplina_id": str(d["disciplina_id"]) if d.get("disciplina_id") else "",
            "questionarios_vinculados": d.get("questionarios_vinculados", []),
            "continuacao_de": d.get("continuacao_de"),
            "revisao_origem_id": str(d["revisao_origem_id"]) if d.get("revisao_origem_id") else None,
            "n_flashcards": 0,
        })

    # Flashcards: próprios + herdados do original (revisão) ou da parte anterior (continuação)
    def _fontes(item):
        return [x for x in (item["id"], item.get("revisao_origem_id"), item.get("continuacao_de")) if x]

    ids_busca = list({i for itens in por_data.values() for item in itens for i in _fontes(item)})
    if ids_busca:
        oids = [o for o in (_oid(i) for i in ids_busca) if o]
        fc_counts = {}
        for fc in db.flashcards.find({"item_id": {"$in": ids_busca + oids}}, {"item_id": 1}):
            k = str(fc["item_id"])
            fc_counts[k] = fc_counts.get(k, 0) + 1
        for itens in por_data.values():
            for item in itens:
                item["n_flashcards"] = sum(fc_counts.get(i, 0) for i in _fontes(item))
    return por_data


def est_calcular_distribuicao(n_assuntos, data_inicio, dias_semana_ativos,
                              intervalo=1, assuntos_por_dia=1):
    """
    Retorna lista onde agenda[i] = date do assunto i.
    intervalo=1: estuda em todos os dias ativos consecutivos.
    intervalo=2: estuda 1 dia ativo, pula 1 dia ativo, estuda...
    O intervalo conta apenas dias ativos, não dias corridos.
    """
    intervalo = max(1, int(intervalo))
    apd = max(1, int(assuntos_por_dia))
    if not dias_semana_ativos:
        return []

    agenda = []
    d = data_inicio
    dias_ativos_vistos = 0
    while len(agenda) < n_assuntos:
        if d.weekday() in dias_semana_ativos:
            if dias_ativos_vistos % intervalo == 0:
                agenda.extend([d] * min(apd, n_assuntos - len(agenda)))
            dias_ativos_vistos += 1
        d += timedelta(days=1)
    return agenda


def est_distribuir_disciplina(plano_id, disc_id, disc_nome, data_inicio,
                              dias_semana_ativos=None, intervalo=1, assuntos_por_dia=1):
    """Distribui assuntos preservando ordem, calculando a data final automaticamente."""
    assuntos = est_listar_assuntos(disc_id)
    if not assuntos:
        return 0, 0
    if dias_semana_ativos is None:
        dias_semana_ativos = list(range(7))

    agenda = est_calcular_distribuicao(len(assuntos), data_inicio, dias_semana_ativos, intervalo, assuntos_por_dia)
    alocados, ja_existiam = 0, 0
    for assunto, dia_alvo in zip(assuntos, agenda):
        if est_alocar_assunto(plano_id, dia_alvo.strftime("%Y-%m-%d"), str(assunto["_id"]),
                              disc_id, disc_nome, assunto["nome"]):
            alocados += 1
        else:
            ja_existiam += 1
    return alocados, ja_existiam


def est_adicionar_link(item_id, titulo, url):
    get_db().est_planejamento.update_one(
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
    if any(v["questionario_id"] == questionario_id for v in vinculados):
        return False
    vinculados.append({
        "questionario_id": questionario_id,
        "questionario_nome": questionario_nome,
        "disciplina_nome": disciplina_nome,
    })
    db.est_planejamento.update_one({"_id": ObjectId(item_id)}, {"$set": {"questionarios_vinculados": vinculados}})
    return True


def est_desvincular_questionario(item_id, questionario_id):
    get_db().est_planejamento.update_one(
        {"_id": ObjectId(item_id)},
        {"$pull": {"questionarios_vinculados": {"questionario_id": questionario_id}}}
    )


# =============================
# MÓDULO DE ESTUDOS — helpers
# =============================
def _semana_inicio_fim(ref):
    segunda = ref - timedelta(days=ref.weekday())
    return segunda, segunda + timedelta(days=6)


def _fmt_data_br(s):
    try:
        return date.fromisoformat(str(s)[:10]).strftime("%d/%m/%Y")
    except Exception:
        return str(s or "")


def _urgencia(dias):
    return "🔴" if dias >= 30 else ("🟡" if dias >= 7 else "🟢")


# =============================
# MÓDULO DE ESTUDOS — progresso do plano
# =============================
def _page_progresso_plano(plano_id, plano_nome):
    """Página de progresso e guia de revisões do plano."""
    if st.button("← Voltar ao plano", key="prog_voltar", help="Voltar para a agenda semanal do plano"):
        st.session_state.pop("est_prog_aberto", None)
        st.rerun()

    st.markdown(f"## 📊 Progresso — {plano_nome}")

    dados = est_progresso_plano(plano_id)
    disc_map = dados["disciplinas"]
    estudados = dados["assuntos_estudados"]
    rev_pendentes = dados["revisoes_pendentes"]
    rev_feitas = dados["revisoes_feitas"]

    if not disc_map:
        st.info("Nenhum assunto alocado ainda neste plano.")
        return

    total_geral = sum(v["total"] for v in disc_map.values())
    estudados_geral = sum(v["estudados"] for v in disc_map.values())
    pct_geral = int(estudados_geral / total_geral * 100) if total_geral else 0

    col_res = st.columns([3, 1])
    with col_res[0]:
        st.markdown(
            f"<div style='font-size:15px;font-weight:600;margin-bottom:2px'>"
            f"Progresso geral: {estudados_geral} / {total_geral} assuntos</div>"
            f"<div class='prog-bar-wrap'><div class='prog-bar-fill' style='width:{pct_geral}%'></div></div>",
            unsafe_allow_html=True
        )
    with col_res[1]:
        st.markdown(
            f"<div style='font-size:36px;font-weight:700;color:#19747E;text-align:center;padding-top:4px'>{pct_geral}%</div>",
            unsafe_allow_html=True
        )

    st.divider()

    tab1, tab2, tab3, tab4 = st.tabs([
        "📚 Por disciplina",
        "🔄 Guia de revisão",
        "📅 Revisões pendentes",
        "✅ Revisões feitas",
    ])

    with tab1:
        for disc_nome, info in sorted(disc_map.items()):
            total, feitos = info["total"], info["estudados"]
            pct = int(feitos / total * 100) if total else 0
            cor_barra = "#28a745" if pct == 100 else "#19747E"
            with st.expander(f"{disc_nome}  —  {feitos}/{total}  ({pct}%)", expanded=False):
                rows_html = (f"<div class='prog-bar-wrap'><div class='prog-bar-fill' "
                             f"style='width:{pct}%;background:{cor_barra}'></div></div>")
                for a in info["assuntos"]:
                    partes = f" <small>({a['partes']} partes)</small>" if a["partes"] > 1 else ""
                    if a["status"] == "estudado":
                        dt = a["data_atualizacao"][:10] if a["data_atualizacao"] else a["data"]
                        rows_html += (
                            f"<div class='prog-assunto-row'><span class='prog-feito'>✓ {esc(a['nome'])}{partes}</span>"
                            f"<span style='float:right;font-size:11px;color:#aaa'>{_fmt_data_br(dt)}</span></div>"
                        )
                    else:
                        rows_html += f"<div class='prog-assunto-row'><span class='prog-pend'>○ {esc(a['nome'])}{partes}</span></div>"
                st.markdown(rows_html, unsafe_allow_html=True)

    with tab2:
        if not estudados:
            st.info("Nenhum assunto marcado como estudado ainda.")
        else:
            hoje = date.today()
            rev_por_disc = {}
            for a in estudados:
                rev_por_disc.setdefault(a["disciplina_nome"] or "(Sem disciplina)", []).append(a)

            st.caption(
                f"{len(estudados)} assunto(s) estudados em {len(rev_por_disc)} disciplina(s) — ordenados pela "
                "última vez que foram vistos (estudo ou revisão), do mais antigo ao mais recente."
            )

            def _dias(a):
                try:
                    return (hoje - date.fromisoformat(a["data_referencia"][:10])).days
                except Exception:
                    return None

            for disc_nome, assuntos_disc in sorted(rev_por_disc.items()):
                max_dias = max((_dias(a) or 0 for a in assuntos_disc), default=0)
                with st.expander(f"{_urgencia(max_dias)} {disc_nome}  —  {len(assuntos_disc)} assunto(s)", expanded=False):
                    for a in assuntos_disc:
                        dias = _dias(a)
                        urg = _urgencia(dias) if dias is not None else "⚪"
                        tempo = f"{dias}d atrás" if dias is not None else ""
                        origem = "revisado" if a.get("ultima_revisao") and a["ultima_revisao"] >= (a["data_atualizacao"] or "")[:10] else "estudado"
                        n_fc = a.get("n_flashcards", 0)
                        rc1, rc2 = st.columns([8, 2])
                        with rc1:
                            st.markdown(
                                f"<div class='rev-card'>{urg} {esc(a['assunto_nome'])}"
                                f"<span class='rev-date' style='float:right'>{origem} em "
                                f"{_fmt_data_br(a['data_referencia'])} · {tempo}</span></div>",
                                unsafe_allow_html=True
                            )
                        with rc2:
                            if n_fc > 0:
                                k = f"fc_vis_rev_{a['item_id']}"
                                if st.button(f"🃏 {n_fc} cards", key=f"fc_rev_{a['item_id']}",
                                             width="stretch", help="Estudar flashcards"):
                                    st.session_state[k] = not st.session_state.get(k, False)
                                    st.rerun()
                        if n_fc > 0 and st.session_state.get(f"fc_vis_rev_{a['item_id']}"):
                            fc_render(fc_cards_de_itens(a["item_ids"]), a["assunto_nome"])

    with tab3:
        if not rev_pendentes:
            st.info("Nenhuma revisão pendente.")
        else:
            atrasadas = 0
            for r in rev_pendentes:
                try:
                    dias_restantes = (date.fromisoformat(r["data"]) - date.today()).days
                    if dias_restantes < 0:
                        prazo, cls = f"⚠️ {abs(dias_restantes)}d atrasada", "rev-card atrasada"
                        atrasadas += 1
                    elif dias_restantes == 0:
                        prazo, cls = "📌 Hoje", "rev-card"
                    else:
                        prazo, cls = f"em {dias_restantes}d", "rev-card"
                except Exception:
                    prazo, cls = "", "rev-card"
                st.markdown(
                    f"<div class='{cls}'><span style='font-size:11px;font-weight:700;color:#3a86ff;"
                    f"text-transform:uppercase;letter-spacing:.07em'>{esc(r['disciplina_nome'])}</span><br>"
                    f"🔁 {esc(r['assunto_nome'])}"
                    f"<span class='rev-date' style='float:right'>{_fmt_data_br(r['data'])} · {prazo}</span></div>",
                    unsafe_allow_html=True
                )
            st.caption(f"{len(rev_pendentes)} revisão(ões) pendente(s), {atrasadas} atrasada(s).")

    with tab4:
        if not rev_feitas:
            st.info("Nenhuma revisão concluída ainda.")
        else:
            st.caption(f"{len(rev_feitas)} revisão(ões) concluída(s).")
            for r in rev_feitas:
                st.markdown(
                    f"<div class='rev-card feita'><span style='font-size:11px;font-weight:700;color:#28a745;"
                    f"text-transform:uppercase;letter-spacing:.07em'>{esc(r['disciplina_nome'])}</span><br>"
                    f"✅ {esc(r['assunto_nome'])}"
                    f"<span class='rev-date' style='float:right'>{_fmt_data_br(r['data'])}</span></div>",
                    unsafe_allow_html=True
                )


# =============================
# MÓDULO DE ESTUDOS — tela inicial (lista de planos)
# =============================
def page_estudos():
    st.title("📅 Plano de Estudos")
    usuario = login_atual()

    if st.session_state.get("est_plano_aberto_id"):
        pid = st.session_state["est_plano_aberto_id"]
        if st.session_state.get("est_prog_aberto"):
            plano = next((p for p in est_listar_planos(usuario) if str(p["_id"]) == pid), None)
            if not plano:
                st.session_state.pop("est_plano_aberto_id", None)
                st.session_state.pop("est_prog_aberto", None)
                st.rerun()
            _page_progresso_plano(pid, plano["nome"])
        else:
            _page_estudos_plano(pid)
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
                        st.caption(f"Criado em {_fmt_data_br(criado)}")
                with c2:
                    if st.button("📖 Abrir", key=f"est_abrir_{pid}", width="stretch", help="Abrir a agenda semanal deste plano"):
                        st.session_state["est_plano_aberto_id"] = pid
                        st.session_state["est_semana_ref"] = date.today()
                        st.session_state.pop("est_prog_aberto", None)
                        st.rerun()
                    if st.button("📊 Progresso", key=f"est_prog_{pid}", width="stretch", help="Ver progresso geral, guia de revisões e histórico"):
                        st.session_state["est_plano_aberto_id"] = pid
                        st.session_state["est_prog_aberto"] = True
                        st.rerun()
                with c3:
                    novo_nome_p = st.text_input("Renomear plano", value=p["nome"], key=f"est_rename_{pid}",
                                                label_visibility="collapsed", placeholder="Renomear...")
                    if st.button("✏️ Renomear", key=f"est_btn_rename_{pid}", width="stretch", help="Salvar o novo nome digitado para este plano"):
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
                        if st.button("Sim, excluir", key=f"est_confirma_del_{pid}", type="primary",
                                     help="Confirmar exclusão permanente do plano e todos os seus dados"):
                            est_excluir_plano(pid)
                            st.session_state.pop(f"est_confirm_del_plano_{pid}", None)
                            st.rerun()
                    with cc2:
                        if st.button("Cancelar", key=f"est_cancela_del_{pid}", help="Cancelar e manter o plano"):
                            st.session_state.pop(f"est_confirm_del_plano_{pid}", None)
                            st.rerun()

    st.divider()
    st.subheader("➕ Criar novo plano")
    c1, c2 = st.columns([3, 1])
    with c1:
        novo_plano_nome = st.text_input("Nome do plano", key="est_novo_plano_nome", placeholder="Ex: Concurso TJDF 2025")
    with c2:
        st.write("")
        st.write("")
        if st.button("Criar plano", key="est_btn_criar_plano", type="primary", help="Criar um novo plano de estudos com o nome informado"):
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
    usuario = login_atual()
    plano_doc = get_db().est_planos.find_one({"_id": ObjectId(plano_id), "usuario_login": usuario})
    if not plano_doc:
        st.error("Plano não encontrado.")
        st.session_state.pop("est_plano_aberto_id", None)
        st.rerun()

    plano_nome = plano_doc.get("nome", "Plano")
    hoje = date.today()

    col_back, col_prog, col_title = st.columns([1, 1, 5])
    with col_back:
        if st.button("← Voltar", key="est_voltar_planos", help="Voltar para a lista de planos"):
            st.session_state.pop("est_plano_aberto_id", None)
            st.rerun()
    with col_prog:
        if st.button("📊 Progresso", key="est_btn_progresso", help="Ver progresso geral, guia de revisões e histórico deste plano"):
            st.session_state["est_prog_aberto"] = True
            st.rerun()
    with col_title:
        st.title(f"📋 {plano_nome}")

    # Revisão automática (preferência salva por plano)
    INTERVALOS_REVISAO = [1, 7, 30]
    _rev_key = f"est_rev_auto_{plano_id}"
    if _rev_key not in st.session_state:
        st.session_state[_rev_key] = plano_doc.get("rev_auto", False)

    def _salvar_rev_auto():
        est_salvar_config_plano(plano_id, rev_auto=st.session_state.get(_rev_key, False))

    with st.sidebar:
        st.divider()
        st.markdown("**⚙️ Revisão automática**")
        rev_auto = st.checkbox("Agendar revisões ao marcar estudado", key=_rev_key, on_change=_salvar_rev_auto)
        if rev_auto:
            st.caption("Ao marcar um assunto como estudado, revisões serão agendadas automaticamente em **+1, +7 e +30 dias**.")
    intervalos_rev = INTERVALOS_REVISAO if rev_auto else []

    # Navegação semanal
    st.session_state.setdefault("est_semana_ref", hoje)
    segunda, domingo = _semana_inicio_fim(st.session_state["est_semana_ref"])

    meses_pt = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
    nav1, nav2, nav3, nav4 = st.columns([1, 1, 3, 1])
    with nav1:
        if st.button("◀ Anterior", key="est_sem_ant", help="Ir para a semana anterior"):
            st.session_state["est_semana_ref"] = segunda - timedelta(days=7)
            st.rerun()
    with nav2:
        if st.button("📌 Hoje", key="est_hoje"):
            st.session_state["est_semana_ref"] = hoje
            st.rerun()
    with nav3:
        l_ini = f"{segunda.day}/{meses_pt[segunda.month - 1]}/{segunda.year}"
        l_fim = f"{domingo.day}/{meses_pt[domingo.month - 1]}/{domingo.year}"
        st.markdown(f"<h3 style='text-align:center;margin:4px 0'>Semana: {l_ini} → {l_fim}</h3>", unsafe_allow_html=True)
    with nav4:
        if st.button("Próxima ▶", key="est_sem_prox", help="Ir para a próxima semana"):
            st.session_state["est_semana_ref"] = segunda + timedelta(days=7)
            st.rerun()

    st.divider()
    col_agenda, col_painel = st.columns([3, 1])

    # ========== PAINEL LATERAL ==========
    with col_painel:
        _painel_disciplinas(plano_id, segunda, hoje)

    # ========== AGENDA SEMANAL ==========
    with col_agenda:
        planejamento = est_buscar_planejamento_periodo(plano_id, segunda, domingo)
        qs_vinculaveis = questionarios_visiveis(incluir_especiais=False)
        dias_semana_nomes = ["Segunda-feira", "Terça-feira", "Quarta-feira",
                             "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]

        for offset in range(7):
            dia_date = segunda + timedelta(days=offset)
            itens = planejamento.get(dia_date.strftime("%Y-%m-%d"), [])
            eh_hoje = dia_date == hoje

            n_ok = sum(1 for i in itens if i["status"] == "estudado")
            n_pend = len(itens) - n_ok
            icone_dia = ("✅" if n_pend == 0 else ("⏳" if n_ok > 0 else "📖")) if itens else ""
            exp_label = (
                f"{icone_dia} {dias_semana_nomes[offset]}, {dia_date.day:02d}/{dia_date.month:02d}"
                f"{' 📌 HOJE' if eh_hoje else ''}  ·  {f'{len(itens)} item(ns)' if itens else 'livre'}"
            ).strip()

            with st.expander(exp_label, expanded=eh_hoje):
                for item in itens:
                    _render_item_agenda(item, plano_id, dia_date, rev_auto, intervalos_rev, qs_vinculaveis)
                st.markdown("<div style='margin-bottom:6px'></div>", unsafe_allow_html=True)


def _painel_disciplinas(plano_id, segunda, hoje):
    st.subheader("📚 Disciplinas")
    disciplinas = est_listar_disciplinas(plano_id)

    with st.expander("➕ Nova disciplina"):
        nd = st.text_input("Nome", key="est_nd_nome")
        if st.button("Criar", key="est_btn_criar_disc"):
            if nd.strip():
                if est_criar_disciplina(plano_id, nd.strip()):
                    st.success("Criada!")
                    st.rerun()
                else:
                    st.warning("Já existe ou nome inválido.")

    if not disciplinas:
        st.info("Nenhuma disciplina ainda.")
        return

    disc_nomes = {str(d["_id"]): d["nome"] for d in disciplinas}
    disc_sel_id = st.selectbox("Disciplina ativa", list(disc_nomes.keys()),
                               format_func=lambda x: disc_nomes[x], key=f"est_disc_sel_{plano_id}")
    disc_sel_nome = disc_nomes.get(disc_sel_id, "")
    assuntos = est_listar_assuntos(disc_sel_id)

    with st.expander("📥 Importar assuntos (um por linha)"):
        texto_col = st.text_area("Cole aqui:", height=110, key="est_import_txt")
        if st.button("Importar", key="est_btn_import"):
            if texto_col.strip():
                n = est_importar_assuntos(disc_sel_id, texto_col)
                st.success(f"{n} assunto(s) importado(s).")
                st.rerun()

    with st.expander("🗓️ Distribuir todos os assuntos"):
        n_assuntos = len(assuntos)
        if n_assuntos == 0:
            st.caption("Nenhum assunto cadastrado.")
        else:
            d_ini = st.date_input("Data início", value=segunda, key="est_dist_ini", format="DD/MM/YYYY")
            apd_val = st.number_input("Assuntos por dia", min_value=1, max_value=20, value=1, step=1,
                                      key="est_dist_apd", help="Quantos assuntos serão alocados em cada dia de estudo.")
            intervalo_val = st.number_input("Intervalo entre dias de estudo", min_value=1, max_value=30, value=1,
                                            step=1, key="est_dist_intervalo",
                                            help="1=todos os dias | 2=dia sim, dia não | 3=aloca, pula 2…")
            dias_labels = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
            dias_sel = st.multiselect("Dias da semana", options=list(range(7)), default=list(range(5)),
                                      format_func=lambda x: dias_labels[x], key="est_dist_dias")

            if dias_sel:
                agenda_prev = est_calcular_distribuicao(n_assuntos, d_ini, dias_sel, intervalo_val, apd_val)
                if agenda_prev:
                    n_dias_unicos = len(set(agenda_prev))
                    st.info(f"**{n_assuntos} assunto(s)** · **{n_dias_unicos} dia(s) de estudo** · "
                            f"Termina em **{agenda_prev[-1].strftime('%d/%m/%Y')}**")
            else:
                st.warning("Selecione ao menos um dia da semana.")

            if st.button("Distribuir", key="est_btn_distribuir", type="primary", disabled=not dias_sel,
                         help="Alocar automaticamente todos os assuntos no calendário conforme as configurações acima"):
                alocados, ja_ex = est_distribuir_disciplina(plano_id, disc_sel_id, disc_sel_nome,
                                                            d_ini, dias_sel, intervalo_val, apd_val)
                if alocados:
                    st.success(f"✅ {alocados} assunto(s) distribuído(s).")
                if ja_ex:
                    st.info(f"{ja_ex} já estavam alocados (ignorados).")
                if alocados:
                    st.rerun()

    st.caption(f"**{len(assuntos)} assunto(s)**")
    if assuntos:
        assunto_opcoes = {str(a["_id"]): a["nome"] for a in assuntos}
        assunto_sel_id = st.selectbox("Assunto", list(assunto_opcoes.keys()),
                                      format_func=lambda x: assunto_opcoes[x], key="est_assunto_sel")
        assunto_sel_nome = assunto_opcoes.get(assunto_sel_id, "")

        with st.expander("✏️ Editar assunto selecionado"):
            novo_nome_a = st.text_input("Nome", value=assunto_sel_nome, key=f"est_edit_assunto_nome_{assunto_sel_id}")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("💾 Salvar", key="est_salvar_assunto", help="Salvar o novo nome do assunto"):
                    if novo_nome_a.strip():
                        est_editar_assunto(assunto_sel_id, novo_nome_a)
                        st.success("Salvo.")
                        st.rerun()
            with c2:
                if st.button("🗑️ Excluir", key="est_del_assunto", help="Excluir este assunto permanentemente"):
                    est_excluir_assunto(assunto_sel_id)
                    st.rerun()

        st.markdown("**Alocar assunto no dia:**")
        dia_sel = st.date_input("Dia", value=hoje, key="est_dia_sel", label_visibility="collapsed", format="DD/MM/YYYY")
        if st.button("📌 Alocar", key="est_btn_alocar", width="stretch", help="Adicionar este assunto no dia selecionado"):
            if est_alocar_assunto(plano_id, dia_sel.strftime("%Y-%m-%d"), assunto_sel_id,
                                  disc_sel_id, disc_sel_nome, assunto_sel_nome):
                st.success("Alocado!")
                st.rerun()
            else:
                st.warning("Já alocado neste dia.")

    with st.expander("🗑️ Excluir disciplina"):
        st.warning("Remove a disciplina, assuntos, planejamentos e flashcards vinculados.")
        confirma = st.checkbox(f'Confirmo exclusão de "{disc_sel_nome}"', key="est_conf_del_disc")
        if st.button("Excluir disciplina", key="est_btn_del_disc", disabled=not confirma):
            est_excluir_disciplina(disc_sel_id)
            st.success("Disciplina excluída.")
            st.rerun()


def _render_item_agenda(item, plano_id, dia_date, rev_auto, intervalos_rev, qs_vinculaveis):
    iid = item["id"]
    tipo = item.get("tipo", "assunto")
    feito = item["status"] == "estudado"
    novo_status = "pendente" if feito else "estudado"
    label_btn = "↩ Desfazer" if feito else "✅ Feito"

    card_cls = " ".join(c for c in ("tf-card", tipo if tipo != "assunto" else "", "feito" if feito else "") if c)
    label_cls = "tf-label feito" if feito else "tf-label"
    title_cls = "tf-title feito" if feito else "tf-title"

    if tipo == "revisao":
        label_txt = "Revisão"
    elif tipo == "atividade":
        label_txt = "Atividade"
    elif item.get("continuacao_de"):
        label_txt = f"↪ {item['disciplina_nome']} (continuação)" if item["disciplina_nome"] else "↪ Continuação"
    else:
        label_txt = item["disciplina_nome"] or ""

    lnks = item.get("links", [])
    links_html = "".join(
        f"<a class='tf-link' href='{esc(safe_url(l.get('url')))}' target='_blank' rel='noopener noreferrer'>🔗 {esc(l.get('titulo'))}</a>"
        for l in lnks if safe_url(l.get("url"))
    )
    if links_html:
        links_html = f"<div class='tf-links'>{links_html}</div>"
    qvs = item.get("questionarios_vinculados", [])
    n_fc = item.get("n_flashcards", 0)

    st.markdown(
        f"<div class='{card_cls}'>"
        f"<div class='{label_cls}'>{esc(label_txt)}</div>"
        f"<div class='{title_cls}'>{esc(item['assunto_nome'])}</div>"
        + (f"<div class='tf-fc'>🃏 {n_fc} flashcard(s)</div>" if n_fc else "")
        + (f"<div class='tf-desc'>{esc_rich(item['descricao'])}</div>" if item.get("descricao") else "")
        + links_html
        + ("<div class='tf-qv-label'>Questionários vinculados</div>" if qvs else "")
        + "</div>",
        unsafe_allow_html=True
    )

    # -- Botões de ação --
    b_feito, b_cont, b_mover, b_excluir = st.columns([2, 1, 1, 1])
    with b_feito:
        if st.button(label_btn, key=f"est_mk_{iid}", type="primary" if not feito else "secondary",
                     width="stretch", help="Marcar como estudado" if not feito else "Desfazer marcação de estudado"):
            est_marcar_status(iid, novo_status, plano_id=plano_id,
                              agendar_revisoes_auto=rev_auto, intervalos_revisao=intervalos_rev)
            st.rerun()
    with b_cont:
        if st.button("Continuar", key=f"est_cont_btn_{iid}", width="stretch",
                     help="Copiar este assunto para outro dia com anotação de onde parou"):
            if st.session_state.get("est_continuando_id") == iid:
                st.session_state.pop("est_continuando_id", None)
            else:
                st.session_state["est_continuando_id"] = iid
                st.session_state.pop("est_realocando_id", None)
            st.rerun()
    with b_mover:
        if st.button("Mover", key=f"est_realoc_btn_{iid}", width="stretch", help="Mover este item para outro dia"):
            if st.session_state.get("est_realocando_id") == iid:
                st.session_state.pop("est_realocando_id", None)
            else:
                st.session_state["est_realocando_id"] = iid
                st.session_state.pop("est_continuando_id", None)
            st.rerun()
    with b_excluir:
        if st.button("Excluir", key=f"est_rm_{iid}", width="stretch", help="Remover este item do planejamento"):
            est_remover_planejamento(iid)
            st.session_state.pop("est_realocando_id", None)
            st.session_state.pop("est_continuando_id", None)
            st.rerun()

    # -- Painel mover --
    if st.session_state.get("est_realocando_id") == iid:
        rc1, rc2, rc3 = st.columns([3, 1, 1])
        with rc1:
            nova_data = st.date_input("Mover para", value=dia_date, key=f"est_nova_data_{iid}", format="DD/MM/YYYY")
        with rc2:
            st.write("")
            st.write("")
            if st.button("Confirmar", key=f"est_confirmar_realoc_{iid}", type="primary", width="stretch",
                         help="Confirmar a nova data"):
                est_realocar_assunto(iid, nova_data.strftime("%Y-%m-%d"))
                st.session_state.pop("est_realocando_id", None)
                st.rerun()
        with rc3:
            st.write("")
            st.write("")
            if st.button("Cancelar", key=f"est_cancelar_realoc_{iid}", width="stretch", help="Cancelar a realocação"):
                st.session_state.pop("est_realocando_id", None)
                st.rerun()

    # -- Painel continuar --
    if st.session_state.get("est_continuando_id") == iid:
        st.markdown(
            "<hr style='border:none;border-top:1px solid #e8e8e8;margin:8px 0'>"
            "<div style='font-size:11px;font-weight:700;color:#8b5cf6;text-transform:uppercase;"
            "letter-spacing:.07em;margin-bottom:6px'>↪ Continuar em outro dia</div>",
            unsafe_allow_html=True
        )
        cc1, cc2 = st.columns([2, 3])
        with cc1:
            cont_data = st.date_input("Data", value=dia_date + timedelta(days=1), key=f"est_cont_data_{iid}",
                                      format="DD/MM/YYYY", help="Dia em que continuará o estudo deste assunto")
        with cc2:
            cont_nota = st.text_area("Onde parei / anotações", key=f"est_cont_nota_{iid}", height=68,
                                     placeholder="Ex: parei no art. 37, falta estudar incisos IV ao VIII...")
        ca1, ca2 = st.columns(2)
        with ca1:
            if st.button("Confirmar", key=f"est_cont_ok_{iid}", type="primary", width="stretch",
                         help="Criar cópia deste assunto na data escolhida"):
                ok = est_continuar_assunto(plano_id, iid, cont_data.strftime("%Y-%m-%d"), cont_nota or "")
                st.session_state.pop("est_continuando_id", None)
                if ok:
                    st.rerun()
                else:
                    st.warning("Já existe uma continuação deste assunto nesta data.")
        with ca2:
            if st.button("Cancelar", key=f"est_cont_cancel_{iid}", width="stretch", help="Cancelar"):
                st.session_state.pop("est_continuando_id", None)
                st.rerun()

    # -- Remover links individuais --
    for li, lnk in enumerate(lnks):
        lc1, lc2 = st.columns([6, 1])
        with lc1:
            st.caption(f"🔗 {lnk.get('titulo', '')}")
        with lc2:
            if st.button("✕", key=f"est_rl_{iid}_{li}", help="Remover link"):
                est_remover_link(iid, li)
                st.rerun()

    # -- Questionários vinculados --
    for qv in qvs:
        qvc1, qvc2, qvc3 = st.columns([2, 6, 1])
        with qvc1:
            if st.button("▶ Questões", key=f"est_pratico_{iid}_{qv['questionario_id']}", width="stretch",
                         help="Abrir este questionário na tela Praticar"):
                st.session_state["current_qid"] = qv["questionario_id"]
                st.session_state["go_to"] = "Praticar"
                st.rerun()
        with qvc2:
            st.markdown(f"<span style='font-size:12px;color:#555;line-height:2.2'>{esc(qv['questionario_nome'])}</span>",
                        unsafe_allow_html=True)
        with qvc3:
            if st.button("✕", key=f"est_desvq_{iid}_{qv['questionario_id']}", help="Desvincular"):
                est_desvincular_questionario(iid, qv["questionario_id"])
                st.rerun()

    # -- Flashcards: abre sob demanda --
    if n_fc > 0:
        fk = f"fc_vis_ag_{iid}"
        aberto = st.session_state.get(fk, False)
        if st.button("🙈 Ocultar flashcards" if aberto else f"🃏 Estudar {n_fc} flashcard(s)", key=f"fc_tg_{iid}"):
            st.session_state[fk] = not aberto
            st.rerun()
        if aberto:
            fc_render(fc_cards_de_itens([iid, item.get("revisao_origem_id"), item.get("continuacao_de")]),
                      item["assunto_nome"])

    # -- Expanders de ação --
    exp1, exp2, exp3 = st.columns(3)
    with exp1:
        with st.expander("📝 Vincular questionário"):
            if not qs_vinculaveis:
                st.caption("Nenhum questionário disponível.")
            else:
                discs_qs = sorted({(q.get("disciplina") or "Sem Disciplina") for q in qs_vinculaveis})
                disc_filt = st.selectbox("Disciplina", ["Todas"] + discs_qs, key=f"est_qfilt_disc_{iid}")
                qs_filtrados = qs_vinculaveis if disc_filt == "Todas" else [
                    q for q in qs_vinculaveis if (q.get("disciplina") or "Sem Disciplina") == disc_filt]
                busca_q = st.text_input("Buscar", key=f"est_qbusca_{iid}", placeholder="Nome do questionário...")
                if busca_q:
                    qs_filtrados = [q for q in qs_filtrados if busca_q.lower() in q["nome"].lower()]
                if qs_filtrados:
                    q_opcoes = {q["id"]: q["nome"] for q in qs_filtrados}
                    q_sel_id = st.selectbox("Questionário", list(q_opcoes.keys()),
                                            format_func=lambda x: q_opcoes[x], key=f"est_qsel_{iid}")
                    q_sel = next((q for q in qs_filtrados if q["id"] == q_sel_id), None)
                    if st.button("Vincular", key=f"est_qvincular_{iid}", type="primary", width="stretch",
                                 help="Vincular o questionário selecionado a este assunto"):
                        if q_sel:
                            if est_vincular_questionario(iid, q_sel["id"], q_sel["nome"], q_sel.get("disciplina", "")):
                                st.success("Vinculado!")
                                st.rerun()
                            else:
                                st.info("Já vinculado.")
                else:
                    st.caption("Nenhum questionário encontrado.")
    with exp2:
        with st.expander("🃏 Flashcards"):
            if n_fc:
                st.caption(f"{n_fc} card(s) cadastrado(s)")
                st.divider()
            with st.form(key=f"fc_add_{iid}"):
                st.caption("Cole um card por linha: **pergunta ; resposta**")
                fc_lote = st.text_area("Cards (um por linha)", height=120, key=f"fc_lote_{iid}",
                                       placeholder="Qual o conceito de X? ; Resposta aqui...")
                if st.form_submit_button("Adicionar cards"):
                    ok_count, err_count = 0, 0
                    for linha in (l.strip() for l in fc_lote.splitlines() if l.strip()):
                        frente, _, verso = linha.partition(";")
                        if frente.strip() and verso.strip():
                            fc_adicionar(iid, frente, verso)
                            ok_count += 1
                        else:
                            err_count += 1
                    if err_count:
                        st.warning(f"{err_count} linha(s) ignorada(s) — use o formato: pergunta ; resposta")
                    if ok_count:
                        st.success(f"{ok_count} card(s) adicionado(s).")
                        st.rerun()
    with exp3:
        with st.expander("🔗 Adicionar link"):
            with st.form(key=f"est_add_link_{iid}"):
                lt = st.text_input("Título", key=f"est_lt_{iid}")
                lu = st.text_input("URL", key=f"est_lu_{iid}")
                if st.form_submit_button("Adicionar"):
                    if lt and lu:
                        if safe_url(lu):
                            est_adicionar_link(iid, lt, lu.strip())
                            st.rerun()
                        else:
                            st.warning("A URL deve começar com http:// ou https://")

    st.markdown("<div style='margin-bottom:4px'></div>", unsafe_allow_html=True)


# =============================
# Main Navigation
# =============================
def main():
    if not _BCRYPT_OK:
        st.error("O pacote **bcrypt** é obrigatório. Adicione `bcrypt` ao requirements.txt e reinicie o app.")
        st.stop()

    if "db_checked" not in st.session_state:
        if not connection_status():
            st.stop()
        st.session_state["db_checked"] = True
    init_db()  # cacheado: roda uma vez por processo

    if not checar_autenticacao():
        st.stop()

    auth_sidebar()
    for msg in st.session_state.pop("_toasts", []):
        st.toast(msg)

    if not st.session_state.get("_cadernos_ok"):
        garantir_cadernos()
        st.session_state["_cadernos_ok"] = True

    # link direto para um questionário (?q=<id>), ex.: o gerado após importar um CSV
    link_q = st.query_params.get("q")
    if link_q:
        if link_q in {q["id"] for q in questionarios_visiveis()}:
            _abrir_questionario(link_q)
        else:
            st.toast("Questionário do link não encontrado.")
        del st.query_params["q"]

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
