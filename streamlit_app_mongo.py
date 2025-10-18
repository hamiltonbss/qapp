
import os
import io
import csv
import random
from datetime import datetime
from typing import Dict, List, Tuple, Optional

import streamlit as st
from pymongo import MongoClient, ASCENDING, errors as pymongo_errors
from bson import ObjectId

# =======================
# Config & Secrets
# =======================

MONGO_URI = st.secrets.get("MONGO_URI", os.getenv("MONGO_URI", ""))
MONGO_DB_NAME = st.secrets.get("MONGO_DB_NAME", os.getenv("MONGO_DB_NAME", "quizdb"))

# =======================
# Cached Mongo Connection
# =======================

@st.cache_resource(show_spinner=False)
def get_client():
    if not MONGO_URI:
        raise RuntimeError("MONGO_URI não definido em st.secrets['MONGO_URI'] ou variável de ambiente MONGO_URI.")
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)

@st.cache_resource(show_spinner=False)
def get_db_cached():
    return get_client()[MONGO_DB_NAME]

def get_db():
    # Mantém assinatura original
    return get_db_cached()

# =======================
# Conexão (Ping Preguiçoso)
# =======================

@st.cache_data(ttl=60, show_spinner=False)
def _ping_status():
    try:
        get_client().admin.command("ping")
        return True, None
    except Exception as e:
        return False, str(e)

def connection_status():
    with st.sidebar:
        st.caption("⚙️ Conexão MongoDB")
        if not MONGO_URI:
            st.error("MONGO_URI não definido em Secrets/Env.")
            return False
        ok, err = _ping_status()
        if ok:
            st.success("MongoDB conectado ✅")
            st.code(f"MONGO_DB_NAME={MONGO_DB_NAME}", language="bash")
        else:
            st.error(f"Falha de conexão: {err}")
        if st.button("🔄 Re-testar conexão"):
            _ping_status.clear()
            st.rerun()
        return ok

# =======================
# Helpers de Documento
# =======================

def _doc_to_row_q(doc):
    return {"_id": str(doc["_id"]), "nome": doc.get("nome","(sem nome)")}

def _doc_to_row_questao(doc):
    return {
        "_id": str(doc["_id"]),
        "questionario_id": str(doc.get("questionario_id")) if doc.get("questionario_id") else None,
        "tipo": doc.get("tipo","VF"),
        "texto": doc.get("texto",""),
        "alternativas": doc.get("alternativas", []),
        "gabarito": doc.get("gabarito", None),
        "explicacao": doc.get("explicacao", ""),
        "tags": doc.get("tags", []),
        "created_at": doc.get("created_at")
    }

# =======================
# Cache leve de consultas
# =======================

@st.cache_data(ttl=15)
def _cached_questionarios():
    db = get_db()
    cur = db.questionarios.find({}).sort("nome", ASCENDING)
    return [_doc_to_row_q(x) for x in cur]

def get_questionarios():
    try:
        return _cached_questionarios()
    except Exception as e:
        st.error(f"[get_questionarios] erro: {e}")
        return []

@st.cache_data(ttl=15)
def _cached_questoes(questionario_id: str):
    db = get_db()
    qid = ObjectId(questionario_id)
    cur = db.questoes.find({"questionario_id": qid}).sort("_id", ASCENDING)
    return [_doc_to_row_questao(x) for x in cur]

def get_questoes(questionario_id: str):
    return _cached_questoes(questionario_id)

def _invalidate_lists():
    _cached_questionarios.clear()
    _cached_questoes.clear()

# =======================
# Inicialização de índices
# =======================

def init_db():
    db = get_db()
    db.questionarios.create_index([("nome", ASCENDING)], unique=True, name="uq_nome")
    db.questoes.create_index([("questionario_id", ASCENDING)], name="ix_qid")
    db.questoes.create_index([("tags", ASCENDING)], name="ix_tags")

@st.cache_resource(show_spinner=False)
def _init_once():
    init_db()
    return True

# =======================
# CRUD Básico
# =======================

def ensure_questionario(nome: str) -> str:
    db = get_db()
    doc = db.questionarios.find_one({"nome": nome})
    if doc:
        return str(doc["_id"])
    res = db.questionarios.insert_one({"nome": nome, "created_at": datetime.utcnow()})
    _invalidate_lists()
    return str(res.inserted_id)

def add_questao(questionario_id: str, tipo: str, texto: str,
                alternativas: Optional[List[str]], gabarito, explicacao: str, tags: List[str]):
    db = get_db()
    qid = ObjectId(questionario_id)
    payload = {
        "questionario_id": qid,
        "tipo": tipo,
        "texto": texto,
        "alternativas": alternativas or [],
        "gabarito": gabarito,
        "explicacao": explicacao or "",
        "tags": tags or [],
        "created_at": datetime.utcnow()
    }
    db.questoes.insert_one(payload)
    _invalidate_lists()

