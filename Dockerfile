FROM shenxianmq/telegram_assistant_env:v1.0.1

# 设置工作目录
WORKDIR /app

# 复制项目文件
COPY main.py .
COPY init.py .
COPY entrypoint.sh .
COPY src/ src/

# 创建必要的目录
RUN mkdir -p \
    config \
    downloads/telegram/videos \
    downloads/telegram/audios \
    downloads/telegram/photos \
    downloads/telegram/others \
    downloads/youtube \
    temp/telegram \
    temp/youtube

# 设置脚本权限
RUN chmod +x entrypoint.sh

# 设置环境变量
ENV PYTHONUNBUFFERED=1

# 使用启动脚本
ENTRYPOINT ["./entrypoint.sh"] 