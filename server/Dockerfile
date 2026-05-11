FROM python:3.12-slim

ARG APP_VERSION=0.1.0
LABEL org.opencontainers.image.title="epaper-nas-display"
LABEL org.opencontainers.image.version="${APP_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_VERSION=${APP_VERSION}

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 15001
CMD ["python", "app.py"]
