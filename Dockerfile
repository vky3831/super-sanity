FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
EXPOSE 8502
CMD ["streamlit", "run", "main.py", "--server.port", "8502", "--server.address", "0.0.0.0"]