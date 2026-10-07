FROM python:3.12-slim

WORKDIR /opt/job-copilot

# requirements 中全部为纯轮子包（faiss-cpu/pypdf 等），无需编译工具链
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/resources ./resources

# 单 worker：进程内持有 FAISS/BM25 缓存与单例服务（扩容前置条件见 deploy/部署指南.md）
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-keep-alive", "65"]
