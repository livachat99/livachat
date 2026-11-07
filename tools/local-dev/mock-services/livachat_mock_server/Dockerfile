FROM python:3.12-slim

WORKDIR /app

# Install protocol package
COPY packages/sdk-python /app/packages/sdk-python
RUN pip install --no-cache-dir -e /app/packages/sdk-python

# Install mock server
COPY tools/local-dev/mock-services/livachat_mock_server /app/mock-server
RUN pip install --no-cache-dir -e /app/mock-server

EXPOSE 8080

ENV MOCK_HOST=0.0.0.0
ENV MOCK_PORT=8080

CMD ["uvicorn", "livachat_mock_server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