# =======================
# Importador CSV com relatório
# =======================

def import_csv_to_db(file_obj) -> Tuple[int, List[str], Dict[str,int]]:
    """
    Aceita: arquivo CSV (upload) ou texto (StringIO).
    Colunas aceitas (case-insensitive): questionario, tipo, texto, alternativas, gabarito, explicacao, tags
    - alternativas separadas por || (duas barras verticais)
    - tags separadas por ; ou ,
    Retorna: (ok, erros[], impacto por questionário)
    """
    if hasattr(file_obj, "read"):
        content = file_obj.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="ignore")
        buf = io.StringIO(content)
    else:
        # Já é string
        buf = io.StringIO(str(file_obj))

    # Sniffer de delimitador
    sample = buf.read(2048)
    buf.seek(0)
    dialect = csv.Sniffer().sniff(sample, delimiters=";,")
    reader = csv.DictReader(buf, dialect=dialect)

    ok = 0
    erros: List[str] = []
    impacto: Dict[str,int] = {}

    for i, row in enumerate(reader, start=2):  # Head é linha 1
        try:
            questionario = (row.get("questionario") or row.get("Questionario") or "").strip()
            tipo = (row.get("tipo") or row.get("Tipo") or "VF").strip().upper()
            texto = (row.get("texto") or row.get("Texto") or "").strip()
            alternativas_raw = row.get("alternativas") or row.get("Alternativas") or ""
            alternativas = [a.strip() for a in alternativas_raw.split("||")] if alternativas_raw else []
            gabarito = (row.get("gabarito") or row.get("Gabarito") or "").strip()
            explicacao = (row.get("explicacao") or row.get("Explicacao") or "").strip()
            tags_raw = (row.get("tags") or row.get("Tags") or "").strip()
            tags = [t.strip() for t in csv.re.split(r"[;,]", tags_raw)] if tags_raw else []

            if not questionario or not texto:
                raise ValueError("Campos obrigatórios ausentes: 'questionario' e/ou 'texto'.")

            qid = ensure_questionario(questionario)
            # Normaliza gabarito para VF
            if tipo == "VF":
                gabarito_norm = str(gabarito).strip().upper() in {"V","VERDADEIRO","TRUE","T","1"}
            else:
                gabarito_norm = gabarito

            add_questao(qid, tipo, texto, alternativas, gabarito_norm, explicacao, tags)
            ok += 1
            impacto[questionario] = impacto.get(questionario, 0) + 1
        except Exception as e:
            erros.append(f"Linha {i}: {e}")

    return ok, erros, impacto

# =======================
# Renderização de Questões
# =======================

def render_questao(q_row, parent_qid: str, numero_q: Optional[int]=None, modo_edicao: bool=False):
    titulo = f"Q{numero_q}" if numero_q is not None else "Questão"
    st.markdown(f"#### {titulo} – {q_row['texto']}")

    key_prefix = f"{parent_qid}_{q_row['_id']}"
    if q_row["tipo"] == "VF":
        sel = st.radio("Sua resposta:", ["V", "F"], key=f"vf_{key_prefix}", horizontal=True, index=None)
        correto = ("V" if q_row["gabarito"] else "F")
        if st.button("Confirmar", key=f"btn_{key_prefix}"):
            acertou = (sel == correto)
            st.success("✔️ Correta!" if acertou else f"❌ Incorreta. Gabarito: {correto}")
    else:
        # Múltipla escolha
        alts = q_row.get("alternativas", [])
        idx = st.radio("Escolha:", list(range(1, len(alts)+1)), format_func=lambda i: alts[i-1],
                       key=f"mc_{key_prefix}", index=None, horizontal=False)
        if st.button("Confirmar", key=f"btn_{key_prefix}"):
            gab = q_row.get("gabarito")
            # gabarito pode ser índice (1..N) ou texto
            ok = False
            if isinstance(gab, int):
                ok = (idx == gab)
            else:
                ok = (alts[idx-1].strip() == str(gab).strip())
            st.success("✔️ Correta!" if ok else f"❌ Incorreta. Gabarito: {gab}")

    # Campo de explicação editável
    with st.expander("📝 Ver/editar explicação"):
        new_exp = st.text_area("Explicação", q_row.get("explicacao",""), key=f"exp_{key_prefix}")
        if st.button("Salvar explicação", key=f"saveexp_{key_prefix}"):
            db = get_db()
            db.questoes.update_one({"_id": ObjectId(q_row["_id"])}, {"$set": {"explicacao": new_exp}})
            st.toast("Explicação salva.")
            _invalidate_lists()
            st.rerun()

# =======================
# Páginas
# =======================

