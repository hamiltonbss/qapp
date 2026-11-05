"""Módulo de Análise Morfossintática — treino com termo destacado.

Cada exercício é uma oração com um ou mais trechos destacados. Para cada destaque o aluno
escolhe, num dropdown, a classificação da camada correspondente (morfologia, sintaxe,
orações ou pontuação) e recebe a correção ao terminar a oração.

Coleções: `sx_exercicios` (compartilhados, como os questionários), `sx_respostas` (por usuário)
e `sx_rotulos` (classificações extras aceitas na importação).
Uso no app principal:
    import sintaxe
    sintaxe.init_indexes(db)
    sintaxe.render_page(db, login, admin=is_admin())
"""
import csv
import html
import io
import random
import re
import unicodedata
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from bson import ObjectId
from pymongo import ASCENDING

# =============================
# Classificações (NGB)
# =============================
CAMADAS = {
    "morfologia": "Classe gramatical da palavra destacada",
    "sintaxe": "Função sintática do termo destacado",
    "oracoes": "Classificação da oração destacada",
    "pontuacao": "Justificativa da vírgula destacada",
}
NOMES_CAMADA = {"morfologia": "Morfologia", "sintaxe": "Sintaxe", "oracoes": "Orações", "pontuacao": "Pontuação"}

ROTULOS = {
    "morfologia": [
        "Substantivo", "Adjetivo", "Artigo", "Numeral", "Pronome pessoal", "Pronome de tratamento",
        "Pronome possessivo", "Pronome demonstrativo", "Pronome indefinido", "Pronome interrogativo",
        "Pronome relativo", "Pronome reflexivo", "Partícula apassivadora", "Índice de indeterminação do sujeito",
        "Partícula expletiva", "Verbo", "Advérbio", "Preposição", "Conjunção coordenativa",
        "Conjunção subordinativa", "Conjunção integrante", "Interjeição", "Palavra denotativa",
    ],
    "sintaxe": [
        "Sujeito simples", "Sujeito composto", "Sujeito oculto", "Sujeito indeterminado",
        "Núcleo do predicado verbal", "Verbo de ligação", "Predicativo do sujeito", "Predicativo do objeto",
        "Objeto direto", "Objeto direto preposicionado", "Objeto direto pleonástico", "Objeto indireto",
        "Objeto indireto pleonástico", "Complemento nominal", "Agente da passiva", "Adjunto adnominal",
        "Adjunto adverbial de tempo", "Adjunto adverbial de lugar", "Adjunto adverbial de modo",
        "Adjunto adverbial de causa", "Adjunto adverbial de intensidade", "Adjunto adverbial de negação",
        "Adjunto adverbial de afirmação", "Adjunto adverbial de dúvida", "Adjunto adverbial de finalidade",
        "Adjunto adverbial de companhia", "Adjunto adverbial de instrumento", "Adjunto adverbial de meio",
        "Adjunto adverbial de assunto", "Adjunto adverbial de concessão", "Adjunto adverbial de condição",
        "Aposto", "Vocativo",
    ],
    "oracoes": [
        "Oração absoluta", "Oração principal", "Coordenada assindética", "Coordenada sindética aditiva",
        "Coordenada sindética adversativa", "Coordenada sindética alternativa",
        "Coordenada sindética conclusiva", "Coordenada sindética explicativa",
        "Subordinada substantiva subjetiva", "Subordinada substantiva objetiva direta",
        "Subordinada substantiva objetiva indireta", "Subordinada substantiva completiva nominal",
        "Subordinada substantiva predicativa", "Subordinada substantiva apositiva",
        "Subordinada adjetiva restritiva", "Subordinada adjetiva explicativa",
        "Subordinada adverbial causal", "Subordinada adverbial concessiva", "Subordinada adverbial condicional",
        "Subordinada adverbial temporal", "Subordinada adverbial final", "Subordinada adverbial consecutiva",
        "Subordinada adverbial comparativa", "Subordinada adverbial conformativa",
        "Subordinada adverbial proporcional",
    ],
    "pontuacao": [
        "Isola adjunto adverbial deslocado", "Isola aposto", "Isola vocativo", "Separa itens de enumeração",
        "Isola oração subordinada adverbial anteposta", "Isola oração adjetiva explicativa",
        "Separa orações coordenadas", "Isola expressão explicativa ou retificativa",
        "Isola conjunção deslocada", "Isola termo intercalado", "Indica elipse do verbo", "Separa local e data",
        "Incorreta: separa sujeito e verbo", "Incorreta: separa verbo e complemento",
        "Incorreta: separa nome e complemento", "Incorreta: isola oração adjetiva restritiva",
        "Incorreta: separa oração principal da substantiva",
    ],
}
COLUNAS = ["grupo", "oracao", "camada", "resposta", "aceitas", "explicacao"]
_MARCA = re.compile(r"\{\{(.+?)\}\}", re.S)

