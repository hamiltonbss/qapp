
import os
import sqlite3
import random
import io
import csv
from datetime import datetime
from pathlib import Path

import streamlit as st
import pandas as pd

# =============================
# Config & Globals
# =============================
DB_PATH = os.environ.get("QUIZ_DB_PATH", "quiz_app.db")

st.set_page_config(page_title="Estudos | Questionários & Simulados", layout="wide")

# =============================
# Database helpers
# =============================
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn

def init_db():
    with get_conn() as conn:
        c = conn.cursor()

        # Questionários básicos
        c.execute("""
        CREATE TABLE IF NOT EXISTS questionarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL UNIQUE,
            descricao TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)

        # Questões: tipo VF (V/F) ou MC (múltipla escolha)
        # Armazenamos até 5 alternativas (A-E) para MC. Campos podem ser NULL para VF.
        c.execute("""
        CREATE TABLE IF NOT EXISTS questoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            questionario_id INTEGER NOT NULL,
            tipo TEXT NOT NULL CHECK (tipo IN ('VF','MC')),
            texto TEXT NOT NULL,
            explicacao TEXT,
            correta_text TEXT NOT NULL,             -- 'V'/'F' para VF; 'A'..'E' ou texto exato para MC (guardamos a letra)
            op_a TEXT, op_b TEXT, op_c TEXT, op_d TEXT, op_e TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (questionario_id) REFERENCES questionarios(id) ON DELETE CASCADE
        );
        """)

        # Respostas do usuário (para métricas de desempenho)
        c.execute("""
        CREATE TABLE IF NOT EXISTS respostas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            questionario_id INTEGER NOT NULL,
            questao_id INTEGER NOT NULL,
            correto INTEGER NOT NULL CHECK (correto IN (0,1)),
            respondido_em TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (questionario_id) REFERENCES questionarios(id) ON DELETE CASCADE,
            FOREIGN KEY (questao_id) REFERENCES questoes(id) ON DELETE CASCADE
        );
        """)

        # Índices para desempenho
        c.execute("CREATE INDEX IF NOT EXISTS idx_questoes_questionario ON questoes(questionario_id);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_respostas_q ON respostas(questionario_id);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_respostas_questao ON respostas(questao_id);")

        # Garante existência do questionário "Favoritos"
        c.execute("INSERT OR IGNORE INTO questionarios (nome, descricao) VALUES (?, ?)",
                  ("Favoritos", "Questões salvas como favoritas."))

        conn.commit()

def get_questionarios():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT id, nome, descricao FROM questionarios ORDER BY nome;")
        return c.fetchall()

def get_questionario_by_name(name):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT id, nome FROM questionarios WHERE nome = ?;", (name,))
        return c.fetchone()

def add_questionario(nome, descricao=""):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO questionarios (nome, descricao) VALUES (?, ?);", (nome, descricao))
        conn.commit()

def delete_questionario(qid):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM questionarios WHERE id = ?;", (qid,))
        conn.commit()

def add_questao_vf(questionario_id, texto, correta, explicacao=""):
    correta_norm = normalize_bool(correta)
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO questoes (questionario_id, tipo, texto, explicacao, correta_text)
            VALUES (?, 'VF', ?, ?, ?);
        """, (questionario_id, texto, explicacao, 'V' if correta_norm else 'F'))
        conn.commit()

def add_questao_mc(questionario_id, texto, alternativas, correta_letra, explicacao=""):
    # alternativas: list up to 5 strings (A..E)
    op = alternativas + [None]* (5 - len(alternativas))
    correta_letra = correta_letra.upper().strip()
    if correta_letra not in list("ABCDE")[:len(alternativas)]:
        # tente mapear por igualdade de texto
        idx = None
        for i, alt in enumerate(alternativas):
            if alt is None:
                continue
            if str(alt).strip().lower() == correta_letra.strip().lower():
                idx = i
                break
        if idx is None:
            raise ValueError("Resposta correta inválida para questão MC.")
        correta_letra = "ABCDE"[idx]

    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO questoes (questionario_id, tipo, texto, explicacao, correta_text,
                                  op_a, op_b, op_c, op_d, op_e)
            VALUES (?, 'MC', ?, ?, ?, ?, ?, ?, ?, ?);
        """, (questionario_id, texto, explicacao, correta_letra, op[0], op[1], op[2], op[3], op[4]))
        conn.commit()

def get_questoes(questionario_id):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT * FROM questoes
            WHERE questionario_id = ?
            ORDER BY id;
        """, (questionario_id,))
        return c.fetchall()

