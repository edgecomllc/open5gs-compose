# Dockerfile
FROM python:3.11-slim

LABEL maintainer="IMS Developer"
LABEL description="Simple IMS SIP Registrar based on Twisted"

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем зависимости и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY ims_registrar.py .

# Создаем пользователя для запуска (без прав root)
RUN useradd -m -u 1000 imsuser && chown -R imsuser:imsuser /app
USER imsuser

# Открываем порты
EXPOSE 5060/udp 5060/tcp 5061/tcp

# Запускаем сервер
CMD ["python", "-u", "ims_registrar.py"]
