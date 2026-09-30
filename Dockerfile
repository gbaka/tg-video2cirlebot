# Dockerfile для Video to Circle Telegram Bot
# Использует многоэтапную сборку для уменьшения размера образа

# ===== Этап 1: Builder =====
FROM python:3.12-slim AS builder

# Устанавливаем системные зависимости для сборки
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Создаем виртуальное окружение
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Устанавливаем Python зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# ===== Этап 2: Runtime =====
FROM python:3.12-slim AS runtime

# Устанавливаем ffmpeg и минимальные системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Копируем виртуальное окружение из builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Создаем пользователя для безопасности
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Рабочая директория
WORKDIR /app

# Копируем код приложения
COPY --chown=appuser:appuser *.py *.yaml ./
COPY --chown=appuser:appuser locales/ ./locales/

# Создаем директорию для временных файлов
RUN mkdir -p /tmp/videobot && chown appuser:appuser /tmp/videobot
# Создаем директорию для SQLite базы
RUN mkdir -p /app/data && chown appuser:appuser /app/data
ENV TMPDIR=/tmp/videobot

# Переключаемся на непривилегированного пользователя
USER appuser

# Переменные окружения (могут быть переопределены в docker-compose)
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Точка входа
ENTRYPOINT ["python", "main.py"]