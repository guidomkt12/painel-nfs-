FROM python:3.12-slim
WORKDIR /app
COPY server.py .
COPY public ./public
RUN mkdir /data && chown 10001:10001 /data
USER 10001:10001
ENV PORT=8080 DB_PATH=/data/keile.sqlite3 PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8080
CMD ["python","server.py"]
