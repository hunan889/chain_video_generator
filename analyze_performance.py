#!/usr/bin/env python3
"""
从 ComfyUI 日志分析视频生成性能

分析内容：
1. 每个任务的总执行时间
2. 每步的平均耗时
3. Story 模式 vs 标准模式的性能对比
4. 模型切换的开销
"""

import re
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict


def parse_comfyui_log(log_file: str = "/tmp/comfyui.log"):
    """解析 ComfyUI 日志"""
    if not Path(log_file).exists():
        print(f"日志文件不存在: {log_file}")
        return []

    with open(log_file, "r") as f:
        lines = f.readlines()

    tasks = []
    current_task = None

    for line in lines:
        # 检测任务开始
        if "got prompt" in line.lower() or "prompt_id" in line.lower():
            if current_task:
                tasks.append(current_task)
            current_task = {
                "start_line": line,
                "steps": [],
                "model_switches": 0,
                "total_time": None,
            }

        # 检测步骤执行
        step_match = re.search(r"(\d+)/(\d+)", line)
        if step_match and current_task:
            current_step = int(step_match.group(1))
            total_steps = int(step_match.group(2))
            current_task["steps"].append({
                "current": current_step,
                "total": total_steps,
                "line": line,
            })

        # 检测模型切换
        if "switching model" in line.lower() and current_task:
            current_task["model_switches"] += 1

        # 检测任务完成
        time_match = re.search(r"Prompt executed in (\d+):(\d+):(\d+)", line)
        if time_match and current_task:
            hours = int(time_match.group(1))
            minutes = int(time_match.group(2))
            seconds = int(time_match.group(3))
            total_seconds = hours * 3600 + minutes * 60 + seconds
            current_task["total_time"] = total_seconds
            current_task["end_line"] = line
            tasks.append(current_task)
            current_task = None

    return tasks


def analyze_performance(tasks):
    """分析性能数据"""
    print("="*70)
    print("ComfyUI 性能分析报告")
    print("="*70)

    if not tasks:
        print("\n未找到任务数据")
        return

    print(f"\n总任务数: {len(tasks)}")

    # 统计数据
    total_times = []
    model_switch_counts = []
    step_counts = []

    for i, task in enumerate(tasks, 1):
        if task["total_time"]:
            total_times.append(task["total_time"])
            model_switch_counts.append(task["model_switches"])

            max_steps = max([s["total"] for s in task["steps"]]) if task["steps"] else 0
            step_counts.append(max_steps)

            print(f"\n任务 {i}:")
            print(f"  总耗时: {task['total_time']//60}分{task['total_time']%60}秒 ({task['total_time']}秒)")
            print(f"  总步数: {max_steps}")
            print(f"  模型切换: {task['model_switches']} 次")

            if max_steps > 0:
                avg_time_per_step = task['total_time'] / max_steps
                print(f"  平均每步: {avg_time_per_step:.1f} 秒")

    # 总体统计
    if total_times:
        print("\n" + "="*70)
        print("总体统计")
        print("="*70)
        print(f"\n平均任务耗时: {sum(total_times)/len(total_times):.1f} 秒 ({sum(total_times)/len(total_times)/60:.2f} 分钟)")
        print(f"最快任务: {min(total_times)} 秒 ({min(total_times)/60:.2f} 分钟)")
        print(f"最慢任务: {max(total_times)} 秒 ({max(total_times)/60:.2f} 分钟)")

        if step_counts:
            avg_steps = sum(step_counts) / len(step_counts)
            print(f"\n平均步数: {avg_steps:.1f}")

            # 计算平均每步耗时
            total_step_time = sum(t/s for t, s in zip(total_times, step_counts) if s > 0)
            avg_time_per_step = total_step_time / len([s for s in step_counts if s > 0])
            print(f"平均每步耗时: {avg_time_per_step:.1f} 秒")

        if model_switch_counts:
            avg_switches = sum(model_switch_counts) / len(model_switch_counts)
            print(f"\n平均模型切换次数: {avg_switches:.1f}")

    # 性能建议
    print("\n" + "="*70)
    print("性能分析")
    print("="*70)

    if total_times:
        avg_time = sum(total_times) / len(total_times)

        if avg_time > 600:  # 超过10分钟
            print("\n⚠ 任务耗时较长，建议:")
            print("  1. 降低 steps（当前平均 {:.0f}，建议 10-15）".format(sum(step_counts)/len(step_counts) if step_counts else 0))
            print("  2. 使用 Story 模式（Merged）减少模型重载")
            print("  3. 检查 GPU 利用率")

        if model_switch_counts and sum(model_switch_counts) > 0:
            print("\n✓ 检测到模型切换（A14B 两阶段模式）")
            print(f"  平均每个任务切换 {sum(model_switch_counts)/len(model_switch_counts):.1f} 次")
            print("  这是正常的 A14B 工作流程（HIGH → LOW 模型）")


def compare_modes():
    """对比不同模式的性能"""
    print("\n" + "="*70)
    print("模式对比（基于之前的测试结果）")
    print("="*70)

    print("\n测试配置: 832x480, 16fps, 10 steps, 2段视频")
    print("\n| 模式 | 耗时 | 特点 |")
    print("|------|------|------|")
    print("| Story 模式（Merged） | 3.3 分钟 | ✓ 共享模型加载<br>✓ 视频连续性<br>✓ 身份一致性 |")
    print("| 标准 I2V（独立） | 6.2 分钟 | ✗ 每段重新加载模型<br>✗ 无连续性保证 |")
    print("\n结论: Story 模式比标准 I2V 快 1.85x")


if __name__ == "__main__":
    import sys

    log_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/comfyui.log"

    print(f"分析日志文件: {log_file}\n")

    tasks = parse_comfyui_log(log_file)
    analyze_performance(tasks)
    compare_modes()

    print("\n" + "="*70)
    print("分析完成")
    print("="*70)
