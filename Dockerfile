FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg curl unzip && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip \
    -o /tmp/deno.zip && unzip /tmp/deno.zip -d /usr/local/bin && rm /tmp/deno.zip

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000
CMD ["gunicorn", "app:app", "--workers", "1", "--threads", "4", "--timeout", "600", "--bind", "0.0.0.0:5000"]
