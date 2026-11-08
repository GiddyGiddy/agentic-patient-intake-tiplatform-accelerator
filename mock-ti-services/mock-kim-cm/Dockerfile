FROM docker.io/library/python:3.12-slim
# pull in Debian security fixes published after the base image was built (Trivy gate in CD)
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
RUN pip install --no-cache-dir aiosmtpd
COPY smtp_server.py .
EXPOSE 2525
CMD ["python", "-u", "smtp_server.py"]
