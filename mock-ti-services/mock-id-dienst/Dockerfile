FROM docker.io/library/python:3.12-slim
# pull in Debian security fixes published after the base image was built (Trivy gate in CD)
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
RUN pip install --no-cache-dir fastapi uvicorn "pyjwt[crypto]" cryptography
COPY main.py .
EXPOSE 8081
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]
