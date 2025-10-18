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
            # Usa o cliente cacheado
            client = get_mongo_client()
            if client is None:
                st.error("Cliente MongoDB não disponível")
                return False
            # Ping simplificado
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
    """Inicializa índices e questionário Favoritos (executado apenas uma vez)"""
    db = get_db()
    try:
        # Cria índices se não existirem (operação idempotente)
        db.questionarios.create_index([("nome", ASCENDING)], name="uq_nome", unique=True, background=True)
        db.questoes.create_index([("questionario_id", ASCENDING)], background=True)
        db.respostas.create_index([("questionario_id", ASCENDING)], background=True)
        db.respostas.create_index([("questao_id", ASCENDING)], background=True)
        
        # Garante existência do questionário "Favoritos"
        if db.questionarios.count_documents({"nome": "Favoritos"}, limit=1) == 0:
            db.questionarios.insert_one({
                "nome": "Favoritos",
                "descricao": "Questões salvas como favoritas.",
                "created_at": datetime.now(timezone.utc).isoformat()
            })
    except Exception as e:
        # Silencia erros de índice já existente
        pass

def _doc_to_row_q(q):
    """Converte questionário Mongo -> dict (id:str)."""
    return {"id": str(q["_id"]), "nome": q.get("nome",""), "descricao": q.get("descricao","")}

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
        cur = db.questionarios.find({}).sort("nome", ASCENDING)
        return [_doc_to_row_q(x) for x in cur]
    except Exception as e:
        st.error(f"[get_questionarios] erro: {e}")
        return []

def get_questionario_by_name(name):
    db = get_db()
    q = db.questionarios.find_one({"nome": name})
    return _doc_to_row_q(q) if q else None

def add_questionario(nome, descricao=""):
    db = get_db()
    res = db.questionarios.insert_one({
        "nome": nome,
        "descricao": descricao,
        "created_at": datetime.now(timezone.utc).isoformat()
    })
    # Limpa cache
    get_questionarios.clear()
    return str(res.inserted_id)

def delete_questionario(qid):
    db = get_db()
    oid = ObjectId(qid)
    db.questoes.delete_many({"questionario_id": oid})
    db.respostas.delete_many({"questionario_id": oid})
    db.questionarios.delete_one({"_id": oid})
    # Limpa cache
    get_questionarios.clear()

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
    """Retorna: total, corretas (última resposta correta), perc"""
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

def update_questao_explicacao(questao_id, texto_exp):
    db = get_db()
    db.questoes.update_one({"_id": ObjectId(questao_id)}, {"$set": {"explicacao": texto_exp}})

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
- alternativas        -> (apenas MC) string com alternativas separadas por ';' ou '|', na ordem A..E
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
    sep = ";" if ";" in s else "|"
    parts = [p.strip() for p in s.split(sep) if p.strip()]
    if len(parts) > 5:
        parts = parts[:5]
    return parts

def ensure_questionario(nome):
    nome = str(nome).strip() or "Sem Título"
    q = get_questionario_by_name(nome)
    if q: 
        return q["id"]
    return add_questionario(nome, "")

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
            texto = row.get("texto","").strip()
            correta = row.get("correta","").strip()
            explicacao = row.get("explicacao","") or ""

            if not texto:
                raise ValueError("Texto da questão vazio.")

            qid = ensure_questionario(questionario)

            if tipo == "VF":
                val = normalize_bool(correta)
                add_questao_vf(qid, texto, val, explicacao)
                ok += 1
            elif tipo == "MC":
                alternativas = parse_alternativas(row.get("alternativas",""))
                if len(alternativas) < 2:
                    raise ValueError("Questão MC requer ao menos 2 alternativas.")
                add_questao_mc(qid, texto, alternativas, correta, explicacao)
                ok += 1
            else:
                raise ValueError("tipo deve ser 'VF' ou 'MC'.")
        except Exception as e:
            erros.append(f"Linha {i}: {e}")

    # Limpa cache após importação
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

