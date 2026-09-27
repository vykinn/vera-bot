FROM python:3.12-slim
WORKDIR /app
COPY main.py /app/main.py
COPY app /app/app
COPY payload /app/payload
COPY tests /app/tests
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "main.py"]
