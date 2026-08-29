FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/app/data/earnings.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY seed_times.json ./

RUN mkdir -p /app/data
VOLUME ["/app/data"]

# override with: docker run ... once  /  pairs  /  upcoming ...
ENTRYPOINT ["python", "-m", "bot.main"]
CMD ["run"]
