# 使用官方 Python 3.9 slim 作为基础镜像
FROM python:3.9-slim

# 设置环境变量，阻止 Python 生成 .pyc 文件并确保输出直接打印
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 设置工作目录
WORKDIR /app

# 创建一个专门用于运行应用的非 root 用户和用户组
RUN addgroup --system app && adduser --system --ingroup app app

# 复制依赖文件并安装
# 先复制 requirements.txt 以更好地利用 Docker 的层缓存
COPY requirements.txt .
# 更新 pip 并安装依赖，--no-cache-dir 减少镜像体积
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

# 复制项目源代码和配置文件到镜像中
# 只复制需要的'src'目录以及配置文件，而不是整个工作区
COPY --chown=app:app src/ ./src
COPY --chown=app:app config/ ./config
COPY --chown=app:app templates/ ./templates

# 切换到非 root 用户
USER app

# 暴露服务端口 (修正注释)
EXPOSE 8001

# 启动服务的命令 (端口与 EXPOSE 一致)
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]