def page_importar():
    st.subheader("📥 Importar questões via CSV")

    # Key dinâmica para resetar o uploader após importação
    up = st.file_uploader(
        "Enviar arquivo CSV", type=["csv"],
        key=f"uploader_{st.session_state.get('uploader_key', 0)}"
    )

    txt = st.text_area("Ou cole o conteúdo CSV")
    go = st.button("Importar")

    if go and (up or txt.strip()):
        try:
            ok, erros, impacto = import_csv_to_db(up if up else txt)
            st.success(f"{ok} questões importadas com sucesso.")
            if impacto:
                st.write("**Impacto por questionário:**")
                for nome, qtd in sorted(impacto.items()):
                    st.write(f"• {nome}: {qtd}")

            if erros:
                with st.expander(f"⚠️ {len(erros)} registros com erro (mostrando até 100)"):
                    for e in erros[:100]:
                        st.code(e)

            # Reset de estado e rerun
            st.toast("Importação concluída. Atualizando a lista...")
            st.session_state["uploader_key"] = random.randint(1, 10**9)
            # limpar pools e respostas
            for k in list(st.session_state.keys()):
                if k.startswith(("pool_", "answered_", "result_", "vf_", "mc_", "exp_")):
                    del st.session_state[k]
            st.rerun()
        except Exception as e:
            st.error(f"Falha ao importar: {e}")

def page_gerenciar():
    st.subheader("🗂️ Gerenciar Questionários")
    rows = get_questionarios()
    if not rows:
        st.info("Nenhum questionário encontrado.")
        return

    nomes = {r["nome"]: r["_id"] for r in rows}
    escolha = st.selectbox("Escolha um questionário", sorted(nomes.keys()))
    qid = nomes[escolha]

    qs = get_questoes(qid)
    st.write(f"**{len(qs)}** questões.")
    for idx, r in enumerate(qs, start=1):
        with st.expander(f"Q{idx} • {r['tipo']} • {r['texto'][:70]}"):
            render_questao(r, parent_qid=qid, numero_q=idx, modo_edicao=True)

def page_praticar():
    st.subheader("🧠 Praticar")
    rows = get_questionarios()
    if not rows:
        st.info("Nenhum questionário encontrado.")
        return

    nomes = {r["nome"]: r["_id"] for r in rows}
    escolha = st.selectbox("Escolha um questionário", sorted(nomes.keys()))
    qid = nomes[escolha]

    # Se trocar de questionário, limpar estado antigo
    if st.session_state.get("current_qid") != qid:
        for k in list(st.session_state.keys()):
            if k.startswith(("pool_", "answered_", "result_", "vf_", "mc_", "exp_")):
                del st.session_state[k]
    st.session_state["current_qid"] = qid

    pool_key = f"pool_{qid}"
    if pool_key not in st.session_state:
        qs = get_questoes(qid)
        st.session_state[pool_key] = list(reversed(qs))  # stack (LIFO)
        st.session_state[f"answered_{qid}"] = 0
        st.session_state[f"correct_{qid}"] = 0

    pool = st.session_state[pool_key]
    if not pool:
        st.success("Fim! Você praticou todas as questões deste questionário.")
        total = len(get_questoes(qid))
        corretas = st.session_state.get(f"correct_{qid}", 0)
        respondidas = st.session_state.get(f"answered_{qid}", 0)
        st.metric("Desempenho", f"{corretas}/{total}", help=f"Respondidas: {respondidas} de {total}")
        if st.button("🔁 Recomeçar"):
            for k in list(st.session_state.keys()):
                if k.startswith((f"pool_{qid}", f"answered_{qid}", f"correct_{qid}", "vf_", "mc_", "exp_")):
                    del st.session_state[k]
            st.rerun()
        return

    q = pool[-1]  # topo
    numero_q = len(pool)  # posição reversa == contador humano
    render_questao(q, parent_qid=qid, numero_q=numero_q)

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Pular"):
            pool.pop()
            st.rerun()
    with col2:
        if st.button("Errei"):
            st.session_state[f"answered_{qid}"] += 1
            pool.pop()
            st.rerun()
    with col3:
        if st.button("Acertei"):
            st.session_state[f"answered_{qid}"] += 1
            st.session_state[f"correct_{qid}"] += 1
            pool.pop()
            st.rerun()

    total = len(get_questoes(qid))
    corretas = st.session_state.get(f"correct_{qid}", 0)
    respondidas = st.session_state.get(f"answered_{qid}", 0)
    st.metric("Desempenho (atualizado)", f"{corretas}/{total}", help=f"Respondidas: {respondidas} de {total}")

# =======================
# Main
# =======================

def main():
    st.set_page_config(page_title="Quiz Mongo", page_icon="🧩", layout="wide")
    st.title("🧩 App de Questões (MongoDB) — versão otimizada")

    ok = connection_status()
    if not ok:
        st.stop()

    _init_once()

    paginas = {
        "Importar": page_importar,
        "Gerenciar": page_gerenciar,
        "Praticar": page_praticar,
    }
    escolha = st.sidebar.radio("Navegação", list(paginas.keys()))
    paginas[escolha]()

if __name__ == "__main__":
    main()