# =============================
# Páginas
# =============================
def render_questao(q_row, parent_qid, questao_numero=None):
    qid = q_row["id"]
    tipo = q_row["tipo"]
    
    # Exibe número da questão se fornecido
    if questao_numero:
        st.markdown(f"#### Questão {questao_numero}")
    
    st.markdown(f"**{q_row['texto']}**")

    answered_key = f"answered_{qid}"
    result_key = f"result_{qid}"

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
    else:
        alternativas = [q_row["op_a"], q_row["op_b"], q_row["op_c"], q_row["op_d"], q_row["op_e"]]
        letras = ["A","B","C","D","E"]
        opts = [(letras[i], alt) for i, alt in enumerate(alternativas) if alt]
        labels = ["— Selecione —"] + [f"{letra}) {alt}" for letra, alt in opts]
        escolha = st.radio("Escolha uma alternativa", labels, key=f"mc_{qid}", index=0)
        if answered_key not in st.session_state and escolha != "— Selecione —":
            letra_escolhida = escolha.split(")")[0]
            is_correct = (letra_escolhida == q_row["correta_text"])
            st.session_state[answered_key] = True
            st.session_state[result_key] = is_correct
            save_resposta(parent_qid, qid, is_correct)

    # Feedback + Explicação
    with st.expander("Ver explicação / editar"):
        exp_key = f"exp_{qid}"
        new_exp = st.text_area("Texto da explicação (salvo no banco):", value=q_row.get("explicacao",""), key=exp_key, height=160)
        if st.button("Salvar explicação", key=f"save_exp_{qid}"):
            update_questao_explicacao(qid, new_exp)
            st.toast("Explicação atualizada.")

    if st.session_state.get(answered_key):
        if st.session_state.get(result_key):
            st.success("✅ Correto!")
        else:
            st.error("❌ Incorreto.")

    # Favoritar
    if st.button("⭐ Salvar nos Favoritos", key=f"fav_{qid}"):
        if duplicar_questao_para_favoritos(qid):
            st.toast("Adicionada em 'Favoritos'.")

    st.divider()

def page_dashboard():
    st.title("📚 Painel de Questionários")
    qs = get_questionarios()
    if not qs:
        st.info("Nenhum questionário cadastrado ainda. Vá em **Importar CSV** para começar.")
        return

    filtro = st.text_input("🔎 Buscar por nome")
    cols = st.columns(2)
    slot = 0
    for q in qs:
        if filtro and filtro.lower() not in q["nome"].lower():
            continue
        with cols[slot % 2]:
            with st.container(border=True):
                st.subheader(q["nome"])
                if q["descricao"]:
                    st.caption(q["descricao"])
                show_desempenho_block(q["id"])
                b1, b2, b3 = st.columns(3)
                with b1:
                    if st.button("Praticar", key=f"pr_{q['id']}"):
                        st.session_state["current_qid"] = q["id"]
                        st.session_state["go_to"] = "Praticar"
                        st.rerun()
                with b2:
                    if q["nome"] != "Favoritos" and st.button("Excluir", key=f"del_{q['id']}"):
                        delete_questionario(q["id"])
                        st.success(f"Questionário '{q['nome']}' excluído.")
                        st.rerun()
                with b3:
                    if st.button("Ver questões", key=f"ver_{q['id']}"):
                        st.session_state["current_qid"] = q["id"]
                        st.session_state["go_to"] = "Gerenciar"
                        st.rerun()
        slot += 1

def page_praticar():
    st.title("🎯 Praticar")
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

    # Cabeçalho de desempenho
    st.subheader("Desempenho")
    show_desempenho_block(qid, show_respondidas=True)

    # Estado de navegação
    key_pool = f"pool_{qid}"
    if key_pool not in st.session_state:
        st.session_state[key_pool] = [r["id"] for r in get_questoes(qid)]
        random.shuffle(st.session_state[key_pool])

    pool = st.session_state[key_pool]
    if not pool:
        st.info("Acabaram as questões! Clique abaixo para reiniciar.")
        if st.button("Reiniciar"):
            st.session_state[key_pool] = [r["id"] for r in get_questoes(qid)]
            random.shuffle(st.session_state[key_pool])
            for k in list(st.session_state.keys()):
                if k.startswith("answered_") or k.startswith("result_"):
                    del st.session_state[k]
            st.rerun()
        return

    # Carrega questão atual
    current_qid = pool[-1]
    row = get_questao_by_id(current_qid)
    
    # Calcula número da questão (total - restantes + 1)
    total_questoes = len(get_questoes(qid))
    questao_numero = total_questoes - len(pool) + 1

    render_questao(row, parent_qid=qid, questao_numero=questao_numero)

    # Mostrar desempenho atualizado
    st.subheader("Desempenho (atualizado)")
    show_desempenho_block(qid, show_respondidas=True)

    # Próxima questão
    if st.button("Próxima questão ▶"):
        pool.pop()
        for k in [f"answered_{current_qid}", f"result_{current_qid}", f"vf_{current_qid}", f"mc_{current_qid}"]:
            if k in st.session_state:
                del st.session_state[k]
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
    txt = st.text_area("... ou cole aqui o conteúdo do CSV", height=180, placeholder="tipo,questionario,texto,correta,explicacao,alternativas\n...")
    
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
    # Verifica conexão apenas uma vez por sessão
    if "db_checked" not in st.session_state:
        ok = connection_status()
        if not ok:
            st.stop()
        st.session_state["db_checked"] = True
        # Inicializa DB apenas uma vez
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
