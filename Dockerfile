# 使用官方 Python 镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 复制配置文件
RUN cp config/settings.yaml.example config/settings.yaml

# 暴露端口
EXPOSE 8000

# 启动服务
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
