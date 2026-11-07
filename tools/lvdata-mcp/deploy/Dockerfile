# 运行期需要访问 /opt/lvdata 数据根：
#   docker run -d --name lvdata-mcp \
#     -v /opt/lvdata:/opt/lvdata \
#     -e DASHSCOPE_API_KEY=sk-xxx \
#     -e DASHSCOPE_API_HOST=llm-xxxx.cn-beijing.maas.aliyuncs.com \
#     -e DASHSCOPE_WORKSPACE_ID=llm-xxxx \
#     -p 8090:8090 lvdata-mcp
FROM python:3.12-slim

WORKDIR /opt/lvdata-mcp
COPY pyproject.toml ./
COPY src ./src
COPY configs ./configs
RUN pip install --no-cache-dir .

# v2 音频分析仍需要 ffmpeg（ogg→wav 转码，§6.3.2）
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

EXPOSE 8090
# v2：配置主体为 lvdata_mcp.yaml（§4.1）；DASHSCOPE_API_KEY / DASHSCOPE_API_HOST /
# DASHSCOPE_WORKSPACE_ID 经 ${ENV_VAR} 占位符解析
ENTRYPOINT ["lvdata-mcp", "serve", "--config", "/opt/lvdata-mcp/configs/dev/lvdata_mcp.yaml"]
