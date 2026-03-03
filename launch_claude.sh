#!/bin/bash
set -e

# Wan2.2 Video Service - Claude Code 一键启动脚本
# 用途：使用打包的 Claude Code 二进制文件启动项目

echo "=========================================="
echo "Wan2.2 Video Service - Claude Code Launcher"
echo "=========================================="

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 获取当前目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_BIN="$SCRIPT_DIR/bin/claude"

echo -e "${GREEN}项目目录: $SCRIPT_DIR${NC}"

# 步骤 1: 检查打包的 Claude Code 二进制
echo ""
echo "=========================================="
echo "步骤 1: 检查 Claude Code 二进制"
echo "=========================================="

if [ -f "$CLAUDE_BIN" ]; then
    echo -e "${GREEN}✓ 找到打包的 Claude Code 二进制${NC}"
    CLAUDE_VERSION=$("$CLAUDE_BIN" --version 2>&1 | head -n 1 || echo "unknown")
    echo -e "${GREEN}✓ Claude Code 版本: ${CLAUDE_VERSION}${NC}"
    CLAUDE_EXEC="$CLAUDE_BIN"
elif command -v claude &> /dev/null; then
    echo -e "${YELLOW}未找到打包的二进制，使用系统安装的 Claude Code${NC}"
    CLAUDE_VERSION=$(claude --version 2>&1 | head -n 1 || echo "unknown")
    echo -e "${GREEN}✓ Claude Code 版本: ${CLAUDE_VERSION}${NC}"
    CLAUDE_EXEC="claude"
else
    echo -e "${RED}未找到 Claude Code${NC}"
    echo ""
    echo "请选择以下方式之一："
    echo "1. 从 GitHub 重新克隆完整项目（包含 bin/claude）"
    echo "2. 手动安装 Claude Code: curl -fsSL https://claude.ai/install.sh | sh"
    echo ""
    exit 1
fi

# 步骤 2: 检查 API Key
echo ""
echo "=========================================="
echo "步骤 2: 检查 Anthropic API Key"
echo "=========================================="

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo -e "${YELLOW}未检测到 ANTHROPIC_API_KEY 环境变量${NC}"
    echo ""
    echo "请设置您的 Anthropic API Key:"
    echo "  export ANTHROPIC_API_KEY='your-api-key-here'"
    echo ""
    echo "或者在启动 Claude Code 后手动配置"
    echo ""
    read -p "是否继续启动 Claude Code? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo -e "${GREEN}✓ ANTHROPIC_API_KEY 已设置${NC}"
fi

# 步骤 3: 检查项目配置
echo ""
echo "=========================================="
echo "步骤 3: 检查项目配置"
echo "=========================================="

if [ -d "$SCRIPT_DIR/.claude" ]; then
    echo -e "${GREEN}✓ 项目配置目录存在${NC}"

    if [ -f "$SCRIPT_DIR/.claude/memory/MEMORY.md" ]; then
        echo -e "${GREEN}✓ 项目记忆文件存在${NC}"
    else
        echo -e "${YELLOW}⚠ 项目记忆文件不存在${NC}"
    fi
else
    echo -e "${YELLOW}⚠ 项目配置目录不存在，将使用默认配置${NC}"
fi

# 步骤 4: 启动 Claude Code
echo ""
echo "=========================================="
echo "步骤 4: 启动 Claude Code"
echo "=========================================="

cd "$SCRIPT_DIR"

echo ""
echo -e "${GREEN}正在启动 Claude Code...${NC}"
echo ""
echo "提示："
echo "  - Claude Code 将在当前项目目录中启动"
echo "  - 项目配置位于 .claude/ 目录"
echo "  - 项目记忆位于 .claude/memory/MEMORY.md"
echo "  - 使用 Ctrl+C 退出"
echo ""

# 启动 Claude Code
exec "$CLAUDE_EXEC"

# 如果 exec 失败，显示错误信息
echo -e "${RED}启动 Claude Code 失败${NC}"
exit 1

