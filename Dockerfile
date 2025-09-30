# 使用官方 Python 3.9 slim 镜像
FROM python:3.9-slim

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 设置工作目录
WORKDIR /app

# 安装依赖
# 复制 requirements.txt 并安装，以利用 Docker 的层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
# 将当前目录内容复制到工作目录中
COPY . .

# 暴露端口
# 默认端口为 8000，与 uvicorn 命令中一致
EXPOSE 8001

# 启动服务的命令
# 使用 uvicorn 启动 FastAPI 应用
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]
