FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m appuser && \
    chown -R appuser:appuser /app

EXPOSE 5000

USER appuser

ENV PYTHONUNBUFFERED=1

CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:create_app_for_gunicorn()"]
