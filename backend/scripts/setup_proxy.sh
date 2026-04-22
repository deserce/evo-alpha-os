#!/bin/bash
#
# 数据采集代理设置脚本
# 使用方法: source scripts/setup_proxy.sh
#

echo "设置代理环境..."

# 设置代理
export HTTP_PROXY=http://127.0.0.1:7897
export HTTPS_PROXY=http://127.0.0.1:7897
export ALL_PROXY=http://127.0.0.1:7897

# 设置 AKShare 代理
export AKSHARE_PROXY=http://127.0.0.1:7897

echo "✅ 代理已设置: http://127.0.0.1:7897"
echo ""
echo "测试连接..."
curl -s -x http://127.0.0.1:7897 -I https://www.baidu.com | head -1
echo ""
echo "现在可以运行数据采集了！"
