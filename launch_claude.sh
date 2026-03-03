#!/bin/bash
set -e

# Wan2.2 Video Service - Claude Code 一键启动脚本
# 用途：自动安装 Claude Code 并启动项目

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

echo -e "${GREEN}项目目录: $SCRIPT_DIR${NC}"

# 步骤 1: 检查 Claude Code 是否已安装
echo ""
echo "=========================================="
echo "步骤 1: 检查 Claude Code 安装状态"
echo "=========================================="

if command -v claude &> /dev/null; then
    CLAUDE_VERSION=$(claude --version 2>&1 | head -n 1 || echo "unknown")
    echo -e "${GREEN}✓ Claude Code 已安装: ${CLAUDE_VERSION}${NC}"
else
    echo -e "${YELLOW}Claude Code 未安装，正在安装...${NC}"

    # 检查操作系统
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        echo "检测到 Linux 系统"

        # 下载并安装 Claude Code
        echo "正在下载 Claude Code..."
        curl -fsSL https://claude.ai/install.sh | sh

        # 添加到 PATH
        if ! grep -q 'export PATH="$HOME/.claude/bin:$PATH"' ~/.bashrc; then
            echo 'export PATH="$HOME/.claude/bin:$PATH"' >> ~/.bashrc
        fi

        # 立即加载到当前会话
        export PATH="$HOME/.claude/bin:$PATH"

        echo -e "${GREEN}✓ Claude Code 安装完成${NC}"
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        echo "检测到 macOS 系统"

        # 检查 Homebrew
        if ! command -v brew &> /dev/null; then
            echo -e "${RED}请先安装 Homebrew: https://brew.sh${NC}"
            exit 1
        fi

        # 使用 Homebrew 安装
        brew install anthropics/claude/claude

        echo -e "${GREEN}✓ Claude Code 安装完成${NC}"
    else
        echo -e "${RED}不支持的操作系统: $OSTYPE${NC}"
        echo "请手动安装 Claude Code: https://docs.anthropic.com/claude/docs/claude-code"
        exit 1
    fi
fi

# 步骤 2: 验证 Claude Code 安装
echo ""
echo "=========================================="
echo "步骤 2: 验证 Claude Code"
echo "=========================================="

if ! command -v claude &> /dev/null; then
    echo -e "${RED}Claude Code 安装失败${NC}"
    echo "请手动安装: https://docs.anthropic.com/claude/docs/claude-code"
    exit 1
fi

CLAUDE_VERSION=$(claude --version 2>&1 | head -n 1 || echo "unknown")
echo -e "${GREEN}✓ Claude Code 版本: ${CLAUDE_VERSION}${NC}"

# 步骤 3: 检查 API Key
echo ""
echo "=========================================="
echo "步骤 3: 检查 Anthropic API Key"
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

# 步骤 4: 检查项目配置
echo ""
echo "=========================================="
echo "步骤 4: 检查项目配置"
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

# 步骤 5: 启动 Claude Code
echo ""
echo "=========================================="
echo "步骤 5: 启动 Claude Code"
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
exec claude

# 如果 exec 失败，显示错误信息
echo -e "${RED}启动 Claude Code 失败${NC}"
exit 1
