import streamlit as st
import google.generativeai as genai
import os
import sqlite3
import pandas as pd
from passlib.hash import pbkdf2_sha256
import fitz  # PyMuPDF
import base64
import re
import time

# --- 1. CONFIGURAÇÕES ---
st.set_page_config(page_title="Analista IA", page_icon="📊", layout="wide")

# COLE SUA CHAVE DO GOOGLE AQUI
CHAVE_GOOGLE = st.secrets["GOOGLE_API_KEY"]
genai.configure(api_key=CHAVE_GOOGLE)

# Mantemos o modelo que funcionou para você
model = genai.GenerativeModel("gemini-flash-latest")

PASTA_PDFS = os.path.join(os.getcwd(), "PDFs")
if not os.path.exists(PASTA_PDFS):
    os.makedirs(PASTA_PDFS)


# --- 2. BANCO DE DADOS (Inalterado) ---
def conectar_db():
    conn = sqlite3.connect("usuarios.db")
    return conn


def inicializar_banco():
    conn = conectar_db()
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS usuarios 
                 (username TEXT PRIMARY KEY, password TEXT, role TEXT)"""
    )
    c.execute("SELECT * FROM usuarios WHERE username='admin'")
    if not c.fetchone():
        senha_hash = pbkdf2_sha256.hash("admin123")
        c.execute(
            "INSERT INTO usuarios VALUES (?, ?, ?)", ("admin", senha_hash, "admin")
        )
        conn.commit()
    conn.close()


def verificar_login(user, pwd):
    conn = conectar_db()
    c = conn.cursor()
    c.execute("SELECT password, role FROM usuarios WHERE username=?", (user,))
    data = c.fetchone()
    conn.close()
    if data and pbkdf2_sha256.verify(pwd, data[0]):
        return data[1]
    return None


def criar_usuario(user, pwd, role):
    conn = conectar_db()
    try:
        senha_hash = pbkdf2_sha256.hash(pwd)
        c = conn.cursor()
        c.execute("INSERT INTO usuarios VALUES (?, ?, ?)", (user, senha_hash, role))
        conn.commit()
        return True
    except:
        return False
    finally:
        conn.close()


def listar_usuarios():
    conn = conectar_db()
    df = pd.read_sql_query("SELECT username, role FROM usuarios", conn)
    conn.close()
    return df


def excluir_usuario(username):
    conn = conectar_db()
    c = conn.cursor()
    c.execute("DELETE FROM usuarios WHERE username=?", (username,))
    conn.commit()
    conn.close()


# --- 3. INTELIGÊNCIA HÍBRIDA (SUPER ZOOM) ---


def pontuar_paginas(caminho_pdf, termos_busca):
    doc = fitz.open(caminho_pdf)
    ranking = []

    termos_principais = termos_busca.lower().split()
    termos_principais = [t for t in termos_principais if len(t) > 3]
    termos_tabela = [
        "r$",
        "franquia",
        "limite",
        "indenização",
        "prêmio",
        "%",
        "vidros",
        "cobertura",
        "tabela",
    ]

    for i, pagina in enumerate(doc):
        texto = pagina.get_text().lower()
        score = 0
        for termo in termos_principais:
            if termo in texto:
                score += 10
        if score > 0:
            for indic in termos_tabela:
                if indic in texto:
                    score += 5
        if score > 0:
            ranking.append((score, i))

    ranking.sort(key=lambda x: x[0], reverse=True)

    # Mantemos 3 páginas para não estourar a cota
    if not ranking:
        return [0, 1, 2]
    return [r[1] for r in ranking[:3]]


def chamar_gemini_vision(imagens_bytes, pergunta):
    try:
        # PROMPT HÍBRIDO: Ensina a IA a olhar texto E tabela
        conteudo = [
            f"Atue como um Analista Sênior de Seguros. O usuário perguntou: '{pergunta}'.",
            "Sua missão é encontrar a resposta nessas imagens de documentos.",
            "DIRETRIZES DE ANÁLISE:",
            "1. TEXTO: Se a resposta estiver em cláusulas ou definições de texto, cite o trecho e explique.",
            "2. TABELAS: Se a resposta for um valor numérico em tabela, seja preciso com a linha e coluna correta.",
            "3. CONTEXTO: Leia as notas de rodapé e as letras miúdas.",
            "4. Se a pergunta for sobre valores, priorize as tabelas. Se for conceitual, priorize o texto.",
        ]

        for num, img_data in imagens_bytes:
            conteudo.append(f"--- Página {num} ---")
            conteudo.append({"mime_type": "image/png", "data": img_data})

        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]

        response = model.generate_content(conteudo, safety_settings=safety_settings)
        return response.text
    except Exception as e:
        if "429" in str(e):
            return "⚠️ Muita carga no sistema. Aguarde 30s."
        return f"Erro técnico: {str(e)}"


def pdf_para_bytes_hd(caminho_pdf, lista_paginas):
    doc = fitz.open(caminho_pdf)
    imagens_bytes = []
    for num_pag in lista_paginas:
        if num_pag < len(doc) and num_pag >= 0:
            pagina = doc[num_pag]

            # AQUI MUDOU: Zoom de 2.0 para 3.0 (SUPER HD)
            # Isso deixa a letra pequena da tabela muito mais nítida para a IA
            pix = pagina.get_pixmap(matrix=fitz.Matrix(3.0, 3.0))

            imagens_bytes.append((num_pag + 1, pix.tobytes("png")))
    return imagens_bytes


def processar_pergunta(arquivos_selecionados, pergunta):
    contexto_final = ""
    for nome in arquivos_selecionados:
        caminho = os.path.join(PASTA_PDFS, nome)
        paginas_campeas = pontuar_paginas(caminho, pergunta)
        imagens = pdf_para_bytes_hd(caminho, paginas_campeas)
        texto_extraido = chamar_gemini_vision(imagens, pergunta)
        contexto_final += f"\n=== DOCUMENTO: {nome} ===\n{texto_extraido}\n"

    return contexto_final


def listar_arquivos_pasta():
    arquivos = [f for f in os.listdir(PASTA_PDFS) if f.lower().endswith(".pdf")]
    arquivos.sort(
        key=lambda x: os.path.getmtime(os.path.join(PASTA_PDFS, x)), reverse=True
    )
    return arquivos


# --- 4. INTERFACE ---
inicializar_banco()

if "autenticado" not in st.session_state:
    st.session_state.autenticado = False
    st.session_state.role = None
    st.session_state.usuario = None

if not st.session_state.autenticado:
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.title("🔐 Login Analista")
        with st.form("login"):
            u = st.text_input("Usuário")
            p = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar"):
                r = verificar_login(u, p)
                if r:
                    st.session_state.autenticado = True
                    st.session_state.role = r
                    st.session_state.usuario = u
                    st.rerun()
                else:
                    st.error("Acesso negado.")
else:
    arquivos = listar_arquivos_pasta()
    with st.sidebar:
        st.caption(f"Logado como: {st.session_state.usuario}")
        if st.button("Sair"):
            st.session_state.autenticado = False
            st.rerun()
        st.divider()
        arquivos_sel = st.multiselect(
            "Selecione os PDFs:",
            arquivos,
            default=arquivos[:2] if len(arquivos) >= 2 else arquivos,
        )

        if st.session_state.role == "admin":
            with st.expander("Painel Admin"):
                with st.form("c"):
                    nu = st.text_input("Novo User")
                    np = st.text_input("Senha", type="password")
                    nt = st.selectbox("Tipo", ["colaborador", "admin"])
                    if st.form_submit_button("Criar"):
                        criar_usuario(nu, np, nt)
                df = listar_usuarios()
                ex = st.selectbox("Excluir", df["username"])
                if st.button("Deletar"):
                    excluir_usuario(ex)
                    st.rerun()

    st.title("📊 Analista de Tabelas (Super HD)")
    st.caption("Qualidade de leitura aumentada para tabelas pequenas.")

    if not arquivos_sel:
        st.info("👈 Selecione um PDF.")
    else:
        if "messages" not in st.session_state:
            st.session_state.messages = []
        for m in st.session_state.messages:
            with st.chat_message(m["role"]):
                st.markdown(m["content"])

        if prompt := st.chat_input("Ex: Me mostre a tabela de franquias de vidros"):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                with st.spinner("🔍 Aplicando Zoom 3x e analisando tabelas..."):
                    try:
                        resposta_final = processar_pergunta(arquivos_sel, prompt)
                        st.markdown(resposta_final)
                        st.session_state.messages.append(
                            {"role": "assistant", "content": resposta_final}
                        )

                    except Exception as e:
                        st.error(f"Ocorreu um erro: {e}")
