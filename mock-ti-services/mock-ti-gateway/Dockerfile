FROM docker.io/library/python:3.12-slim
# pull in Debian security fixes published after the base image was built (Trivy gate in CD)
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn httpx "pyjwt[crypto]" cryptography
COPY gateway.py .
EXPOSE 8082
CMD ["uvicorn", "gateway:app", "--host", "0.0.0.0", "--port", "8082"]
