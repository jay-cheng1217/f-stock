FROM python:3.11

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirement.txt

COPY . .

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8001"]