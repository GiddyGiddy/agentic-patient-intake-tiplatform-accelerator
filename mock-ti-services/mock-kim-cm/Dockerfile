FROM docker.io/library/python:3.12-slim
WORKDIR /srv
RUN pip install --no-cache-dir aiosmtpd
COPY smtp_server.py .
EXPOSE 2525
CMD ["python", "-u", "smtp_server.py"]
