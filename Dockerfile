# proj3-apply-gate 容器化产物
#
# ⚠️ 诚实说明：本机（Windows 10 19044 21H2）实测 **Docker 装不了** ——
#    WSL / VirtualMachinePlatform / Hyper-V 三个功能全部 Disabled，无法启用容器后端。
#    所以这个文件是**静态交付物**，从未在本机真实跑过容器。
#    README 的「已知限制」一节也写了这一点。面试被问到要直说，不要装。
#
# 在有 Docker 的机器上：
#     docker build -t proj3-apply-gate .
#     docker run --rm -v "$(pwd)/data:/app/data" --env-file .env proj3-apply-gate gate

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app

# 依赖单独一层，改代码不用重装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 密钥通过 --env-file 注入，绝不写进镜像层
RUN mkdir -p data/generated output

# 默认跑 L1 门禁（不花钱、不联网）——最能说明这套东西是「可验证的」
CMD ["python", "scripts/gate.py", "--json"]
