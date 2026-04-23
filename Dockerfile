FROM python:3.11-slim

# System deps: ffmpeg para merge/convert, curl+unzip para Deno,
# procps para pkill (cancelar ffmpeg no Linux)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    unzip \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Instala Deno (essencial para o JS challenge do YouTube desde nov/2025)
ENV DENO_INSTALL=/root/.deno
RUN curl -fsSL https://deno.land/install.sh | sh
ENV PATH="/root/.deno/bin:$PATH"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py youtube_downloader.py ./

# config.toml é baked na imagem; secrets.toml é montado como volume (nunca commitado)
RUN mkdir -p .streamlit downloads
COPY .streamlit/config.toml .streamlit/config.toml

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py"]
