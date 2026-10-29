FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY samatva ./samatva
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["samatva", "serve", "--host", "0.0.0.0", "--port", "8000"]
