# Streamlit + MongoDB (Template de Deploy via GitHub)

Pronto para **fork** e deploy no **Streamlit Community Cloud** (ou em Heroku/Render/Docker).

## 🚀 Como usar

1. **Fork** este repositório ou clique em **Use this template** no GitHub.
2. Confirme que estes arquivos estão aqui:
   - `streamlit_app.py` — **arquivo principal do app** (pode renomear para `app.py`, se preferir, mas ajuste no deploy).
   - `requirements.txt` — dependências.
   - `Procfile` — *apenas* para plataformas como Heroku/Render (o Streamlit Cloud **não** usa).
   - `.gitignore` — evita subir segredos e lixo.
   - `.streamlit/secrets.example.toml` — **exemplo** de segredos **(não commitar o real)**.

3. (Opcional) Teste localmente:
   ```bash
   python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   export MONGO_URI="mongodb+srv://<usuario>:<senha>@<cluster>/<params>"
   export MONGO_DB_NAME="quiz_app"
   streamlit run streamlit_app.py
   ```

## 🌥️ Deploy no Streamlit Community Cloud (grátis)
1. Entre em https://share.streamlit.io/ (ou https://streamlit.io/cloud) e faça **Sign in** com sua conta GitHub.
2. Clique em **New app**.
3. Selecione seu repositório, *branch* e defina o **main file** como `streamlit_app.py`.
4. Em **Advanced settings** > **Secrets** (ou depois em **Settings** > **Secrets**), cole algo como:
   ```toml
   MONGO_URI = "mongodb+srv://usuario:senha@cluster.mongodb.net/?retryWrites=true&w=majority"
   MONGO_DB_NAME = "quiz_app"
   ```
   > **Importante:** **NÃO** commitar `secrets.toml`. No Streamlit Cloud, os secrets ficam criptografados no painel.
5. Clique em **Deploy**. Uma URL pública será gerada (`https://<seuapp>.streamlit.app`).

### Atualizações
Cada `git push` no branch configurado dispara nova build automaticamente. Se necessário, use **Rerun**/**Restart** no painel.

## 🔐 Boas práticas de credenciais
- Nunca commite `secrets.toml`, `.env` ou senhas no repo público.
- Use variáveis de ambiente/Secrets do provedor (Streamlit Cloud, Render, etc.).
- Em MongoDB Atlas, crie um usuário com privilégios mínimos e **Whitelist** de IPs conforme necessário.

## 🧩 Estrutura de dados (MongoDB)
Este template assume coleções:
- `questionarios` (índice único em `nome`)
- `questoes` (campos: `questionario_id`, `tipo`, `texto`, `explicacao`, alternativas e `correta_text`)
- `respostas` (registra tentativas e acertos)

> O arquivo `streamlit_app.py` já contém as funções para inicialização, métricas, importação CSV e prática/simulados.

## 🧰 Alternativas de deploy
- **Heroku/Render/Fly.io**: usar `Procfile` (`web: streamlit run streamlit_app.py ...`), configurar variáveis de ambiente no painel.
- **Docker**: criar `Dockerfile` e publicar em um serviço de sua escolha.

## ❓ Dúvidas comuns
- **Precisa de Procfile no Streamlit Cloud?** Não.
- **Como setar segredos?** Em **Settings > Secrets** do app (UI do Streamlit Cloud).
- **Repositório privado funciona?** Sim, basta dar permissão ao Streamlit Cloud para acessar o repo.

---
Feito para acelerar seu deploy 📦