def get_random_questoes(questionario_ids, n):
    # retorna até n questões, amostrando aleatoriamente entre questionários escolhidos
    placeholders = ",".join(["?"]*len(questionario_ids))
    with get_conn() as conn:
        c = conn.cursor()
        c.execute(f"""
            SELECT * FROM questoes
            WHERE questionario_id IN ({placeholders})
            ORDER BY RANDOM()
            LIMIT ?;
        """, (*questionario_ids, n))
        return c.fetchall()

def save_resposta(questionario_id, questao_id, correto):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO respostas (questionario_id, questao_id, correto)
            VALUES (?, ?, ?);
        """, (questionario_id, questao_id, 1 if correto else 0))
        conn.commit()

def desempenho_questionario(questionario_id):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN correto = 1 THEN 1 ELSE 0 END) as acertos
            FROM respostas
            WHERE questionario_id = ?;
        """, (questionario_id,))
        row = c.fetchone()
        total = row["total"] or 0
        acertos = row["acertos"] or 0
        perc = (acertos/total)*100 if total > 0 else 0.0
        return total, acertos, perc

def duplicar_questao_para_favoritos(questao_id):
    fav = get_questionario_by_name("Favoritos")
    if not fav:
        add_questionario("Favoritos", "Questões salvas como favoritas.")
        fav = get_questionario_by_name("Favoritos")
    fav_id = fav["id"] if isinstance(fav, sqlite3.Row) else fav[0]

    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM questoes WHERE id = ?;", (questao_id,))
        q = c.fetchone()
        if not q:
            return False

        c.execute("""
            INSERT INTO questoes (questionario_id, tipo, texto, explicacao, correta_text,
                                  op_a, op_b, op_c, op_d, op_e)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (fav_id, q["tipo"], q["texto"], q["explicacao"], q["correta_text"],
              q["op_a"], q["op_b"], q["op_c"], q["op_d"], q["op_e"]))
        conn.commit()
    return True

# =============================
# CSV Import Helpers
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

Exemplos:
tipo,questionario,texto,correta,explicacao,alternativas
VF,Direito Adm,"A licitação é regra e a contratação direta é exceção.",V,"Art. 37, XXI, CF/88 e Lei 14.133/21",
MC,Matemática,"Qual é a derivada de x^2?",B,"d/dx x^2 = 2x","x;2x;x^2;1;0"
MC,TI,"Qual a porta padrão do HTTP?",80,"Padrão histórico","21|22|80|110|443"   # correta por texto
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
    if len(parts) == 0:
        return []
    if len(parts) > 5:
        parts = parts[:5]
    return parts

def ensure_questionario(nome):
    nome = str(nome).strip()
    if not nome:
        nome = "Sem Título"
    q = get_questionario_by_name(nome)
    if q:
        return q["id"] if isinstance(q, sqlite3.Row) else q[0]
    add_questionario(nome, "")
    q = get_questionario_by_name(nome)
    return q["id"] if isinstance(q, sqlite3.Row) else q[0]

def import_csv_to_db(filelike_or_str):
    """
    Aceita:
      - arquivo enviado via st.file_uploader (BytesIO)
      - string de CSV colada
    """
    if hasattr(filelike_or_str, "read"):
        content = filelike_or_str.read()
        try:
            txt = content.decode("utf-8")
        except Exception:
            txt = content.decode("latin-1")
    else:
        txt = str(filelike_or_str)

    # Detectar separador automaticamente com csv.Sniffer
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
    for i, row in enumerate(reader, start=2):  # start=2 por causa do cabeçalho
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
                # correta pode ser letra ou texto
                correta_cand = correta
                # Se for número (porta 80) ou letra, tratamos abaixo em add_questao_mc
                add_questao_mc(qid, texto, alternativas, correta_cand, explicacao)
                ok += 1
            else:
                raise ValueError("tipo deve ser 'VF' ou 'MC'.")
        except Exception as e:
            erros.append(f"Linha {i}: {e}")

    return ok, erros

# =============================
# UI Helpers
# =============================
def metric_card(title, value, help_text=None):
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.markdown(f"### {value}")
        if help_text:
            st.caption(help_text)

def show_desempenho_block(qid):
    total, acertos, perc = desempenho_questionario(qid)
    c1, c2, c3 = st.columns([1,1,3])
    with c1:
        metric_card("Respondidas", total)
    with c2:
        metric_card("Acertos", acertos)
    with c3:
        st.progress(int(perc), text=f"Aproveitamento: {perc:.1f}%")

# =============================
# Páginas (Router)
# =============================
def page_dashboard():
    st.title("📚 Painel de Questionários")
    qs = get_questionarios()
    if not qs:
        st.info("Nenhum questionário cadastrado ainda. Vá em **Importar CSV** para começar.")
        return

    # Busca
    filtro = st.text_input("🔎 Buscar por nome")
    cols = st.columns(3)
    count = 0
    for q in qs:
        if filtro and filtro.lower() not in q["nome"].lower():
            continue
        with cols[count % 3]:
            st.subheader(q["nome"])
            if q["descricao"]:
                st.caption(q["descricao"])
            show_desempenho_block(q["id"])
            b1, b2, b3 = st.columns(3)
            with b1:
                if st.button("Praticar", key=f"pr_{q['id']}"):
                    st.session_state["mode"] = "practice"
                    st.session_state["current_qid"] = q["id"]
                    st.rerun()
            with b2:
                if q["nome"] != "Favoritos" and st.button("Excluir", key=f"del_{q['id']}"):
                    delete_questionario(q["id"])
                    st.success(f"Questionário '{q['nome']}' excluído.")
                    st.rerun()
            with b3:
                if st.button("Ver questões", key=f"ver_{q['id']}"):
                    st.session_state["mode"] = "manage"
                    st.session_state["current_qid"] = q["id"]
                    st.rerun()
        count += 1

def render_questao(q_row, show_favorito=True, record=False, parent_qid=None):
    """
    Exibe questão e retorna (answered, correto) se o usuário responder.
    Se record=True, salva a resposta em 'respostas' usando parent_qid.
    """
    qid = q_row["id"]
    tipo = q_row["tipo"]
    st.markdown(f"#### Q{qid} - {q_row['texto']}")

    correto = None
    answered = False

    if tipo == "VF":
        escolha = st.radio("Sua resposta", ["Verdadeiro", "Falso"], key=f"vf_{qid}")
        if st.button("Responder", key=f"btn_vf_{qid}"):
            gabarito = q_row["correta_text"] == "V"
            user = (escolha == "Verdadeiro")
            correto = (gabarito == user)
            answered = True
    else:
        alternativas = [q_row["op_a"], q_row["op_b"], q_row["op_c"], q_row["op_d"], q_row["op_e"]]
        letras = ["A","B","C","D","E"]
        opts = [(letras[i], alt) for i, alt in enumerate(alternativas) if alt]
        labels = [f"{letra}) {alt}" for letra, alt in opts]
        escolha = st.radio("Escolha uma alternativa", labels, key=f"mc_{qid}")
        if st.button("Responder", key=f"btn_mc_{qid}"):
            try:
                letra_escolhida = escolha.split(")")[0]
            except Exception:
                letra_escolhida = None
            correto = (letra_escolhida == q_row["correta_text"])
            answered = True

    if answered:
        if correto:
            st.success("✅ Correto!")
        else:
            st.error("❌ Incorreto.")
        if q_row["explicacao"]:
            with st.expander("Ver explicação"):
                st.write(q_row["explicacao"])

        if record and parent_qid is not None:
            save_resposta(parent_qid, qid, bool(correto))

    if show_favorito:
        addfav = st.button("⭐ Salvar nos Favoritos", key=f"fav_{qid}")
        if addfav:
            if duplicar_questao_para_favoritos(qid):
                st.toast("Adicionada em 'Favoritos'.")
            else:
                st.warning("Não foi possível adicionar aos favoritos.")

    st.divider()
    return answered, correto

def page_praticar():
    st.title("🎯 Praticar")
    qs = get_questionarios()
    nomes = {q["nome"]: q["id"] for q in qs}
    escolha = st.selectbox("Selecione um questionário", ["(Selecione)"] + list(nomes.keys()),
                           index= list(nomes.keys()).index(next((n for n in nomes if n != "Favoritos"), list(nomes.keys())[0]))+1 if nomes else 0)
    if escolha == "(Selecione)":
        return

    qid = nomes[escolha]
    show_desempenho_block(qid)

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
            st.rerun()
        return

    # Pega a próxima
    next_id = pool.pop()
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM questoes WHERE id = ?;", (next_id,))
        row = c.fetchone()

    answered, correto = render_questao(row, show_favorito=True, record=True, parent_qid=qid)

def page_gerenciar():
    st.title("🧰 Gerenciar Questionário")
    qs = get_questionarios()
    if not qs:
        st.info("Nenhum questionário cadastrado.")
        return
    nomes = {q["nome"]: q["id"] for q in qs}
    escolha = st.selectbox("Selecione um questionário", list(nomes.keys()))
    qid = nomes[escolha]

    show_desempenho_block(qid)
    st.subheader("Questões")
    rows = get_questoes(qid)
    if not rows:
        st.info("Sem questões aqui ainda.")
    for r in rows:
        with st.expander(f"Q{r['id']} • {r['tipo']} • {r['texto'][:70]}"):
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
            if r["explicacao"]:
                st.write("**Explicação:**")
                st.write(r["explicacao"])
            if st.button("⭐ Favoritar", key=f"m_fav_{r['id']}"):
                if duplicar_questao_para_favoritos(r["id"]):
                    st.toast("Adicionada em 'Favoritos'.")

def page_importar():
    st.title("📥 Importar questões via CSV (inclui múltipla escolha em lote)")
    st.markdown("Faça upload de um CSV **com cabeçalho**. Veja o modelo abaixo.")

    with st.expander("📄 Ver modelo de CSV suportado"):
        st.code(TEMPLATE_DOC, language="text")

    up = st.file_uploader("Enviar arquivo CSV", type=["csv"])
    txt = st.text_area("... ou cole aqui o conteúdo do CSV", height=180, placeholder="tipo,questionario,texto,correta,explicacao,alternativas\n...")
    if st.button("Importar"):
        try:
            if up is not None:
                ok, erros = import_csv_to_db(up)
            elif txt.strip():
                ok, erros = import_csv_to_db(txt)
            else:
                st.warning("Envie um arquivo ou cole o conteúdo do CSV.")
                return

            st.success(f"{ok} questões importadas com sucesso.")
            if erros:
                st.warning("Ocorreram alguns erros:")
                for e in erros[:100]:
                    st.write("- ", e)
        except Exception as e:
            st.error(f"Falha na importação: {e}")

def page_simulado():
    st.title("📝 Simulados")
    qs_all = [q for q in get_questionarios() if q["nome"] != "Favoritos"]
    if not qs_all:
        st.info("Crie ou importe questionários primeiro.")
        return
    options = {f"{q['nome']} (id {q['id']})": q["id"] for q in qs_all}
    escolha = st.multiselect("Selecione um ou mais questionários", list(options.keys()))
    qids = [options[k] for k in escolha]

    total_disp = 0
    if qids:
        with get_conn() as conn:
            c = conn.cursor()
            ph = ",".join(["?"]*len(qids))
            c.execute(f"SELECT COUNT(*) FROM questoes WHERE questionario_id IN ({ph});", qids)
            total_disp = c.fetchone()[0]

    n = st.number_input("Número de questões no simulado", min_value=1, value=min(10, max(1,total_disp)), max_value=max(1,total_disp) if total_disp else 1, step=1, disabled=(total_disp==0))

    if st.button("Iniciar Simulado", disabled=(not qids or total_disp == 0)):
        st.session_state["simulado_qids"] = qids
        st.session_state["simulado_pool"] = [dict(r) for r in get_random_questoes(qids, n)]
        st.session_state["simulado_idx"] = 0
        st.session_state["simulado_acertos"] = 0
        st.session_state["mode"] = "run_simulado"
        st.rerun()

def page_run_simulado():
    st.title("🧪 Simulado em andamento")
    pool = st.session_state.get("simulado_pool", [])
    idx = st.session_state.get("simulado_idx", 0)
    acertos = st.session_state.get("simulado_acertos", 0)

    if idx >= len(pool):
        total = len(pool)
        perc = (acertos/total)*100 if total else 0
        st.success(f"Fim do simulado! Acertos: {acertos}/{total} ({perc:.1f}%).")
        if st.button("Voltar aos simulados"):
            st.session_state["mode"] = "simulados"
            st.rerun()
        return

    q = pool[idx]
    st.info(f"Questão {idx+1} de {len(pool)}")
    answered, correto = render_questao(q, show_favorito=True, record=False, parent_qid=None)
    if answered:
        if correto:
            st.session_state["simulado_acertos"] = acertos + 1
        st.session_state["simulado_idx"] = idx + 1
        st.rerun()

# =============================
# Main Navigation
# =============================
def main():
    init_db()

    # Sidebar
    with st.sidebar:
        st.header("Navegação")
        choice = st.radio("Ir para", ["Painel", "Praticar", "Gerenciar", "Importar CSV", "Simulados"])

    st.session_state.setdefault("mode", None)

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
            st.session_state["mode"] = "simulados"
            page_simulado()

if __name__ == "__main__":
    main()
