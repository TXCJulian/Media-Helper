FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .     

EXPOSE 3332

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3332"]