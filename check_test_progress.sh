#!/bin/bash
# 检查对比测试进度

echo "=== 对比测试进度 ==="
echo ""

# 检查测试是否在运行
if pgrep -f "run_comparison_test.py" > /dev/null; then
    echo "✓ 测试正在运行中..."
    echo ""

    # 显示最后 30 行输出
    echo "最新输出:"
    echo "----------------------------------------"
    tail -30 /tmp/claude-0/-home-gime-soft/tasks/b94qbhnqw.output 2>/dev/null || echo "无法读取输出文件"
else
    echo "✗ 测试未运行"
    echo ""

    # 检查是否有结果文件
    if [ -f "comparison_results.json" ]; then
        echo "✓ 测试已完成，结果文件:"
        echo "  - comparison_results.json"
        echo "  - comparison_report.txt"
        echo ""
        echo "查看报告:"
        echo "  cat comparison_report.txt"
    fi
fi