MODELO_IMPORTACAO = """grupo;oracao;camada;resposta;aceitas;explicacao
Termos acessórios;O menino {{de boné}} chegou cedo.;sintaxe;Adjunto adnominal;;Caracteriza o substantivo concreto "menino".
Termos acessórios;O menino de boné chegou {{cedo}}.;sintaxe;Adjunto adverbial de tempo;;Indica circunstância de tempo do verbo.
Termos acessórios;O livro {{que}} li era ótimo.;morfologia;Pronome relativo;;Retoma "livro" e inicia oração adjetiva.
Orações adverbiais;Saímos, {{embora chovesse}}.;oracoes;Subordinada adverbial concessiva;;"Embora" introduz fato contrário que não impede a ação.
Vírgula;No fim da tarde{{,}} os alunos saíram.;pontuacao;Isola adjunto adverbial deslocado;;Adjunto adverbial anteposto pode ser isolado por vírgula.
Vírgula;Os alunos da turma B{{,}} saíram cedo.;pontuacao;Incorreta: separa sujeito e verbo;;Não se separa o sujeito do verbo por vírgula."""


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


def normalizar(s):
    """Compara rótulos sem diferenciar maiúsculas, acentos e espaços."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def normalizar_camada(c):
    n = normalizar(c).replace(" ", "")
    return {"morfologia": "morfologia", "morfologica": "morfologia", "classe": "morfologia",
            "sintaxe": "sintaxe", "sintatica": "sintaxe", "funcao": "sintaxe",
            "oracoes": "oracoes", "oracao": "oracoes", "periodo": "oracoes",
            "pontuacao": "pontuacao", "virgula": "pontuacao"}.get(n)


def _bolinha(n):
    return chr(0x2460 + n) if 0 <= n < 20 else f"({n + 1})"


def init_indexes(db):
    for fn in (
        lambda: db.sx_exercicios.create_index([("grupo", ASCENDING), ("texto", ASCENDING)], name="uq_sx_ex", unique=True),
        lambda: db.sx_respostas.create_index([("usuario_login", ASCENDING), ("exercicio_id", ASCENDING),
                                              ("item_id", ASCENDING), ("em", ASCENDING)], name="idx_sx_resp"),
        lambda: db.sx_rotulos.create_index([("camada", ASCENDING), ("rotulo", ASCENDING)], name="uq_sx_rot", unique=True),
    ):
        try:
            fn()
        except Exception as e:
            print(f"[sintaxe] {e}")


def rotulos(db, camada):
    """Lista do dropdown: classificações padrão + extras importadas."""
    extras = [d["rotulo"] for d in db.sx_rotulos.find({"camada": camada}).sort("rotulo", ASCENDING)]
    base = ROTULOS.get(camada, [])
    vistos = {normalizar(x) for x in base}
    return base + [x for x in extras if normalizar(x) not in vistos]


def _canonico(db, camada, valor, cache):
    """Rótulo oficial equivalente a 'valor' (ignora acento/caixa) ou None."""
    if camada not in cache:
        cache[camada] = {normalizar(r): r for r in rotulos(db, camada)}
    return cache[camada].get(normalizar(valor))


def acertou(item, resposta):
    if not resposta:
        return False
    validas = {normalizar(item["resposta"])} | {normalizar(a) for a in item.get("aceitas") or []}
    return normalizar(resposta) in validas


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


def interpretar_csv(db, txt, aceitar_novos=False):
    """Lê o CSV (uma linha por destaque) e agrupa as linhas da mesma oração.
    Retorna (exercicios, erros, rotulos_novos) — exercicios: [{grupo, texto, itens}]."""
    txt = _decodificar(txt)
    amostra = txt[:2000]
    delim = ";" if amostra.count(";") >= amostra.count("\t") else "\t"
    rows = [r for r in csv.reader(io.StringIO(txt), delimiter=delim) if any((c or "").strip() for c in r)]
    if not rows:
        return [], ["Nenhuma linha encontrada."], set()
    cab = [normalizar(c) for c in rows[0]]
    if "oracao" in cab and "resposta" in cab:
        idx = {c: cab.index(c) for c in COLUNAS if c in cab}
        dados, inicio = rows[1:], 2
    else:
        idx = {c: i for i, c in enumerate(COLUNAS)}
        dados, inicio = rows, 1

    cache, novos = {}, set()
    exercicios, erros = {}, []
    for n, r in enumerate(dados, start=inicio):
        def campo(c):
            i = idx.get(c)
            return r[i].strip() if i is not None and i < len(r) else ""

        grupo, oracao = campo("grupo") or "Geral", campo("oracao")
        camada = normalizar_camada(campo("camada"))
        marcas = list(_MARCA.finditer(oracao))
        if not oracao:
            erros.append(f"Linha {n}: oração vazia.")
            continue
        if len(marcas) != 1:
            erros.append(f"Linha {n}: marque exatamente um trecho com {{{{ }}}} (encontrei {len(marcas)}).")
            continue
        if not camada:
            erros.append(f"Linha {n}: camada '{campo('camada')}' inválida (use morfologia, sintaxe, oracoes ou pontuacao).")
            continue

        def validar(valor):
            if not valor:
                return None
            ok = _canonico(db, camada, valor, cache)
            if ok:
                return ok
            if aceitar_novos:
                novos.add((camada, valor.strip()))
                return valor.strip()
            raise ValueError(f"'{valor}' não está na lista de {NOMES_CAMADA[camada].lower()}")

        try:
            resposta = validar(campo("resposta"))
            if not resposta:
                raise ValueError("resposta vazia")
            aceitas = [a for a in (validar(x) for x in campo("aceitas").split("|")) if a and a != resposta]
        except ValueError as e:
            erros.append(f"Linha {n}: {e}.")
            continue

        m = marcas[0]
        texto = oracao[:m.start()] + m.group(1) + oracao[m.end():]
        ini, fim = m.start(), m.start() + len(m.group(1))
        ex = exercicios.setdefault((grupo, texto), {"grupo": grupo, "texto": texto, "itens": []})
        item_id = f"{camada}:{ini}-{fim}"
        if any(i["id"] == item_id for i in ex["itens"]):
            erros.append(f"Linha {n}: destaque repetido na mesma oração.")
            continue
        ex["itens"].append({"id": item_id, "camada": camada, "inicio": ini, "fim": fim, "trecho": m.group(1),
                            "resposta": resposta, "aceitas": aceitas,
                            "explicacao": campo("explicacao").replace("\\n", "\n")})
    out = list(exercicios.values())
    for ex in out:
        ex["itens"].sort(key=lambda i: (i["inicio"], -i["fim"]))
    return out, erros, novos


def importar(db, exercicios, novos_rotulos, login):
    """Grava os exercícios; numa oração que já existe no grupo, acrescenta só os destaques novos.
    Retorna (orações novas, destaques novos)."""
    for camada, rotulo in novos_rotulos:
        db.sx_rotulos.update_one({"camada": camada, "rotulo": rotulo},
                                 {"$setOnInsert": {"criado_por": login, "criado_em": _agora()}}, upsert=True)
    n_ex = n_it = 0
    for ex in exercicios:
        atual = db.sx_exercicios.find_one({"grupo": ex["grupo"], "texto": ex["texto"]})
        if atual:
            ja = {i["id"] for i in atual.get("itens") or []}
            novos = [i for i in ex["itens"] if i["id"] not in ja]
            if novos:
                itens = sorted((atual.get("itens") or []) + novos, key=lambda i: (i["inicio"], -i["fim"]))
                db.sx_exercicios.update_one({"_id": atual["_id"]}, {"$set": {"itens": itens}})
                n_it += len(novos)
        else:
            db.sx_exercicios.insert_one({**ex, "criado_por": login, "criado_em": _agora()})
            n_ex += 1
            n_it += len(ex["itens"])
    return n_ex, n_it


def prompt_llm(grupo, camadas, quantidade, extras_por_camada):
    listas = "\n".join(f"  {c}: " + ", ".join(extras_por_camada[c]) for c in camadas)
    nomes_camadas = ", ".join(camadas)
    return f"""Você vai criar exercícios de análise morfossintática para concurso.

