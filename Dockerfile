FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY openportfolio ./openportfolio
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["opp", "serve", "--host", "0.0.0.0", "--port", "8000"]
