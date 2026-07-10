#!/bin/bash

pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -U yt-dlp

# 运行Python程序
python main.py || true

# 保持容器运行
tail -f /dev/null 