FORMATO (obrigatório):
- Responda SOMENTE com um bloco CSV, sem texto antes ou depois.
- Primeira linha exatamente: grupo;oracao;camada;resposta;aceitas;explicacao
- Uma linha por trecho destacado. Marque o trecho entre {{{{ e }}}} (para pontuação, marque só a vírgula: {{{{,}}}}).
- Cada linha tem exatamente UM trecho marcado. Para vários destaques na mesma oração, repita a oração em linhas diferentes, mudando só o trecho marcado.
- grupo: "{grupo}" em todas as linhas.
- camada: {nomes_camadas}.
- resposta: use EXATAMENTE um nome destas listas:
{listas}
- aceitas: outras respostas também corretas, separadas por | (vazio se não houver).
- explicacao: uma frase curta justificando a resposta.
- Não use ";" dentro dos campos.

CONTEÚDO:
- Gere {quantidade} orações com 1 a 3 destaques cada, em estilo de prova de concurso.
- Inclua casos que costumam confundir: complemento nominal × adjunto adnominal, objeto indireto × complemento nominal, adjetiva restritiva × explicativa, vírgulas proibidas.
- Se uma classificação for discutível entre gramáticos, use a coluna "aceitas" e explique."""


# =============================
# Consultas
# =============================
def resumo_grupos(db, login):
    """Grupos com nº de orações, destaques, camadas e seu aproveitamento (última tentativa)."""
    grupos = {}
    for ex in db.sx_exercicios.find({}, {"grupo": 1, "itens.camada": 1}):
        g = grupos.setdefault(ex["grupo"], {"oracoes": 0, "itens": 0, "camadas": set()})
        g["oracoes"] += 1
        g["itens"] += len(ex.get("itens") or [])
        g["camadas"] |= {i["camada"] for i in ex.get("itens") or []}
    ultimas = ultimas_respostas(db, login)
    acc = {}
    for (_, _), r in ultimas.items():
        a = acc.setdefault(r["grupo"], [0, 0])
        a[0] += 1 if r["acertou"] else 0
        a[1] += 1
    for g, v in grupos.items():
        a = acc.get(g, [0, 0])
        v["feitos"] = a[1]
        v["perc"] = a[0] / a[1] * 100 if a[1] else None
    return grupos


def ultimas_respostas(db, login, filtro=None):
    """{(exercicio_id, item_id): última resposta do usuário}."""
    pipe = [{"$match": {"usuario_login": login, **(filtro or {})}}, {"$sort": {"em": 1}},
            {"$group": {"_id": {"e": "$exercicio_id", "i": "$item_id"}, "acertou": {"$last": "$acertou"},
                        "grupo": {"$last": "$grupo"}}}]
    return {(str(d["_id"]["e"]), d["_id"]["i"]): {"acertou": d["acertou"], "grupo": d["grupo"]}
            for d in db.sx_respostas.aggregate(pipe)}


def montar_fila(db, login, grupos, camadas, filtro, ordem):
    exs = list(db.sx_exercicios.find({"grupo": {"$in": list(grupos)}}).sort([("grupo", ASCENDING), ("criado_em", ASCENDING)]))
    exs = [e for e in exs if any(i["camada"] in camadas for i in e.get("itens") or [])]
    if filtro != "Todas":
        ult = ultimas_respostas(db, login, {"grupo": {"$in": list(grupos)}})
        sel = []
        for e in exs:
            chaves = [(str(e["_id"]), i["id"]) for i in e["itens"] if i["camada"] in camadas]
            feitos = [ult[k] for k in chaves if k in ult]
            if filtro == "Não feitas" and not feitos:
                sel.append(e)
            elif filtro == "Caderno de erros" and any(not f["acertou"] for f in feitos):
                sel.append(e)
        exs = sel
    if ordem == "Embaralhar":
        random.shuffle(exs)
    return [str(e["_id"]) for e in exs]


# =============================
# Renderização da oração
# =============================
def _css():
    st.markdown("""<style>
    .sx-oracao{font-size:20px;line-height:2.1;padding:16px 20px;border-radius:12px;background:#fafbfc;
      border:1px solid #e3e7ea;margin:4px 0 12px 0;color:#1a1a1a}
    .sx-hl{padding:2px 3px;border-radius:5px;background:#fff3c4;border-bottom:2px solid #e0a800}
    .sx-hl.oracoes{background:none;border-bottom:3px double #6b46c1}
    .sx-hl.pontuacao{background:#ffd6d6;border-bottom:2px solid #c0392b;font-weight:800;padding:0 4px}
    .sx-hl sup{font-size:12px;font-weight:700;color:#8a6d00;margin-left:1px}
    .sx-hl.oracoes sup{color:#6b46c1}
    .sx-hl.pontuacao sup{color:#c0392b}
    .sx-ok{color:#1e7e34}.sx-bad{color:#c0392b}
    .sx-exp{font-size:13px;color:#555;margin:-6px 0 10px 0}
    </style>""", unsafe_allow_html=True)


def oracao_html(texto, itens):
    """Oração com os destaques numerados. Destaques aninhados são suportados;
    sobreposições parciais ficam sem marca na frase (o trecho aparece no rótulo do dropdown)."""
    numerados = sorted(enumerate(itens), key=lambda x: (x[1]["inicio"], -x[1]["fim"]))
    aceitos, abertos = [], []
    for n, it in numerados:
        while abertos and abertos[-1]["fim"] <= it["inicio"]:
            abertos.pop()
        if abertos and it["fim"] > abertos[-1]["fim"]:
            continue  # sobreposição parcial
        aceitos.append((n, it))
        abertos.append(it)

    def montar(a, b, filhos):
        out, pos = [], a
        i = 0
        while i < len(filhos):
            n, it = filhos[i]
            netos = []
            j = i + 1
            while j < len(filhos) and filhos[j][1]["fim"] <= it["fim"]:
                netos.append(filhos[j])
                j += 1
            out.append(_esc(texto[pos:it["inicio"]]))
            out.append(f"<span class='sx-hl {it['camada']}'>{montar(it['inicio'], it['fim'], netos)}"
                       f"<sup>{_bolinha(n)}</sup></span>")
            pos = it["fim"]
            i = j
        out.append(_esc(texto[pos:b]))
        return "".join(out)

    return f"<div class='sx-oracao'>{montar(0, len(texto), aceitos)}</div>"


# =============================
# Página
# =============================
def render_page(db, login, admin=False):
    _css()
    st.title("🧩 Análise morfossintática")
    msg = st.session_state.pop("_sx_msg", None)
    if msg:
        st.success(msg)

    if st.session_state.get("sx_fila") is not None:
        _sessao(db, login)
        return

    t1, t2, t3, t4 = st.tabs(["✍️ Treinar", "📊 Desempenho", "📥 Importar", "🗂️ Grupos"])
    with t1:
        _aba_treinar(db, login)
    with t2:
        _aba_desempenho(db, login)
    with t3:
        _aba_importar(db, login)
    with t4:
        _aba_grupos(db, login, admin)


def _aba_treinar(db, login):
    grupos = resumo_grupos(db, login)
    if not grupos:
        st.info("Nenhum exercício ainda. Use a aba **Importar** (há um prompt pronto para gerar com uma LLM).")
        return
    st.dataframe([{"Grupo": g, "Orações": v["oracoes"], "Destaques": v["itens"],
                   "Camadas": ", ".join(NOMES_CAMADA[c] for c in CAMADAS if c in v["camadas"]),
                   "Destaques feitos": v["feitos"],
                   "Aproveitamento (%)": round(v["perc"], 1) if v["perc"] is not None else None}
                  for g, v in sorted(grupos.items())], width="stretch", hide_index=True)

    sel = st.multiselect("Grupos", sorted(grupos), default=sorted(grupos)[:1])
    disponiveis = [c for c in CAMADAS if any(c in grupos[g]["camadas"] for g in sel)]
    camadas = st.multiselect("Camadas", disponiveis, default=disponiveis, format_func=NOMES_CAMADA.get)
    c1, c2 = st.columns(2)
    filtro = c1.radio("Quais orações", ["Todas", "Não feitas", "Caderno de erros"], horizontal=True,
                      key="sx_filtro", help="Caderno de erros: orações com algum destaque errado na sua última tentativa.")
    ordem = c2.radio("Ordem", ["Embaralhar", "Na ordem do grupo"], horizontal=True, key="sx_ordem")
    limite = st.number_input("Máximo de orações na sessão (0 = todas)", min_value=0, value=10, step=5, key="sx_lim")

    if st.button("▶ Começar", type="primary", disabled=not (sel and camadas)):
        fila = montar_fila(db, login, sel, camadas, filtro, ordem)
        if limite:
            fila = fila[:int(limite)]
        if not fila:
            st.success("🎉 Nenhuma oração com esses filtros.")
            return
        _iniciar_sessao(fila, camadas)
        st.rerun()


def _iniciar_sessao(fila, camadas):
    st.session_state["sx_fila"] = fila
    st.session_state["sx_camadas"] = list(camadas)
    st.session_state["sx_pos"] = 0
    st.session_state["sx_corr"] = {}
    st.session_state["sx_nonce"] = random.randint(0, 10 ** 9)


def _encerrar():
    for k in ("sx_fila", "sx_camadas", "sx_pos", "sx_corr", "sx_nonce"):
        st.session_state.pop(k, None)


def _sessao(db, login):
    fila, pos = st.session_state["sx_fila"], st.session_state.get("sx_pos", 0)
    camadas = st.session_state.get("sx_camadas") or list(CAMADAS)
    corr = st.session_state.setdefault("sx_corr", {})
    if pos >= len(fila):
        _resultado_sessao(db, login)
        return

    ex = db.sx_exercicios.find_one({"_id": _oid(fila[pos])})
    if not ex:
        st.session_state["sx_pos"] = pos + 1
        st.rerun()
    itens = [i for i in ex.get("itens") or [] if i["camada"] in camadas]

    top1, top2 = st.columns([4, 1])
    top1.progress(pos / len(fila), text=f"Oração {pos + 1} de {len(fila)} · {ex['grupo']}")
    if top2.button("⏹ Encerrar", key="sx_parar", width="stretch"):
        st.session_state["sx_pos"] = len(fila)  # vai para o resultado
        st.rerun()

    st.markdown(oracao_html(ex["texto"], itens), unsafe_allow_html=True)

    corrigido = corr.get(pos)
    nonce = st.session_state.get("sx_nonce", 0)
    escolhas = {}
    for n, it in enumerate(itens):
        opcoes = rotulos(db, it["camada"])
        if it["resposta"] not in opcoes:
            opcoes = opcoes + [it["resposta"]]
        rotulo = f"{_bolinha(n)} **{it['trecho'].strip()}** — {CAMADAS[it['camada']]}"
        escolhas[it["id"]] = st.selectbox(rotulo, opcoes, index=None, placeholder="Escolha a classificação…",
                                          key=f"sx_{nonce}_{pos}_{it['id']}", disabled=corrigido is not None)
        if corrigido is not None:
            r = corrigido[it["id"]]
            if r["acertou"]:
                st.markdown(f"<div class='sx-exp'><span class='sx-ok'>✅ Correto</span>"
                            + (f" · também aceito: {_esc(', '.join(it['aceitas']))}" if it.get("aceitas") else "")
                            + "</div>", unsafe_allow_html=True)
            else:
                st.markdown(f"<div class='sx-exp'><span class='sx-bad'>❌ {_esc(r['resposta'] or 'Em branco')}</span>"
                            f" → correto: <b>{_esc(it['resposta'])}</b>"
                            + (f" (ou {_esc(', '.join(it['aceitas']))})" if it.get("aceitas") else "")
                            + "</div>", unsafe_allow_html=True)
            if it.get("explicacao"):
                st.caption(f"💡 {it['explicacao']}")

    if corrigido is None:
        faltam = sum(1 for v in escolhas.values() if not v)
        if faltam:
            st.caption(f"{faltam} destaque(s) sem resposta — contam como erro se você corrigir assim.")
        if st.button("✔ Corrigir oração", type="primary", key=f"sx_corrigir_{nonce}_{pos}"):
            res = {}
            agora = _agora()
            docs = []
            for it in itens:
                resp = escolhas.get(it["id"])
                ok = acertou(it, resp)
                res[it["id"]] = {"resposta": resp, "acertou": ok, "correta": it["resposta"], "camada": it["camada"]}
                docs.append({"usuario_login": login, "exercicio_id": ex["_id"], "item_id": it["id"],
                             "grupo": ex["grupo"], "camada": it["camada"], "resposta": resp,
                             "correta": it["resposta"], "acertou": ok, "em": agora})
            if docs:
                db.sx_respostas.insert_many(docs)
            corr[pos] = res
            st.rerun()
    else:
        acertos = sum(1 for r in corrigido.values() if r["acertou"])
        (st.success if acertos == len(corrigido) else st.info)(f"{acertos} de {len(corrigido)} destaques corretos.")
        ultimo = pos >= len(fila) - 1
        if st.button("Ver resultado" if ultimo else "Próxima oração ▶", type="primary", key=f"sx_prox_{nonce}_{pos}"):
            st.session_state["sx_pos"] = pos + 1
            st.rerun()


def _resultado_sessao(db, login):
    corr = st.session_state.get("sx_corr") or {}
    fila = st.session_state.get("sx_fila") or []
    linhas = [r for res in corr.values() for r in res.values()]
    st.subheader("Resultado da sessão")
    if not linhas:
        st.info("Nenhuma oração corrigida nesta sessão.")
    else:
        ok = sum(1 for r in linhas if r["acertou"])
        m1, m2, m3 = st.columns(3)
        m1.metric("Orações corrigidas", len(corr))
        m2.metric("Destaques corretos", f"{ok} de {len(linhas)}")
        m3.metric("Aproveitamento", f"{ok / len(linhas) * 100:.1f}%")
        st.dataframe(_tabela_camadas(linhas), width="stretch", hide_index=True)
        conf = _confusoes(linhas)
        if conf:
            st.markdown("**Confusões desta sessão**")
            st.dataframe(conf, width="stretch", hide_index=True)

    erradas = [fila[p] for p, res in corr.items() if any(not r["acertou"] for r in res.values())]
    c1, c2, c3 = st.columns(3)
    if c1.button("🎯 Refazer só as erradas", disabled=not erradas, type="primary", width="stretch"):
        _iniciar_sessao(erradas, st.session_state.get("sx_camadas") or list(CAMADAS))
        st.rerun()
    if c2.button("🔁 Refazer a sessão", width="stretch", disabled=not fila):
        _iniciar_sessao(list(fila), st.session_state.get("sx_camadas") or list(CAMADAS))
        st.rerun()
    if c3.button("Nova sessão", width="stretch"):
        _encerrar()
        st.rerun()


def _tabela_camadas(linhas):
    agg = {}
    for r in linhas:
        a = agg.setdefault(r["camada"], [0, 0])
        a[0] += 1 if r["acertou"] else 0
        a[1] += 1
    return [{"Camada": NOMES_CAMADA.get(c, c), "Destaques": a[1], "Corretos": a[0],
             "Aproveitamento (%)": round(a[0] / a[1] * 100, 1)} for c, a in agg.items()]


def _confusoes(linhas, limite=15):
    cont = {}
    for r in linhas:
        if not r["acertou"]:
            k = (NOMES_CAMADA.get(r["camada"], r["camada"]), r.get("resposta") or "(em branco)", r["correta"])
            cont[k] = cont.get(k, 0) + 1
    return [{"Camada": k[0], "Você marcou": k[1], "Era": k[2], "Vezes": v}
            for k, v in sorted(cont.items(), key=lambda kv: -kv[1])[:limite]]


# ---- Desempenho geral ----
def _aba_desempenho(db, login):
    docs = list(db.sx_respostas.find({"usuario_login": login},
                                     {"camada": 1, "resposta": 1, "correta": 1, "acertou": 1, "grupo": 1}))
    if not docs:
        st.info("Faça uma sessão de treino para ver seu desempenho.")
        return
    ok = sum(1 for d in docs if d["acertou"])
    m1, m2, m3 = st.columns(3)
    m1.metric("Destaques respondidos", len(docs))
    m2.metric("Corretos", ok)
    m3.metric("Aproveitamento geral", f"{ok / len(docs) * 100:.1f}%")

    st.markdown("#### Por camada")
    st.dataframe(_tabela_camadas(docs), width="stretch", hide_index=True)

    st.markdown("#### Por grupo")
    agg = {}
    for d in docs:
        a = agg.setdefault(d.get("grupo") or "—", [0, 0])
        a[0] += 1 if d["acertou"] else 0
        a[1] += 1
    st.dataframe(sorted(({"Grupo": g, "Respostas": a[1], "Aproveitamento (%)": round(a[0] / a[1] * 100, 1)}
                         for g, a in agg.items()), key=lambda r: r["Aproveitamento (%)"]),
                 width="stretch", hide_index=True)

    st.markdown("#### Por classificação (a resposta certa era…)")
    por = {}
    for d in docs:
        a = por.setdefault((d["camada"], d["correta"]), [0, 0])
        a[0] += 1 if d["acertou"] else 0
        a[1] += 1
    tabela = sorted(({"Camada": NOMES_CAMADA.get(c, c), "Classificação": r, "Vezes": a[1],
                      "Acerto (%)": round(a[0] / a[1] * 100, 1)} for (c, r), a in por.items()),
                    key=lambda x: (x["Acerto (%)"], -x["Vezes"]))
    st.dataframe(tabela, width="stretch", hide_index=True,
                 column_config={"Acerto (%)": st.column_config.ProgressColumn(
                     "Acerto", format="%.1f%%", min_value=0, max_value=100)})

    conf = _confusoes(docs, 20)
    if conf:
        st.markdown("#### 🔀 Mapa de confusões")
        st.caption("O que você marcou × o que era. As primeiras linhas são as distinções a revisar.")
        st.dataframe(conf, width="stretch", hide_index=True)

    fracas = [(c, r) for (c, r), a in por.items() if a[1] >= 3 and a[0] / a[1] < 0.7]
    if fracas:
        st.markdown("#### 🎯 Treinar meus pontos fracos")
        st.caption("Classificações com menos de 70% de acerto (mínimo de 3 respostas): "
                   + "; ".join(f"{r}" for _, r in fracas[:8]))
        if st.button("▶ Sessão com as classificações em que mais erro", type="primary", key="sx_fracos"):
            alvo = {(c, normalizar(r)) for c, r in fracas}
            exs = [e for e in db.sx_exercicios.find({}, {"itens": 1})
                   if any((i["camada"], normalizar(i["resposta"])) in alvo for i in e.get("itens") or [])]
            random.shuffle(exs)
            if exs:
                _iniciar_sessao([str(e["_id"]) for e in exs[:15]], list(CAMADAS))
                st.rerun()


# ---- Importar ----
def _aba_importar(db, login):
    st.markdown("Uma linha por **trecho destacado**; marque o trecho com `{{ }}`. "
                "Linhas com a mesma oração viram um só exercício, com vários destaques.")
    with st.expander("📄 Modelo de CSV"):
        st.code(MODELO_IMPORTACAO, language="text")
        st.caption("Colunas: grupo;oracao;camada;resposta;aceitas;explicacao. "
                   "camada = morfologia, sintaxe, oracoes ou pontuacao. "
                   "aceitas = outras respostas corretas separadas por |.")
    with st.expander("📚 Classificações aceitas (as opções do dropdown)"):
        for c in CAMADAS:
            st.markdown(f"**{NOMES_CAMADA[c]}:** " + ", ".join(rotulos(db, c)))

    with st.expander("🤖 Gerar prompt para a LLM"):
        g1, g2, g3 = st.columns([2, 2, 1])
        grupo = g1.text_input("Grupo", value="Termos acessórios", key="sx_pr_grupo")
        cams = g2.multiselect("Camadas", list(CAMADAS), default=["sintaxe"], format_func=NOMES_CAMADA.get,
                              key="sx_pr_cams")
        qtd = g3.number_input("Orações", min_value=1, max_value=100, value=15, key="sx_pr_qtd")
        if cams:
            st.code(prompt_llm(grupo, cams, int(qtd), {c: rotulos(db, c) for c in cams}), language="text")
            st.caption("Copie (ícone no canto do bloco), cole na LLM junto com o conteúdo de estudo, "
                       "revise o CSV e importe abaixo.")

    up = st.file_uploader("Arquivo CSV", type=["csv", "txt"], key="sx_up")
    colado = st.text_area("... ou cole aqui", height=160, key="sx_colar",
                          placeholder="grupo;oracao;camada;resposta;aceitas;explicacao")
    conteudo = up.getvalue() if up is not None else colado
    if not (conteudo and _decodificar(conteudo).strip()):
        return
    aceitar = st.checkbox("Aceitar classificações fora da lista (elas passam a aparecer no dropdown)",
                          key="sx_aceitar_novos")
    exercicios, erros, novos = interpretar_csv(db, conteudo, aceitar)
    n_itens = sum(len(e["itens"]) for e in exercicios)
    st.caption(f"Prévia: {len(exercicios)} oração(ões), {n_itens} destaque(s)"
               + (f" · {len(erros)} linha(s) com problema" if erros else ""))
    if exercicios:
        st.markdown("".join(oracao_html(e["texto"], e["itens"]) for e in exercicios[:5]), unsafe_allow_html=True)
        st.dataframe([{"Grupo": e["grupo"], "Oração": e["texto"], "Destaque": i["trecho"],
                       "Camada": NOMES_CAMADA[i["camada"]], "Resposta": i["resposta"],
                       "Aceitas": " | ".join(i["aceitas"])} for e in exercicios[:30] for i in e["itens"]],
                     width="stretch", hide_index=True)
    if novos:
        st.info("Classificações novas que serão adicionadas: " + "; ".join(f"{r} ({NOMES_CAMADA[c]})" for c, r in sorted(novos)))
    if erros:
        with st.expander(f"⚠️ {len(erros)} linha(s) ignoradas", expanded=not exercicios):
            for e in erros[:100]:
                st.write("- ", e)
            if not aceitar and any("não está na lista" in e for e in erros):
                st.caption("Se a classificação estiver certa e só faltar na lista, marque a opção acima.")
    if st.button("📥 Importar exercícios", type="primary", disabled=not exercicios, key="sx_imp_btn"):
        n_ex, n_it = importar(db, exercicios, novos, login)
        st.session_state["_sx_msg"] = f"Importados: {n_ex} oração(ões) nova(s), {n_it} destaque(s)."
        st.rerun()


# ---- Grupos ----
def _aba_grupos(db, login, admin):
    grupos = sorted(db.sx_exercicios.distinct("grupo"))
    if not grupos:
        st.caption("Nenhum grupo ainda.")
        return
    g = st.selectbox("Grupo", grupos)
    exs = list(db.sx_exercicios.find({"grupo": g}).sort("criado_em", ASCENDING))
    st.caption(f"{len(exs)} oração(ões) · {sum(len(e.get('itens') or []) for e in exs)} destaque(s)")
    for e in exs:
        with st.container(border=True):
            st.markdown(oracao_html(e["texto"], e.get("itens") or []), unsafe_allow_html=True)
            st.caption(" · ".join(f"{_bolinha(n)} {NOMES_CAMADA[i['camada']]}: {i['resposta']}"
                                  for n, i in enumerate(e.get("itens") or [])))
            if admin and st.button("🗑️ Excluir oração", key=f"sx_del_{e['_id']}"):
                db.sx_exercicios.delete_one({"_id": e["_id"]})
                db.sx_respostas.delete_many({"exercicio_id": e["_id"]})
                st.rerun()

    if not admin:
        st.caption("Renomear e excluir grupos é restrito ao administrador.")
        return
    st.divider()
    r1, r2 = st.columns([3, 1])
    novo = r1.text_input("Renomear grupo", value=g, key=f"sx_ren_{g}")
    if r2.button("Salvar", key=f"sx_ren_btn_{g}") and novo.strip() and novo.strip() != g:
        db.sx_exercicios.update_many({"grupo": g}, {"$set": {"grupo": novo.strip()}})
        db.sx_respostas.update_many({"grupo": g}, {"$set": {"grupo": novo.strip()}})
        st.session_state["_sx_msg"] = "Grupo renomeado."
        st.rerun()
    flag = f"_sx_conf_del_{g}"
    if st.session_state.get(flag):
        st.warning(f"Excluir o grupo '{g}', com todas as orações e respostas?")
        if st.button("Sim, excluir", type="primary", key=f"sx_del_g_ok_{g}"):
            ids = [e["_id"] for e in exs]
            db.sx_exercicios.delete_many({"_id": {"$in": ids}})
            db.sx_respostas.delete_many({"exercicio_id": {"$in": ids}})
            st.session_state.pop(flag, None)
            st.session_state["_sx_msg"] = "Grupo excluído."
            st.rerun()
        if st.button("Cancelar", key=f"sx_del_g_no_{g}"):
            st.session_state.pop(flag, None)
            st.rerun()
    elif st.button("🗑️ Excluir grupo", key=f"sx_del_g_{g}"):
        st.session_state[flag] = True
        st.rerun()
